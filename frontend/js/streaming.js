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
 * verbatim. The body is rebuilt from the sections instead (exactly how the
 * server joins them, `app/response/generate.py`), skipping `next_hint` when a
 * hint card shows it — the same content, each part shown exactly once.
 */
function answerBody(generated, hasHintCard, hasQuestionCard = false) {
  const sections = generated?.sections || [];
  if (!sections.length) return generated?.text || "";
  const rest = sections.filter(
    (section) =>
      !(hasHintCard && section.kind === "next_hint") &&
      !(hasQuestionCard && section.kind === "check_question"),
  );
  if (!rest.length) return "";
  // Presentation order only: "Next steps" closes the answer instead of sitting
  // between the hint and the explanation. Same sections, same text.
  // ...and the agent's question to the learner ("Your turn") comes last of all.
  const last = (section) => (section.kind === "check_question" ? 2 : section.kind === "next_steps" ? 1 : 0);
  const ordered = [...rest].sort((a, b) => last(a) - last(b));
  return ordered.map((section) => `## ${section.title}\n\n${section.body}`).join("\n\n");
}

/**
 * The corpus sources this answer actually drew on (server-computed: only what
 * the prompt contained and the model reported using). Previously sent but
 * never shown.
 */
function withReferences(body, citations) {
  if (!citations || !citations.length) return body;
  const list = citations.map((label) => `- ${label}`).join("\n");
  return `${body}\n\n## References\n\n${list}`;
}

/** Turn a finished `ChatResponse` into the workspace's message shape. */
/**
 * The agent's pending closed-choice question (tutoring), as option buttons.
 * Clicking one sends that option as the learner's reply; the server grades it.
 */
function buildQuestion(payload) {
  const pending = payload.tutoring?.pending;
  if (!pending || pending.kind !== "question" || !(pending.options || []).length) return null;
  return { prompt: pending.question, options: pending.options };
}

function buildMessage(payload, steps) {
  const generated = payload.generated;
  const hint = buildHint(generated);
  const question = buildQuestion(payload);
  return {
    id: `msg_${crypto.randomUUID()}`,
    role: "agent",
    createdAt: new Date().toISOString(),
    content: withReferences(answerBody(generated, Boolean(hint), Boolean(question)) || payload.response, generated?.citations),
    concept: buildConcept(payload),
    work: steps.map(({ name, node, durationMs }) => ({ name, node, durationMs, status: "completed" })),
    hint,
    question,
    nextSteps: generated?.next_steps || [],
    citations: generated?.citations || [],
    revealsCode: Boolean(generated?.reveals_code),
    assistanceLevel: generated?.assistance_level || null,
    route: payload.route,
    topic: payload.plan?.topic || null,
    conversationId: payload.conversation_id,
    // Computed by the server; the UI only renders it (see app/graph/api.py).
    skillDeltas: payload.skill_deltas || {},
    // Server-computed (app/graph/api.py): true only when profile evidence
    // actually changed the plan. Never assumed by the client.
    adapted: Boolean(payload.adapted),
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

function buildBody({ conversationId, content, attachmentFile, assistanceCap, teachingMode, topic }) {
  const form = new FormData();
  if (content) form.append("text", content);
  if (conversationId) form.append("conversation_id", conversationId);
  if (attachmentFile) form.append("image", attachmentFile, attachmentFile.name || "upload.png");
  if (assistanceCap) form.append("assistance_cap", assistanceCap);
  if (teachingMode) form.append("teaching_mode", teachingMode);
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
export async function streamChat({ conversationId, content, attachmentFile = null, assistanceCap = null, teachingMode = null, topic = null, onEvent } = {}) {
  const response = await authFetch("/chat/stream", {
    method: "POST",
    body: buildBody({ conversationId, content, attachmentFile, assistanceCap, teachingMode, topic }),
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

  // A `stage` frame is emitted when a graph node FINISHES (LangGraph
  // "updates" mode), so each frame is a completed step. Its duration is the
  // gap since the previous frame (or since the stream opened), measured here.
  let mark = performance.now();
  const handleStage = (stage) => {
    const now = performance.now();
    const step = { name: stage.label, node: stage.node, status: "completed", durationMs: now - mark };
    mark = now;
    steps.push(step);
    onEvent?.({ type: "step", data: { index: steps.length - 1, ...step } });
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


  const message = buildMessage(finalPayload, steps);
  onEvent?.({ type: "content_start", data: { concept: message.concept } });
  await typeOut(message.content, onEvent);
  onEvent?.({ type: "final", data: { message } });
  return message;
}
