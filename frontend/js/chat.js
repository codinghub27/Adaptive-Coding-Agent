import { api } from "./api.js";
import { SessionManager } from "./sessions.js";
import {
  createStreamingMessage,
  finalizeStreamingMessage,
  messageMarkup,
  renderMessages,
  renderProfile,
  showProfileModal,
  showToast,
  startStreamingContent,
  updateStreamingContent,
  updateStreamingStep,
} from "./ui.js";

const elements = {
  list: document.querySelector("#conversation-list"),
  messages: document.querySelector("#message-list"),
  scroll: document.querySelector("#chat-scroll"),
  input: document.querySelector("#message-input"),
  send: document.querySelector("#send-button"),
  composer: document.querySelector("#composer"),
  file: document.querySelector("#file-input"),
  attachment: document.querySelector("#attachment-strip"),
  sidebar: document.querySelector("#sidebar"),
  profile: document.querySelector("#profile-panel"),
  scrim: document.querySelector("#drawer-scrim"),
  userChip: document.querySelector("#logout"),
};

const state = {
  user: null,
  profile: null,
  activeConversation: null,
  messages: [],
  processing: false,
  attachment: null,
  /** The actual File, posted as multipart. `attachment` is only the preview. */
  attachmentFile: null,
  /**
   * A ceiling the client asks for. The server may only lower assistance with
   * it, never raise it — the hint ladder stays server-enforced.
   */
  assistanceCap: null,
};

const IMAGE_FALLBACK_TEXT = "Help me understand what’s happening in this screenshot.";
const NEXT_HINT_PROMPT = "Give me the next hint.";
const MORE_HELP_PROMPT = "I’ve worked through the hints. Take me one step further.";

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("adaptive_theme", theme);
}

function scrollToBottom(behavior = "smooth") {
  requestAnimationFrame(() => elements.scroll.scrollTo({ top: elements.scroll.scrollHeight, behavior }));
}

function closeDrawers() {
  elements.sidebar.classList.remove("open");
  elements.profile.classList.remove("open");
  elements.scrim.classList.remove("visible");
}

function openDrawer(drawer) {
  closeDrawers();
  drawer.classList.add("open");
  elements.scrim.classList.add("visible");
}

function renderUserChip() {
  if (!elements.userChip || !state.user) return;
  const name = elements.userChip.querySelector("strong");
  const detail = elements.userChip.querySelector("small");
  const avatar = elements.userChip.querySelector(".avatar");
  if (name) name.textContent = state.user.username;
  if (detail) detail.textContent = "View learning profile";
  if (avatar) avatar.textContent = state.user.username.slice(0, 2).toUpperCase();
  const topbarAvatar = document.querySelector(".profile-button .avatar");
  if (topbarAvatar) topbarAvatar.textContent = state.user.username.slice(0, 2).toUpperCase();
}

async function selectConversation(conversation) {
  if (!conversation) return;
  state.activeConversation = conversation;
  localStorage.setItem("adaptive_active_conversation", conversation.id);
  sessionManager.setActive(conversation.id);
  document.querySelector("#chat-title").textContent = conversation.title;
  elements.messages.innerHTML = `<div class="session-skeleton"></div><div class="session-skeleton short"></div>`;
  closeDrawers();
  try {
    // History always comes from the backend, so a hard refresh restores the
    // conversation exactly as the server stored it.
    state.messages = await api.getMessages(conversation.id);
    renderMessages(elements.messages, state.messages);
    scrollToBottom("instant");
  } catch {
    elements.messages.innerHTML = `<div class="error-state">Unable to load this conversation.<br><button id="retry-messages">Retry</button></div>`;
    document.querySelector("#retry-messages").addEventListener("click", () => selectConversation(conversation));
  }
}

const sessionManager = new SessionManager(api, {
  list: elements.list,
  onSelect: selectConversation,
});

function resizeInput() {
  elements.input.style.height = "auto";
  elements.input.style.height = `${Math.min(elements.input.scrollHeight, 140)}px`;
}

function setProcessing(processing) {
  state.processing = processing;
  elements.send.disabled = processing;
  elements.send.classList.toggle("loading", processing);
  elements.input.setAttribute("aria-busy", String(processing));
  elements.input.placeholder = processing ? "Adaptive is working through your question…" : "Ask about your code, paste an error, or describe what you’re learning...";
}

/** Derive a session title from the first thing the learner asked. */
function derivedTitle(text) {
  const firstLine = String(text).split("\n").find((line) => line.trim()) || "New session";
  return firstLine.trim().slice(0, 60);
}

/** Refresh the profile panel and session list from the server after a turn. */
async function refreshAfterTurn(firstTurnText) {
  try {
    if (firstTurnText && state.activeConversation?.untitled) {
      const renamed = await api.renameConversation(state.activeConversation.id, derivedTitle(firstTurnText));
      state.activeConversation = renamed;
      document.querySelector("#chat-title").textContent = renamed.title;
    }
    const [conversations, profile] = await Promise.all([api.getConversations(), api.getProfile()]);
    sessionManager.conversations = conversations;
    const updated = conversations.find((item) => item.id === state.activeConversation?.id);
    if (updated) {
      state.activeConversation = updated;
      document.querySelector("#chat-title").textContent = updated.title;
    }
    sessionManager.render();
    state.profile = profile;
    renderProfile(profile);
  } catch {
    // A failed background refresh must never lose the answer on screen.
    showToast("Your profile will refresh on the next turn.");
  }
}

async function sendMessage(explicitText = null, { topic = null } = {}) {
  const content = (explicitText ?? elements.input.value).trim();
  if ((!content && !state.attachmentFile) || state.processing || !state.activeConversation) return;
  const fallbackText = content || IMAGE_FALLBACK_TEXT;
  const attachment = state.attachment;
  const attachmentFile = state.attachmentFile;
  const wasUntitled = Boolean(state.activeConversation.untitled);
  elements.input.value = "";
  resizeInput();
  clearAttachment();
  setProcessing(true);
  try {
    const userMessage = api.sendMessage(state.activeConversation.id, fallbackText, attachment);
    state.messages.push(userMessage);
    elements.messages.querySelector(".empty-state")?.remove();
    elements.messages.insertAdjacentHTML("beforeend", messageMarkup(userMessage));
    scrollToBottom();

    let streamingArticle = null;
    await api.streamChat(state.activeConversation.id, fallbackText, {
      attachmentFile,
      assistanceCap: state.assistanceCap,
      topic,
      onEvent(event) {
        if (event.type === "start") {
          streamingArticle = createStreamingMessage(elements.messages, event.data.steps);
          scrollToBottom();
        }
        if (event.type === "step") {
          updateStreamingStep(streamingArticle, event);
          scrollToBottom();
        }
        if (event.type === "content_start") {
          startStreamingContent(streamingArticle, event.data.concept);
          scrollToBottom();
        }
        if (event.type === "token") {
          updateStreamingContent(streamingArticle, event.data.fullContent);
          scrollToBottom();
        }
        if (event.type === "final") {
          state.messages.push(event.data.message);
          finalizeStreamingMessage(streamingArticle, event.data.message);
          scrollToBottom();
        }
      },
    });
    await refreshAfterTurn(wasUntitled ? fallbackText : null);
  } catch (error) {
    showToast(error?.message || "Message failed to send. Please try again.", "error");
    const failed = document.createElement("div");
    failed.className = "error-state";
    failed.innerHTML = `Message failed to send.<br><button>Retry</button>`;
    failed.querySelector("button").addEventListener("click", () => {
      failed.remove();
      sendMessage(fallbackText, { topic });
    });
    elements.messages.append(failed);
  } finally {
    setProcessing(false);
    elements.input.focus();
  }
}

function readAttachment(file) {
  if (!file?.type.startsWith("image/")) {
    showToast("Image upload failed. Please choose an image file.", "error");
    return;
  }
  if (file.size > 5 * 1024 * 1024) {
    showToast("Image is larger than the 5 MB limit.", "error");
    return;
  }
  const reader = new FileReader();
  reader.onload = () => {
    state.attachmentFile = file;
    state.attachment = { name: file.name || "pasted-image.png", size: file.size, type: file.type, data: reader.result };
    elements.attachment.hidden = false;
    elements.attachment.innerHTML = `<div class="attachment"><img src="${reader.result}" alt="Image preview"><span><strong>${file.name || "Pasted image"}</strong><small>${Math.max(1, Math.round(file.size / 1024))} KB · Ready to send</small></span><button aria-label="Remove attachment">×</button></div>`;
    elements.attachment.querySelector("button").addEventListener("click", clearAttachment);
  };
  reader.onerror = () => showToast("Image upload failed. Please try another image.", "error");
  reader.readAsDataURL(file);
}

function clearAttachment() {
  state.attachment = null;
  state.attachmentFile = null;
  elements.file.value = "";
  elements.attachment.hidden = true;
  elements.attachment.innerHTML = "";
}

function setAssistanceCap(capped, note) {
  state.assistanceCap = capped ? "hint" : null;
  document.querySelector("#hint-mode")?.classList.toggle("active", capped);
  if (note) showToast(note);
}

function bindComposer() {
  elements.input.addEventListener("input", resizeInput);
  elements.input.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      sendMessage();
    }
  });
  elements.send.addEventListener("click", () => sendMessage());
  document.querySelector("#attach-button").addEventListener("click", () => elements.file.click());
  elements.file.addEventListener("change", () => readAttachment(elements.file.files[0]));
  document.querySelector("#code-button").addEventListener("click", () => {
    const before = elements.input.value;
    elements.input.value = `${before}${before ? "\n\n" : ""}\`\`\`python\n# Paste your code here\n\`\`\``;
    resizeInput();
    elements.input.focus();
    elements.input.setSelectionRange(elements.input.value.length - 4, elements.input.value.length - 4);
  });
  document.querySelector("#hint-mode").addEventListener("click", () => {
    const next = state.assistanceCap === null;
    setAssistanceCap(next, next ? "Hint mode on — the agent will stay at hint level" : "Balanced guidance restored");
  });
  ["dragenter", "dragover"].forEach((name) => elements.composer.addEventListener(name, (event) => {
    event.preventDefault();
    elements.composer.classList.add("dragover");
  }));
  ["dragleave", "drop"].forEach((name) => elements.composer.addEventListener(name, (event) => {
    event.preventDefault();
    elements.composer.classList.remove("dragover");
  }));
  elements.composer.addEventListener("drop", (event) => readAttachment(event.dataTransfer.files[0]));
  document.addEventListener("paste", (event) => {
    const image = [...event.clipboardData.items].find((item) => item.type.startsWith("image/"));
    if (image) readAttachment(image.getAsFile());
  });
}

function bindMessageInteractions() {
  elements.messages.addEventListener("click", async (event) => {
    const suggestion = event.target.closest(".suggestion");
    if (suggestion) {
      elements.input.value = suggestion.dataset.prompt;
      resizeInput();
      elements.input.focus();
      return;
    }
    const toggle = event.target.closest(".working-toggle");
    if (toggle) {
      const card = toggle.closest(".working-card");
      card.classList.toggle("collapsed");
      toggle.setAttribute("aria-expanded", String(!card.classList.contains("collapsed")));
      toggle.querySelector("span").textContent = card.classList.contains("collapsed") ? "⌄" : "⌃";
      return;
    }
    const copyCode = event.target.closest(".copy-code");
    if (copyCode) {
      await navigator.clipboard.writeText(copyCode.closest(".code-block").querySelector("code").innerText);
      copyCode.textContent = "Copied";
      showToast("Code copied to clipboard");
      setTimeout(() => (copyCode.textContent = "Copy"), 1600);
      return;
    }
    const copyMessage = event.target.closest(".copy-message");
    if (copyMessage) {
      await navigator.clipboard.writeText(copyMessage.closest(".message-body").querySelector(".agent-content").innerText);
      showToast("Response copied");
      return;
    }
    const nextHint = event.target.closest(".next-hint");
    if (nextHint) {
      // The ladder lives on the server: asking for the next rung is another
      // turn, and the backend decides what (if anything) it reveals.
      const ladder = nextHint.closest(".hint-ladder");
      const moreHelp = ladder?.dataset.moreHelp === "true";
      // Hint progress is keyed by (user, conversation, topic) on the server,
      // so the follow-up has to name the same topic or the ladder restarts at
      // rung one instead of climbing.
      sendMessage(moreHelp ? NEXT_HINT_PROMPT : MORE_HELP_PROMPT, { topic: ladder?.dataset.topic || null });
      return;
    }
    const option = event.target.closest(".question-options button");
    if (option) {
      const siblings = option.parentElement.querySelectorAll("button");
      siblings.forEach((button) => (button.disabled = true));
      option.style.borderColor = "var(--blue)";
      sendMessage(option.textContent);
    }
  });
}

async function openProfileModal() {
  const choice = await showProfileModal({
    user: state.user,
    profile: state.profile,
    conversationCount: sessionManager.conversations.length,
  });
  if (choice === "signout") {
    await api.logout();
    window.location.href = "/login.html";
  }
}

function bindChrome() {
  document.querySelectorAll("[data-theme-toggle]").forEach((button) => button.addEventListener("click", () => setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark")));
  document.querySelector("#open-sidebar").addEventListener("click", () => openDrawer(elements.sidebar));
  document.querySelector("#open-profile").addEventListener("click", () => openDrawer(elements.profile));
  document.querySelectorAll("[data-close-drawers]").forEach((button) => button.addEventListener("click", closeDrawers));
  document.querySelector("#new-chat").addEventListener("click", () => sessionManager.create());
  document.querySelector("#search-toggle").addEventListener("click", () => {
    const search = document.querySelector(".session-search");
    search.hidden = !search.hidden;
    if (!search.hidden) search.querySelector("input").focus();
  });
  document.querySelector(".session-search input").addEventListener("input", (event) => sessionManager.render(event.target.value));
  document.querySelectorAll(".mode-pill button").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll(".mode-pill button").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    setAssistanceCap(button.textContent === "Challenge", `${button.textContent} guidance selected`);
  }));
  elements.userChip.addEventListener("click", openProfileModal);
  document.querySelector(".profile-settings").addEventListener("click", openProfileModal);
  document.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      sessionManager.create();
    }
    if (event.key === "Escape") closeDrawers();
  });
}

async function initialize() {
  setTheme(localStorage.getItem("adaptive_theme") || "dark");
  try {
    // Route guard: no valid session (or no refresh cookie) means no workspace.
    state.user = await api.getMe();
  } catch {
    window.location.replace("/login.html");
    return;
  }
  renderUserChip();
  sessionManager.bind();
  bindComposer();
  bindMessageInteractions();
  bindChrome();
  try {
    const [conversations, profile] = await Promise.all([sessionManager.load(), api.getProfile()]);
    state.profile = profile;
    renderProfile(profile);
    const preferred = localStorage.getItem("adaptive_active_conversation");
    const initial = conversations.find((item) => item.id === preferred) || conversations[0];
    if (initial) await selectConversation(initial);
    else await sessionManager.create();
  } catch {
    elements.list.innerHTML = `<div class="error-state">Unable to load conversations.<br><button id="retry-sessions">Retry</button></div>`;
    document.querySelector("#retry-sessions").addEventListener("click", () => location.reload());
    showToast("Unable to load your workspace", "error");
  }
}

initialize();
