import { icon, sectionStyle, stageIcon } from "./icons.js";

const escapeHtml = (value = "") =>
  String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[character]);

/* ------------------------------------------------------------------------ */
/* Code blocks                                                               */
/* ------------------------------------------------------------------------ */

const PY_KEYWORDS = "def|return|if|else|elif|for|while|in|not|and|or|is|None|True|False|class|import|from|as|with|try|except|finally|raise|yield|lambda|pass|break|continue|global|nonlocal|assert|del|async|await";
const TOKEN_RE = new RegExp(
  [
    "(#[^\\n]*)", // 1 comment
    "(\"(?:\\\\.|[^\"\\\\\\n])*\"|'(?:\\\\.|[^'\\\\\\n])*')", // 2 string
    `\\b(${PY_KEYWORDS})\\b`, // 3 keyword
    "\\b(\\d+(?:\\.\\d+)?)\\b", // 4 number
    "\\b([A-Za-z_]\\w*)(?=\\()", // 5 call
  ].join("|"),
  "g",
);
const TOKEN_CLASSES = [null, "tok-comment", "tok-str", "tok-key", "tok-num", "tok-fn"];

/** Single-pass highlighter: each token is escaped and wrapped exactly once. */
function highlight(code) {
  let out = "";
  let last = 0;
  for (const match of code.matchAll(TOKEN_RE)) {
    out += escapeHtml(code.slice(last, match.index));
    const group = match.findIndex((value, index) => index > 0 && value !== undefined);
    out += `<span class="${TOKEN_CLASSES[group]}">${escapeHtml(match[0])}</span>`;
    last = match.index + match[0].length;
  }
  return out + escapeHtml(code.slice(last));
}

function codeBlock(code, language = "code") {
  return `<div class="code-block"><div class="code-head">${icon("code")}<span>${escapeHtml(language)}</span><button class="copy-code" aria-label="Copy code">${icon("copy")}<b>Copy</b></button></div><pre><code>${highlight(code.replace(/^\n+|\s+$/g, ""))}</code></pre></div>`;
}

/* ------------------------------------------------------------------------ */
/* Markdown → structured response                                            */
/* ------------------------------------------------------------------------ */

// Decorative emoji the model sometimes writes (📚 🎯 🚀 …). The UI supplies its
// own consistent symbols, so these are removed; plain ✓ ✗ → are kept.
const EMOJI_RE = /(?:[\u{1F000}-\u{1FAFF}☀-✒✔-✖✘-➿⭐⭕⌚⌛⏩-⏺⤴⤵〰〽㊗㊙]️?|‍|️)/gu;
const KEYCAP_RE = /(\d)️?⃣/gu;
const DONE_MARK_RE = /^(?:✅|✔️?|☑️?|✓)\s*/u;
const NOT_MARK_RE = /^(?:❌|✖️?|✗|✘)\s*/u;

// An emoji plus the single space after it, so "📚 Roadmap" becomes "Roadmap".
const EMOJI_GAP_RE = new RegExp(`${EMOJI_RE.source}[ \\t]?`, "gu");
const stripEmoji = (text) => text.replace(KEYCAP_RE, "$1.").replace(EMOJI_GAP_RE, "");

/** Inline formatting on ALREADY-ESCAPED text. */
function inline(text) {
  const spans = [];
  let out = text.replace(/`([^`]+)`/g, (_, code) => {
    spans.push(`<code>${code}</code>`);
    return `\u0001${spans.length - 1}\u0001`;
  });
  out = out
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/__(.+?)__/g, "<strong>$1</strong>")
    .replace(/(^|[^*\w])\*(?!\s)([^*\n]+?)\*(?!\w)/g, "$1<em>$2</em>")
    .replace(/(^|[^_\w])_(?!\s)([^_\n]+?)_(?!\w)/g, "$1<em>$2</em>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>')
    .replace(/ -- /g, " — ")
    .replace(/&lt;br\s*\/?&gt;/g, "<br>");
  return out.replace(/\u0001(\d+)\u0001/g, (_, index) => spans[Number(index)]);
}

const fmt = (line) => inline(escapeHtml(stripEmoji(line).trim()));

const HEADING_RE = /^(#{1,6})\s+(.*?)\s*#*\s*$/;
const LIST_RE = /^(\s*)([-*•+]|\d{1,3}[.)])\s+(.*)$/;
const HR_RE = /^\s*([-*_])(?:\s*\1){2,}\s*$/;
const TABLE_SEP_RE = /^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$/;

/**
 * Some answers arrive with a table or numbered list flattened onto one line
 * ("| a | b | |---|---| | 1 | 2 |", "1. Foo 2. Bar"). Restore the line breaks
 * so they render as the structure they were meant to be.
 */
function normalizeLines(markdown) {
  return markdown
    .split("\n")
    .flatMap((line) => {
      const trimmed = line.trim();
      if (trimmed.startsWith("|") && /\|\s*:?-{3,}/.test(trimmed) && /\|\s+\|/.test(trimmed)) {
        return trimmed.replace(/\|\s+\|/g, "|\n|").split("\n");
      }
      if (/^1[.)]\s/.test(trimmed) && /\s2[.)]\s+\S/.test(trimmed)) {
        return trimmed.split(/\s+(?=\d{1,2}[.)]\s+[^\d\s])/);
      }
      return [line];
    });
}

function splitRow(row) {
  return row.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((cell) => cell.trim());
}

function tableMarkup(rows) {
  const hasHeader = rows.length > 1 && TABLE_SEP_RE.test(rows[1].trim());
  const head = hasHeader ? splitRow(rows[0]) : null;
  const body = (hasHeader ? rows.slice(2) : rows).filter((row) => !TABLE_SEP_RE.test(row.trim()));
  const thead = head ? `<thead><tr>${head.map((cell) => `<th>${fmt(cell)}</th>`).join("")}</tr></thead>` : "";
  const tbody = body.map((row) => `<tr>${splitRow(row).map((cell) => `<td>${fmt(cell)}</td>`).join("")}</tr>`).join("");
  return `<div class="table-wrap"><table>${thead}<tbody>${tbody}</tbody></table></div>`;
}

function listItemMarkup(text) {
  let body = text;
  let cls = "";
  let mark = "";
  const task = body.match(/^\[( |x|X)\]\s+(.*)$/);
  if (task) {
    const done = task[1] !== " ";
    cls = done ? "task done" : "task";
    mark = `<span class="task-box">${done ? icon("check") : ""}</span>`;
    body = task[2];
  } else if (DONE_MARK_RE.test(body)) {
    cls = "task done";
    mark = `<span class="task-box">${icon("check")}</span>`;
    body = body.replace(DONE_MARK_RE, "");
  } else if (NOT_MARK_RE.test(body)) {
    cls = "task no";
    mark = `<span class="task-box">${icon("xCircle")}</span>`;
    body = body.replace(NOT_MARK_RE, "");
  }
  return `<li${cls ? ` class="${cls}"` : ""}>${mark}<span>${fmt(body)}</span>`;
}

/** Nested lists from indentation; ordered vs. bulleted per level. */
function listMarkup(items) {
  let html = "";
  const stack = [];
  for (const item of items) {
    const ordered = /\d/.test(item.marker);
    while (stack.length && item.indent < stack.at(-1).indent) {
      html += `</li></${stack.pop().tag}>`;
    }
    const top = stack.at(-1);
    if (!top || item.indent > top.indent) {
      const tag = ordered ? "ol" : "ul";
      const start = ordered && parseInt(item.marker, 10) > 1 ? ` start="${parseInt(item.marker, 10)}"` : "";
      stack.push({ indent: item.indent, tag });
      html += `<${tag}${start}>`;
    } else {
      html += "</li>";
    }
    html += listItemMarkup(item.text);
  }
  while (stack.length) html += `</li></${stack.pop().tag}>`;
  return html;
}

// `render_verdict` (app/response/format.py) opens with one of these fixed
// phrases, so the badge is a direct reading of the sandbox verdict — never a
// guess from free text.
const VERDICT_PREFIXES = [
  ["The sandbox confirmed this passes", "pass", "checkCircle", "Passed in sandbox"],
  ["The sandbox still found a failure", "fail", "xCircle", "Failing in sandbox"],
  ["Correctness could not be checked", "unverified", "info", "Not verified"],
];

function paragraphMarkup(lines) {
  const raw = lines.join("\n").trim();
  if (!raw) return "";
  // A paragraph that is entirely italic is a server-side caveat ("Not verified
  // by running your code …"); give it the note treatment.
  const note = raw.match(/^_([^_][\s\S]*?)_$/) || raw.match(/^\*([^*][\s\S]*?)\*$/);
  if (note) {
    return `<div class="callout note">${icon("info")}<p>${fmt(note[1]).replace(/\n/g, "<br>")}</p></div>`;
  }
  const verdict = VERDICT_PREFIXES.find(([prefix]) => raw.startsWith(prefix));
  const badge = verdict ? `<span class="verdict ${verdict[1]}">${icon(verdict[2])}${verdict[3]}</span>` : "";
  return `${badge}<p>${lines.map(fmt).join("<br>")}</p>`;
}

function sectionOpen(title) {
  const clean = stripEmoji(title).replace(/^(\d+)\.\s*/, "").trim();
  const number = stripEmoji(title).match(/^(\d+)\.\s*/);
  const { name, tone } = sectionStyle(clean);
  const marker = number ? `<span class="resp-num">${number[1]}</span>` : `<span class="resp-icon">${icon(name)}</span>`;
  return `<section class="resp-section${tone ? ` tone-${tone}` : ""}"><header class="resp-head">${marker}<h3>${inline(escapeHtml(clean))}</h3></header><div class="resp-body">`;
}

export function renderMarkdown(markdown = "") {
  const blocks = [];
  let source = String(markdown).replace(/\r\n?/g, "\n");
  source = source.replace(/```([\w+#.-]*)[^\S\n]*\n([\s\S]*?)```/g, (_, language, code) => {
    blocks.push(codeBlock(code, language || "code"));
    return `\n\u0000${blocks.length - 1}\u0000\n`;
  });
  // An unclosed fence (mid-stream) renders as the code it will become.
  const open = source.indexOf("```");
  if (open !== -1) {
    const rest = source.slice(open + 3);
    const newline = rest.indexOf("\n");
    const language = newline === -1 ? rest : rest.slice(0, newline);
    blocks.push(codeBlock(newline === -1 ? "" : rest.slice(newline + 1), language.trim() || "code"));
    source = `${source.slice(0, open)}\n\u0000${blocks.length - 1}\u0000\n`;
  }

  const lines = normalizeLines(source);
  let html = "";
  let inSection = false;
  let paragraph = [];
  const flush = () => {
    html += paragraphMarkup(paragraph);
    paragraph = [];
  };

  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) { flush(); continue; }

    const code = trimmed.match(/^\u0000(\d+)\u0000$/);
    if (code) { flush(); html += blocks[Number(code[1])]; continue; }

    const heading = trimmed.match(HEADING_RE);
    if (heading) {
      flush();
      // A new top-level section opens for the server's own section titles
      // (SECTION_TITLES); headings the model writes inside a section (e.g. an
      // explanation's "## Weekly Breakdown") stay sub-headings of it.
      const known = sectionStyle(stripEmoji(heading[2]).trim()).name !== "dot";
      if (heading[1].length <= 2 && (!inSection || known)) {
        if (inSection) html += "</div></section>";
        html += sectionOpen(heading[2]);
        inSection = true;
      } else {
        html += `<h4>${fmt(heading[2])}</h4>`;
      }
      continue;
    }

    if (HR_RE.test(trimmed)) { flush(); html += "<hr>"; continue; }

    if (trimmed.startsWith("|")) {
      flush();
      const rows = [];
      while (index < lines.length && lines[index].trim().startsWith("|")) rows.push(lines[index++]);
      index -= 1;
      html += tableMarkup(rows);
      continue;
    }

    if (trimmed.startsWith(">")) {
      flush();
      const quote = [];
      while (index < lines.length && lines[index].trim().startsWith(">")) quote.push(lines[index++].trim().replace(/^>\s?/, ""));
      index -= 1;
      html += `<blockquote>${quote.map(fmt).join("<br>")}</blockquote>`;
      continue;
    }

    if (LIST_RE.test(line)) {
      flush();
      const items = [];
      while (index < lines.length) {
        const match = lines[index].match(LIST_RE);
        if (match) {
          items.push({ indent: match[1].replace(/\t/g, "  ").length, marker: match[2], text: match[3] });
        } else if (lines[index].trim() && /^\s{2,}/.test(lines[index]) && items.length) {
          items.at(-1).text += ` ${lines[index].trim()}`; // wrapped continuation
        } else {
          break;
        }
        index += 1;
      }
      index -= 1;
      html += listMarkup(items);
      continue;
    }

    paragraph.push(trimmed);
  }
  flush();
  if (inSection) html += "</div></section>";
  return html;
}

/* ------------------------------------------------------------------------ */
/* Working process                                                           */
/* ------------------------------------------------------------------------ */

const SPINNER = '<span class="spinner" aria-hidden="true"></span>';

function formatDuration(ms) {
  if (!Number.isFinite(ms) || ms <= 0) return "";
  if (ms < 1) return "<1ms";
  return ms < 1000 ? `${Math.round(ms)}ms` : `${(ms / 1000).toFixed(1)}s`;
}

function stepState(status) {
  if (status === "running") return SPINNER;
  if (status === "completed") return icon("check", "ok");
  return "";
}

function stepMarkup(step) {
  const status = step.status || "pending";
  return `<li class="work-step ${escapeHtml(status)}" data-node="${escapeHtml(step.node || "")}" data-ms="${Number(step.durationMs) || 0}">
    <span class="step-icon">${icon(stageIcon(step.node))}</span>
    <span class="step-label">${escapeHtml(step.name)}</span>
    <span class="step-time">${formatDuration(step.durationMs)}</span>
    <span class="step-state">${stepState(status)}</span>
  </li>`;
}

// Live placeholder for whichever node is running now; the stream only names a
// node once it has finished, so the label stays generic.
const NEXT_STEP = `<li class="work-step running next"><span class="step-icon">${SPINNER}</span><span class="step-label">Thinking…</span><span class="step-time"></span><span class="step-state"></span></li>`;

function workSummary(steps) {
  const total = steps.reduce((sum, step) => sum + (step.durationMs || 0), 0);
  const count = `${steps.length} step${steps.length === 1 ? "" : "s"}`;
  return total ? `${count} · ${formatDuration(total)}` : count;
}

function workMarkup(steps = [], streaming = false) {
  if (!steps.length && !streaming) return "";
  const complete = !streaming && steps.length > 0 && steps.every((step) => step.status === "completed");
  return `<div class="working-card ${complete ? "complete collapsed" : "live"}">
    <button class="working-toggle" aria-expanded="${String(!complete)}">
      <span class="work-status">${complete ? icon("checkCircle") : SPINNER}</span>
      <strong>${complete ? "Reasoning complete" : "Working on your request"}</strong>
      <small class="work-meta">${complete ? workSummary(steps) : ""}</small>
      <span class="work-chevron">${icon("chevron")}</span>
    </button>
    <ol class="working-steps">${steps.map(stepMarkup).join("")}${streaming ? NEXT_STEP : ""}</ol>
  </div>`;
}

function conceptMarkup(concept) {
  if (!concept) return "";
  return `<div class="concept-card"><span class="concept-icon">${icon("target")}</span><span><small>${escapeHtml(concept.label)}</small><strong>${escapeHtml(concept.title)}</strong></span></div>`;
}

/**
 * The hint ladder card.
 *
 * Every field here comes from the server: `level`/`total` are the turn's
 * `hint_level`/`hint_ceiling` (1-indexed for display) and `moreHelpAvailable`
 * is `more_help_available`. The button never reveals anything on its own — it
 * asks for another turn, and the backend decides what the next rung says.
 */
function hintMarkup(hint, messageId, topic) {
  if (!hint) return "";
  const level = Math.min(hint.level, hint.total);
  const atCeiling = !hint.moreHelpAvailable;
  return `<div class="hint-ladder" data-message-id="${escapeHtml(messageId)}" data-more-help="${String(Boolean(hint.moreHelpAvailable))}" data-topic="${escapeHtml(topic || "")}">
    <div class="hint-head"><div><span class="bulb-icon">${icon("bulb")}</span><span><span class="hint-count">${atCeiling ? "Final guidance" : `Hint ${level} of ${hint.total}`}</span><strong>${atCeiling ? "Solution direction" : "One step at a time"}</strong></span></div><div class="hint-dots">${Array.from({ length: hint.total }, (_, index) => `<i class="${index < level ? "active" : ""}"></i>`).join("")}</div></div>
    <div class="hint-copy">${renderMarkdown(hint.text)}</div>
    <div class="hint-actions"><button class="next-hint">${atCeiling ? "Ask for more help" : "Get next hint"}${icon("arrowRight")}</button><span class="hint-note">${icon(atCeiling ? "info" : "shield")}${atCeiling ? "You’ve reached this level’s ceiling" : "Solution stays hidden"}</span></div>
  </div>`;
}

function questionMarkup(question) {
  if (!question) return "";
  return `<div class="question-card"><span>${icon("help")}Quick clarification</span><strong>${escapeHtml(question.prompt)}</strong><div class="question-options">${question.options.map((option) => `<button>${escapeHtml(option)}</button>`).join("")}</div></div>`;
}

function actionsMarkup() {
  return `<div class="message-actions"><button class="copy-message" aria-label="Copy response" title="Copy">${icon("copy")}</button><button aria-label="Helpful" title="Helpful">${icon("thumbUp")}</button><button aria-label="Not helpful" title="Not helpful">${icon("thumbDown")}</button></div>`;
}

export function messageMarkup(message) {
  const time = new Date(message.createdAt || Date.now()).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  if (message.role === "user") {
    const attachment = message.attachment
      ? `<div class="sent-attachment"><img src="${message.attachment.data}" alt="${escapeHtml(message.attachment.name)}" style="max-width:220px;max-height:160px;border-radius:8px;margin-bottom:8px;display:block"></div>`
      : "";
    return `<article class="message user" data-id="${escapeHtml(message.id)}"><div><div class="user-bubble">${attachment}${escapeHtml(message.content)}</div><div class="message-time">${time}</div></div></article>`;
  }
  return `<article class="message agent" data-id="${escapeHtml(message.id)}">
    ${AVATAR}
    <div class="message-body"><div class="message-meta"><strong>Adaptive</strong><span>${time}</span>${message.adapted ? `<span class="adapted-label">${icon("sparkle")}Adapted to your level</span>` : ""}</div>
      ${workMarkup(message.work || [])}
      ${conceptMarkup(message.concept)}
      <div class="agent-content">${renderMarkdown(message.content)}</div>
      ${skillDeltaMarkup(message.skillDeltas)}
      ${hintMarkup(message.hint, message.id, message.topic)}
      ${questionMarkup(message.question)}
      ${actionsMarkup()}
    </div>
  </article>`;
}

/**
 * Render this turn's skill movement, e.g. "hashing +0.15".
 *
 * The values come straight from the server's `skill_deltas`; nothing here
 * computes or infers a learner model. An empty object renders nothing, which is
 * the common case: exposure creates a skill key without moving it, and a turn
 * with no verified outcome must not claim progress.
 */
function skillDeltaMarkup(deltas = {}) {
  const entries = Object.entries(deltas);
  if (!entries.length) return "";
  const chips = entries
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .map(([topic, delta]) => {
      const up = delta > 0;
      const sign = up ? "+" : "−";
      const value = Math.abs(delta).toFixed(2);
      return `<span class="skill-delta ${up ? "up" : "down"}">${escapeHtml(topic)} ${sign}${value}</span>`;
    })
    .join("");
  return `<div class="skill-deltas" aria-label="How this turn changed your skill levels">${chips}</div>`;
}

export function renderMessages(container, messages) {
  if (!messages.length) {
    container.innerHTML = `<section class="empty-state"><div class="empty-mark">${icon("logo")}</div><h1>What are you learning today?</h1><p>I’ll adapt the depth, hints, and pace to how you learn.</p><div class="suggestions">
      <button class="suggestion" data-prompt="Help me debug this code"><span>01 · DEBUG</span><strong>Debug my code</strong><small>Find the cause, not just the fix</small></button>
      <button class="suggestion" data-prompt="Explain binary search with a visual mental model"><span>02 · UNDERSTAND</span><strong>Explain a concept</strong><small>Build a durable mental model</small></button>
      <button class="suggestion" data-prompt="Give me a guided DSA challenge"><span>03 · PRACTICE</span><strong>Solve a DSA problem</strong><small>Progressive hints, solution hidden</small></button>
      <button class="suggestion" data-prompt="Review my code for clarity and edge cases"><span>04 · REVIEW</span><strong>Review my code</strong><small>Clarity, correctness, trade-offs</small></button>
    </div></section>`;
    return;
  }
  container.innerHTML = `<div class="date-divider"><span>Today</span></div>${messages.map(messageMarkup).join("")}`;
}

const AVATAR = `<span class="message-avatar" aria-hidden="true">${icon("logo")}</span>`;

export function createStreamingMessage(container, steps) {
  const article = document.createElement("article");
  article.className = "message agent";
  article.innerHTML = `${AVATAR}<div class="message-body"><div class="message-meta"><strong>Adaptive</strong><span>now</span><span class="adapted-label live">${SPINNER}Thinking</span></div>${workMarkup(steps, true)}<div class="stream-content"></div></div>`;
  container.append(article);
  return article;
}

export function updateStreamingStep(article, event) {
  const list = article.querySelector(".working-steps");
  if (!list) return;
  const { index, name, node, status = "completed", durationMs } = event.data;
  const rendered = list.querySelectorAll(".work-step:not(.next)");
  const step = stepMarkup({ name, node, status, durationMs });
  if (rendered[index]) {
    rendered[index].outerHTML = step;
    return;
  }
  const next = list.querySelector(".work-step.next");
  if (next) next.insertAdjacentHTML("beforebegin", step);
  else list.insertAdjacentHTML("beforeend", step);
}

/** The answer is starting: fold the finished process away behind its summary. */
function completeWorkCard(article) {
  const card = article.querySelector(".working-card");
  if (!card) return;
  card.querySelector(".work-step.next")?.remove();
  const steps = [...card.querySelectorAll(".work-step")];
  card.classList.remove("live");
  card.classList.add("complete", "collapsed");
  card.querySelector(".working-toggle").setAttribute("aria-expanded", "false");
  card.querySelector(".work-status").innerHTML = icon("checkCircle");
  card.querySelector(".working-toggle strong").textContent = "Reasoning complete";
  const total = steps.reduce((sum, step) => sum + (Number(step.dataset.ms) || 0), 0);
  const count = `${steps.length} step${steps.length === 1 ? "" : "s"}`;
  card.querySelector(".work-meta").textContent = total ? `${count} · ${formatDuration(total)}` : count;
  const label = article.querySelector(".adapted-label.live");
  if (label) label.innerHTML = `${icon("pen")}Writing`;
}

export function startStreamingContent(article, concept) {
  completeWorkCard(article);
  article.querySelector(".stream-content").innerHTML = `${conceptMarkup(concept)}<div class="agent-content streaming-cursor"></div>`;
}

export function updateStreamingContent(article, markdown) {
  const target = article.querySelector(".agent-content");
  if (target) target.innerHTML = renderMarkdown(markdown);
}

export function finalizeStreamingMessage(article, message) {
  const wrapper = document.createElement("div");
  wrapper.innerHTML = messageMarkup(message);
  article.replaceWith(wrapper.firstElementChild);
}

/** Turn a snake_case topic/skill key into a readable label. */
function humanizeKey(key) {
  const words = String(key).replace(/[_-]+/g, " ").trim();
  return words ? words[0].toUpperCase() + words.slice(1) : "";
}

const FLUENCY_BANDS = [
  [0.8, "Fluent and fast"],
  [0.6, "Building momentum"],
  [0.4, "Finding the patterns"],
  [0.2, "Early foundations"],
  [0, "Just getting started"],
];

function fluencyBand(overall) {
  return (FLUENCY_BANDS.find(([floor]) => overall / 100 >= floor) || FLUENCY_BANDS.at(-1))[1];
}

/** Ordered skill entries, strongest first, as display percentages. */
function skillEntries(profile) {
  return Object.entries(profile?.skill_levels || {})
    .map(([name, level]) => [humanizeKey(name), Math.round(level * 100), level])
    .sort((a, b) => b[1] - a[1]);
}

function overallFluency(skills) {
  if (!skills.length) return 0;
  return Math.round(skills.reduce((total, entry) => total + entry[1], 0) / skills.length);
}

/** r=38 -> circumference 239, which is the stroke-dasharray the CSS sets. */
function ringOffset(overall) {
  return Math.round(239 * (1 - overall / 100));
}

function skillRows(skills) {
  if (!skills.length) {
    return `<p class="skill-empty">Your skill map fills in as you work through problems.</p>`;
  }
  return skills
    .map(
      (entry) =>
        `<div class="skill-row"><div class="skill-meta"><span>${escapeHtml(entry[0])}</span><b>${entry[1]}%</b></div><div class="skill-bar"><i style="width:${entry[1]}%"></i></div></div>`,
    )
    .join("");
}

function preferenceLabels(profile) {
  const preferences = profile?.learning_preferences || {};
  return {
    style: preferences.likes_step_by_step ? "Step-by-step" : "Balanced",
    // Hints are ALWAYS a server-paced ladder; the preference only decides
    // whether answers start from the lowest rung (F10: "Direct" was shown
    // while the ladder was in use).
    hints: preferences.prefers_hints ? "Hint-first" : "Hint ladder",
  };
}

/**
 * Paint the two preference cards from the stored profile and mark them as
 * the toggles they now are. `data-preference` carries the flag each card
 * writes, so the click handler never has to guess from position.
 */
export function renderPreferenceCards(grid, profile) {
  if (!grid) return;
  const preferences = profile?.learning_preferences || {};
  const cards = [
    { key: "likes_step_by_step", on: Boolean(preferences.likes_step_by_step) },
    { key: "prefers_hints", on: Boolean(preferences.prefers_hints) },
  ];
  grid.querySelectorAll(":scope > div").forEach((card, index) => {
    const spec = cards[index];
    if (!spec) return;
    card.dataset.preference = spec.key;
    card.classList.toggle("on", spec.on);
    card.setAttribute("role", "button");
    card.setAttribute("tabindex", "0");
    card.setAttribute("aria-pressed", String(spec.on));
    const value = card.querySelector("strong");
    if (value) {
      value.textContent = spec.key === "likes_step_by_step"
        ? (spec.on ? "Step-by-step" : "Balanced")
        : (spec.on ? "Hint-first" : "Hint ladder"); // the ladder is always used (F10)
    }
  });
}

/**
 * The learning streak: consecutive days, ending today or yesterday, on which
 * at least one conversation saw activity. Derived from the conversation list
 * the sidebar already holds — nothing here is invented.
 */
export function renderStreak(conversations = []) {
  const host = document.querySelector(".usage");
  if (!host) return;

  const dayKey = (date) => new Date(date).toDateString();
  const active = new Set(conversations.map((item) => dayKey(item.updatedAt)));

  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);

  let streak = 0;
  const cursor = new Date(startOfToday);
  // Yesterday still counts as an unbroken streak until today's first session.
  if (!active.has(dayKey(cursor))) cursor.setDate(cursor.getDate() - 1);
  while (active.has(dayKey(cursor))) {
    streak += 1;
    cursor.setDate(cursor.getDate() - 1);
  }

  const label = host.querySelector("b");
  if (label) label.textContent = streak === 1 ? "1 day" : `${streak} days`;

  // Seven bars, oldest on the left, lit on the days that saw activity.
  const bars = host.querySelectorAll(".mini-bars i");
  bars.forEach((bar, index) => {
    const day = new Date(startOfToday);
    day.setDate(day.getDate() - (bars.length - 1 - index));
    bar.classList.toggle("off", !active.has(dayKey(day)));
  });
}

function growthItems(profile) {
  const errors = profile?.common_errors || [];
  if (!errors.length) {
    return `<li><i></i><span><strong>No recurring errors</strong><small>Nothing to review right now</small></span></li>`;
  }
  return errors
    .map(
      (tag) =>
        `<li class="weak"><i></i><span><strong>${escapeHtml(humanizeKey(tag))}</strong><small>Needs review</small></span></li>`,
    )
    .join("");
}

/**
 * Bind the learning-profile panel to the real `LearnerProfileView`.
 *
 * The panel reflects what `update_learner_model` persisted at the end of the
 * last turn; nothing here is computed client-side beyond formatting.
 */
export function renderProfile(profile) {
  const skills = skillEntries(profile);
  const overall = overallFluency(skills);

  const skillsHost = document.querySelector("#skills");
  if (skillsHost) skillsHost.innerHTML = skillRows(skills.slice(0, 6));

  const ring = document.querySelector(".profile-panel .score-ring");
  if (ring) ring.style.strokeDashoffset = String(ringOffset(overall));
  const score = document.querySelector(".profile-panel .profile-score strong");
  if (score) score.innerHTML = `${overall}<small>%</small>`;
  const heroTitle = document.querySelector(".profile-panel .profile-hero h3");
  if (heroTitle) heroTitle.textContent = fluencyBand(overall);
  const heroNote = document.querySelector(".profile-panel .profile-hero p");
  if (heroNote) {
    // `suggested_focus` is the server's call on what to work on next (weakest
    // first); the panel shows it rather than ranking anything itself.
    const focus = profile?.suggested_focus || [];
    heroNote.textContent = focus.length
      ? `Work on next: ${focus.join(", ")}`
      : skills.length
        ? `${skills.length} skill${skills.length === 1 ? "" : "s"} tracked`
        : "No skills tracked yet";
  }
  const topbarRing = document.querySelector(".profile-ring");
  if (topbarRing) topbarRing.textContent = String(overall);

  // Current focus: the topic most recently backed by an observed outcome,
  // computed server-side (`current_focus`). It used to be "the weakest key",
  // which on a fresh account was any exposure key at 50% (F10).
  const focusKey = profile?.current_focus || null;
  const focus = focusKey
    ? skills.find((entry) => entry[0] === humanizeKey(focusKey)) || null
    : null;
  const focusCard = document.querySelector(".focus-card");
  if (focusCard) {
    const name = focusCard.querySelector("strong");
    const badge = focusCard.querySelector("b");
    const bar = focusCard.querySelector(".focus-progress i");
    const note = focusCard.querySelector("p");
    if (focus) {
      if (name) name.textContent = focus[0];
      if (badge) badge.textContent = `Level ${Math.max(1, Math.ceil(focus[2] * 5))}`;
      if (bar) bar.style.width = `${focus[1]}%`;
      if (note) note.textContent = focus[1] >= 80 ? "Close to mastery" : "Keep practising this one";
    } else {
      if (name) name.textContent = "Not set yet";
      if (badge) badge.textContent = "Level 1";
      if (bar) bar.style.width = "0%";
      if (note) note.textContent = "Ask a question to start your map";
    }
  }

  renderPreferenceCards(document.querySelector(".profile-panel .preference-grid"), profile);

  const growth = document.querySelector(".profile-panel .growth-list");
  if (growth) growth.innerHTML = growthItems({ ...profile, common_errors: (profile?.common_errors || []).slice(0, 4) });
}

/**
 * The detailed learning profile, opened from the sidebar user chip.
 *
 * Resolves with "signout" when the user signs out from inside it, otherwise
 * null. Built from the existing panel and modal classes, so it inherits the
 * workspace styling rather than introducing a new visual language.
 */
export function showProfileModal({ user, profile, conversationCount = 0 }) {
  return new Promise((resolve) => {
    const root = document.querySelector("#modal-root");
    const skills = skillEntries(profile);
    const overall = overallFluency(skills);
    const labels = preferenceLabels(profile);
    const joined = user?.createdAt
      ? new Date(user.createdAt).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })
      : "—";
    const initials = (user?.username || "?").slice(0, 2).toUpperCase();
    const sessionNote = `${skills.length} skill${skills.length === 1 ? "" : "s"} tracked · ${conversationCount} session${conversationCount === 1 ? "" : "s"}`;

    root.innerHTML = `<div class="modal-backdrop" role="presentation"><section class="modal profile-modal" role="dialog" aria-modal="true" aria-labelledby="profile-modal-title">
      <div class="profile-identity"><span class="avatar">${escapeHtml(initials)}</span><span><strong id="profile-modal-title">${escapeHtml(user?.username || "Your account")}</strong><small>Member since ${escapeHtml(joined)}</small></span></div>
      <div class="profile-hero"><div class="profile-score"><svg viewBox="0 0 90 90"><circle cx="45" cy="45" r="38"></circle><circle class="score-ring" cx="45" cy="45" r="38" style="stroke-dashoffset:${ringOffset(overall)}"></circle></svg><strong>${overall}<small>%</small></strong></div><div><span>OVERALL FLUENCY</span><h3>${escapeHtml(fluencyBand(overall))}</h3><p>${escapeHtml(sessionNote)}</p></div></div>
      <section class="profile-section"><div class="section-label"><span>Full skill map</span><small>${profile?.language ? escapeHtml(humanizeKey(profile.language)) : "All languages"}</small></div>${skillRows(skills)}</section>
      <section class="profile-section"><div class="section-label"><span>How you learn best</span></div><div class="preference-grid" data-profile-preferences><div><i class="steps-icon"></i><span>STYLE</span><strong>${escapeHtml(labels.style)}</strong></div><div><i class="guide-icon"></i><span>HINTS</span><strong>${escapeHtml(labels.hints)}</strong></div></div></section>
      <section class="profile-section"><div class="section-label"><span>Recurring errors</span></div><ul class="growth-list">${growthItems(profile)}</ul></section>
      <div class="modal-actions"><button data-cancel>Close</button><button class="confirm danger" data-signout>Sign out</button></div>
    </section></div>`;

    const close = (result) => {
      root.innerHTML = "";
      resolve(result);
    };
    renderPreferenceCards(root.querySelector("[data-profile-preferences]"), profile);
    root.querySelector("[data-cancel]").addEventListener("click", () => close(null));
    root.querySelector("[data-signout]").addEventListener("click", () => close("signout"));
    root.querySelector(".modal-backdrop").addEventListener("click", (event) => {
      if (event.target.classList.contains("modal-backdrop")) close(null);
    });
  });
}

export function showToast(message, type = "") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  document.querySelector("#toast-region").append(toast);
  setTimeout(() => toast.remove(), 3200);
}

export function showModal({ title, description, value = "", confirmText = "Confirm", danger = false, input = false }) {
  return new Promise((resolve) => {
    const root = document.querySelector("#modal-root");
    root.innerHTML = `<div class="modal-backdrop" role="presentation"><section class="modal" role="dialog" aria-modal="true" aria-labelledby="modal-title"><h2 id="modal-title">${escapeHtml(title)}</h2><p>${escapeHtml(description)}</p>${input ? `<input value="${escapeHtml(value)}" aria-label="${escapeHtml(title)}" maxlength="60">` : ""}<div class="modal-actions"><button data-cancel>Cancel</button><button class="confirm ${danger ? "danger" : ""}" data-confirm>${escapeHtml(confirmText)}</button></div></section></div>`;
    const close = (result) => {
      root.innerHTML = "";
      resolve(result);
    };
    root.querySelector("[data-cancel]").addEventListener("click", () => close(null));
    root.querySelector("[data-confirm]").addEventListener("click", () => close(input ? root.querySelector("input").value.trim() : true));
    root.querySelector(".modal-backdrop").addEventListener("click", (event) => {
      if (event.target.classList.contains("modal-backdrop")) close(null);
    });
    root.querySelector("input")?.focus();
  });
}
