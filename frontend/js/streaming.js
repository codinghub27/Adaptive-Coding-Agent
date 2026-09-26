/**
 * Server-sent-event reader for one teaching turn.
 *
 * Talks to `POST /chat/stream`, which emits:
 *   event: stage  data: {node, label}          — once per graph node entered
 *   event: done   data: <the full ChatResponse> — exactly once, terminal
 *   event: error  data: {detail}                — terminal, on failure
 *
 * and translates it into the event contract the workspace already consumes:
 *   {type:"start",         data:{steps}}
 *   {type:"step",          data:{index, name, status, detail}}
 *   {type:"content_start", data:{concept}}
 *   {type:"token",         data:{content, fullContent}}
 *   {type:"final",         data:{message}}
 *
 * The backend does not stream the answer token by token — `final_response` is
 * a single synchronous node — so the finished text arrives whole in the `done`
 * frame and is typed out here. The working steps, however, are genuinely live:
 * each one is a real graph node that has actually started.
 */

import { ApiError, authFetch } from "./api.js";

const TYPEWRITER_MAX_MS = 26;
const TYPEWRITER_BASE_MS = 8;
/** Never spend longer than this typing out a long answer. */
const TYPEWRITER_BUDGET_MS = 2600;

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

/** Concept-card label per route. The title always comes from server data. */
const CONCEPT_LABELS = {
  dsa: "Pattern in focus",
  debug: "Root cause",
  explain: "Mental model",
  clarify: "Quick clarification",
};

function humanizeTopic(topic) {
  if (!topic) return null;
  const words = String(topic).replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : null;
}

function buildConcept(payload) {
  const route = payload.route || "clarify";
  const title = humanizeTopic(payload.plan?.topic);
  if (!title) return null;
  return { label: CONCEPT_LABELS[route] || "In focus", title };
}

/**
 * The hint ladder as the SERVER reports it.
 *
 * `hint_level` and `hint_ceiling` are 0-indexed rungs (L0..L6); the card shows
 * them 1-indexed. There is no client-side level: climbing the ladder means
 * sending another turn, which is what makes the ceiling unforgeable.
 */
function buildHint(generated) {
  if (!generated || generated.hint_level === null || generated.hint_level === undefined) return null;
  const section = (generated.sections || []).find((item) => item.kind === "next_hint");
  if (!section) return null;
  return {
    level: generated.hint_level + 1,
    total: (generated.hint_ceiling ?? generated.hint_level) + 1,
    text: section.body,
    moreHelpAvailable: Boolean(generated.more_help_available),
    revealsCode: Boolean(generated.reveals_code),
  };
}

/**
 * The answer body, minus whatever the hint card already shows.
 *
 * The server assembles `generated.text` from every section including
 * `next_hint`; rendering that text as-is under a hint card repeats the hint
 * verbatim. When a hint card is present the body is rebuilt from the other
 * sections instead — the same content, each part shown exactly once.
 */
function answerBody(generated, hasHintCard) {
  const sections = generated?.sections || [];
  if (!hasHintCard || !sections.length) return generated?.text || "";
  const rest = sections.filter((section) => section.kind !== "next_hint");
  if (!rest.length) return "";
  return rest.map((section) => `## ${section.title}\n\n${section.body}`).join("\n\n");
}

/** Turn a finished `ChatResponse` into the workspace's message shape. */
function buildMessage(payload, steps) {
  const generated = payload.generated;
  const hint = buildHint(generated);
  return {
    id: `msg_${crypto.randomUUID()}`,
    role: "agent",
    createdAt: new Date().toISOString(),
    content: answerBody(generated, Boolean(hint)) || payload.response,
    concept: buildConcept(payload),
    work: steps.map((step) => ({ ...step, status: "completed" })),
    hint,
    question: null,
    nextSteps: generated?.next_steps || [],
    citations: generated?.citations || [],
    revealsCode: Boolean(generated?.reveals_code),
    assistanceLevel: generated?.assistance_level || null,
    route: payload.route,
    topic: payload.plan?.topic || null,
    conversationId: payload.conversation_id,
    adapted: true,
  };
}

/** Split a raw SSE buffer into complete frames, returning the remainder. */
function takeFrames(buffer) {
  const frames = [];
  let rest = buffer;
  let boundary = rest.indexOf("\n\n");
  while (boundary !== -1) {
    frames.push(rest.slice(0, boundary));
    rest = rest.slice(boundary + 2);
    boundary = rest.indexOf("\n\n");
  }
  return { frames, rest };
}

function parseFrame(frame) {
  let event = "message";
  const data = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).trim());
  }
  return { event, data: data.join("\n") };
}

function buildBody({ conversationId, content, attachmentFile, assistanceCap, topic }) {
  const form = new FormData();
  if (content) form.append("text", content);
  if (conversationId) form.append("conversation_id", conversationId);
  if (attachmentFile) form.append("image", attachmentFile, attachmentFile.name || "upload.png");
  if (assistanceCap) form.append("assistance_cap", assistanceCap);
  if (topic) form.append("topic", topic);
  return form;
}

async function typeOut(text, onEvent) {
  const tokens = String(text).split(/(\s+)/);
  const perToken = Math.min(TYPEWRITER_MAX_MS, Math.max(1, Math.floor(TYPEWRITER_BUDGET_MS / Math.max(1, tokens.length))));
  let streamed = "";
  for (const token of tokens) {
    streamed += token;
    onEvent?.({ type: "token", data: { content: token, fullContent: streamed } });
    if (perToken > 0) await wait(Math.min(perToken, TYPEWRITER_BASE_MS + token.length));
  }
}

/**
 * Run one streamed turn. Resolves with the finished message, or throws —
 * a mid-stream 401 is handled by `authFetch` (refresh + one retry).
 */
export async function streamChat({ conversationId, content, attachmentFile = null, assistanceCap = null, topic = null, onEvent } = {}) {
  const response = await authFetch("/chat/stream", {
    method: "POST",
    body: buildBody({ conversationId, content, attachmentFile, assistanceCap, topic }),
  });

  if (!response.ok || !response.body) {
    throw new ApiError("The agent could not be reached.", response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  const steps = [];
  let buffer = "";
  let finalPayload = null;
  let failure = null;

  onEvent?.({ type: "start", data: { steps: [] } });

  const handleStage = (stage) => {
    const previous = steps.length - 1;
    if (previous >= 0) {
      steps[previous].status = "completed";
      onEvent?.({ type: "step", data: { index: previous, name: steps[previous].name, status: "completed" } });
    }
    steps.push({ name: stage.label, status: "running" });
    onEvent?.({ type: "step", data: { index: steps.length - 1, name: stage.label, status: "running" } });
  };

  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const { frames, rest } = takeFrames(buffer);
      buffer = rest;
      for (const raw of frames) {
        const { event, data } = parseFrame(raw);
        if (!data) continue;
        if (event === "stage") handleStage(JSON.parse(data));
        else if (event === "done") finalPayload = JSON.parse(data);
        else if (event === "error") failure = JSON.parse(data)?.detail || "The request could not be completed.";
      }
    }
  } finally {
    reader.releaseLock();
  }

  if (failure) throw new ApiError(failure, 500);
  if (!finalPayload) throw new ApiError("The agent stopped responding.", 502);

  // Every node that ran has now finished.
  steps.forEach((step, index) => {
    if (step.status !== "completed") {
      step.status = "completed";
      onEvent?.({ type: "step", data: { index, name: step.name, status: "completed" } });
    }
  });

  const message = buildMessage(finalPayload, steps);
  onEvent?.({ type: "content_start", data: { concept: message.concept } });
  await typeOut(message.content, onEvent);
  onEvent?.({ type: "final", data: { message } });
  return message;
}
