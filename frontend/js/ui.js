const escapeHtml = (value = "") =>
  String(value).replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" })[character]);

function highlight(code) {
  return escapeHtml(code)
    .replace(/(#.*)$/gm, '<span class="tok-comment">$1</span>')
    .replace(/\b(def|return|if|else|elif|for|while|in|not|None|True|False|class|import|from|as)\b/g, '<span class="tok-key">$1</span>')
    .replace(/\b(\d+)\b/g, '<span class="tok-num">$1</span>')
    .replace(/(&quot;.*?&quot;|&#039;.*?&#039;)/g, '<span class="tok-str">$1</span>')
    .replace(/\b([a-zA-Z_]\w*)(?=\()/g, '<span class="tok-fn">$1</span>');
}

function codeBlock(code, language = "code") {
  return `<div class="code-block"><div class="code-head"><span>${escapeHtml(language)}</span><button class="copy-code" aria-label="Copy code">Copy</button></div><pre><code>${highlight(code.trim())}</code></pre></div>`;
}

export function renderMarkdown(markdown = "") {
  const blocks = [];
  let text = markdown.replace(/```(\w+)?\n([\s\S]*?)```/g, (_, language, code) => {
    blocks.push(codeBlock(code, language || "code"));
    return `%%CODE_${blocks.length - 1}%%`;
  });
  text = escapeHtml(text)
    // The response generator emits h2 ("## Section"); the design has one
    // heading size, so every heading level renders as the same h3.
    .replace(/^#{1,4} (.+)$/gm, "<h3>$1</h3>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/^- (.+)$/gm, "<li>$1</li>");
  text = text
    .split(/\n{2,}/)
    .map((section) => {
      if (section.startsWith("%%CODE_") || section.startsWith("<h3>")) return section;
      if (section.includes("<li>")) return `<ul>${section}</ul>`;
      return section.trim() ? `<p>${section.replace(/\n/g, "<br>")}</p>` : "";
    })
    .join("");
  blocks.forEach((block, index) => (text = text.replace(`%%CODE_${index}%%`, block)));
  return text;
}

function conceptMarkup(concept) {
  if (!concept) return "";
  return `<div class="concept-card"><span class="concept-icon">◇</span><span><small>${escapeHtml(concept.label)}</small><strong>${escapeHtml(concept.title)}</strong></span></div>`;
}

function workMarkup(steps = [], streaming = false) {
  // While streaming the card is rendered empty and filled stage by stage.
  if (!steps.length && !streaming) return "";
  const complete = steps.length > 0 && steps.every((step) => step.status === "completed");
  return `<div class="working-card ${complete ? "complete" : ""}">
    <button class="working-toggle" aria-expanded="true"><i></i><strong>${streaming && !complete ? "Working…" : "Working process"}</strong><span>⌃</span></button>
    <div class="working-steps">${steps.map((step) => `<div class="work-step ${step.status}" data-step="${escapeHtml(step.name)}"><span class="step-state"></span><span>${escapeHtml(step.name)}</span>${step.detail ? `<small>${escapeHtml(step.detail)}</small>` : ""}</div>`).join("")}</div>
  </div>`;
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
    <div class="hint-head"><div><span class="bulb-icon">?</span><span><span class="hint-count">${atCeiling ? "Final guidance" : `Hint ${level} of ${hint.total}`}</span><strong>${atCeiling ? "Solution direction" : "One step at a time"}</strong></span></div><div class="hint-dots">${Array.from({ length: hint.total }, (_, index) => `<i class="${index < level ? "active" : ""}"></i>`).join("")}</div></div>
    <div class="hint-copy">${renderMarkdown(hint.text)}</div>
    <div class="hint-actions"><button class="next-hint">${atCeiling ? "Ask for more help →" : "Get next hint →"}</button><span class="hint-note">${atCeiling ? "You’ve reached this level’s ceiling" : "Solution stays hidden"}</span></div>
  </div>`;
}

function questionMarkup(question) {
  if (!question) return "";
  return `<div class="question-card"><span>Quick clarification</span><strong>${escapeHtml(question.prompt)}</strong><div class="question-options">${question.options.map((option) => `<button>${escapeHtml(option)}</button>`).join("")}</div></div>`;
}

function actionsMarkup() {
  return `<div class="message-actions"><button class="copy-message" aria-label="Copy response" title="Copy">▣</button><button aria-label="Helpful" title="Helpful">＋</button><button aria-label="Not helpful" title="Not helpful">−</button></div>`;
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
    <span class="message-avatar" aria-hidden="true">A</span>
    <div class="message-body"><div class="message-meta"><strong>Adaptive</strong><span>${time}</span>${message.adapted ? '<span class="adapted-label">Adapted to your level</span>' : ""}</div>
      ${workMarkup(message.work || [])}
      ${conceptMarkup(message.concept)}
      <div class="agent-content">${renderMarkdown(message.content)}</div>
      ${hintMarkup(message.hint, message.id, message.topic)}
      ${questionMarkup(message.question)}
      ${actionsMarkup()}
    </div>
  </article>`;
}

export function renderMessages(container, messages) {
  if (!messages.length) {
    container.innerHTML = `<section class="empty-state"><div class="empty-mark">✦</div><h1>What are you learning today?</h1><p>I’ll adapt the depth, hints, and pace to how you learn.</p><div class="suggestions">
      <button class="suggestion" data-prompt="Help me debug this code"><span>01 · DEBUG</span><strong>Debug my code</strong><small>Find the cause, not just the fix</small></button>
      <button class="suggestion" data-prompt="Explain binary search with a visual mental model"><span>02 · UNDERSTAND</span><strong>Explain a concept</strong><small>Build a durable mental model</small></button>
      <button class="suggestion" data-prompt="Give me a guided DSA challenge"><span>03 · PRACTICE</span><strong>Solve a DSA problem</strong><small>Progressive hints, solution hidden</small></button>
      <button class="suggestion" data-prompt="Review my code for clarity and edge cases"><span>04 · REVIEW</span><strong>Review my code</strong><small>Clarity, correctness, trade-offs</small></button>
    </div></section>`;
    return;
  }
  container.innerHTML = `<div class="date-divider"><span>Today</span></div>${messages.map(messageMarkup).join("")}`;
}

export function createStreamingMessage(container, steps) {
  const article = document.createElement("article");
  article.className = "message agent";
  article.innerHTML = `<span class="message-avatar" aria-hidden="true">A</span><div class="message-body"><div class="message-meta"><strong>Adaptive</strong><span>now</span><span class="adapted-label">Adapting response</span></div>${workMarkup(steps, true)}<div class="stream-content"></div></div>`;
  container.append(article);
  return article;
}

export function updateStreamingStep(article, event) {
  const container = article.querySelector(".working-steps");
  // Stages stream in one at a time (each is a graph node that has actually
  // started), so an index we have not rendered yet means "append", not "drop".
  if (container && !container.children[event.data.index] && event.data.name) {
    container.insertAdjacentHTML(
      "beforeend",
      `<div class="work-step ${escapeHtml(event.data.status || "running")}" data-step="${escapeHtml(event.data.name)}"><span class="step-state"></span><span>${escapeHtml(event.data.name)}</span></div>`,
    );
  }
  const steps = article.querySelectorAll(".work-step");
  const target = steps[event.data.index];
  if (!target) return;
  target.className = `work-step ${event.data.status}`;
  if (event.data.detail && !target.querySelector("small")) target.insertAdjacentHTML("beforeend", `<small>${escapeHtml(event.data.detail)}</small>`);
  if (event.data.status === "completed" && [...steps].every((step) => step.classList.contains("completed"))) {
    article.querySelector(".working-card")?.classList.add("complete");
    article.querySelector(".working-toggle strong").textContent = "Working process";
  }
}

export function startStreamingContent(article, concept) {
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
    hints: preferences.prefers_hints ? "Guided" : "Direct",
  };
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
    heroNote.textContent = skills.length
      ? `${skills.length} skill${skills.length === 1 ? "" : "s"} tracked`
      : "No skills tracked yet";
  }
  const topbarRing = document.querySelector(".profile-ring");
  if (topbarRing) topbarRing.textContent = String(overall);

  // Current focus: the weakest tracked skill is the one being worked on.
  const focus = skills.at(-1);
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

  const labels = preferenceLabels(profile);
  const preferenceCells = document.querySelectorAll(".profile-panel .preference-grid > div strong");
  if (preferenceCells[0]) preferenceCells[0].textContent = labels.style;
  if (preferenceCells[1]) preferenceCells[1].textContent = labels.hints;

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
      <section class="profile-section"><div class="section-label"><span>How you learn best</span></div><div class="preference-grid"><div><i class="steps-icon"></i><span>STYLE</span><strong>${escapeHtml(labels.style)}</strong></div><div><i class="guide-icon"></i><span>HINTS</span><strong>${escapeHtml(labels.hints)}</strong></div></div></section>
      <section class="profile-section"><div class="section-label"><span>Recurring errors</span></div><ul class="growth-list">${growthItems(profile)}</ul></section>
      <div class="modal-actions"><button data-cancel>Close</button><button class="confirm danger" data-signout>Sign out</button></div>
    </section></div>`;

    const close = (result) => {
      root.innerHTML = "";
      resolve(result);
    };
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
