/**
 * Real API client for the Adaptive Coding Agent backend.
 *
 * Method names and normalized return shapes are deliberately identical to the
 * mock layer this replaces, so the presentation modules bind unchanged.
 *
 * Auth model:
 * - the access token lives in a module variable (memory only) — never in
 *   localStorage, so an XSS payload cannot read it back out;
 * - the refresh token lives in an httpOnly, SameSite=Strict cookie scoped to
 *   /auth, set by the server; JavaScript can neither read nor forge it;
 * - a hard refresh loses the in-memory access token, so `ensureSession()`
 *   silently exchanges the cookie for a new pair on first use;
 * - a 401 mid-flight triggers one single-flight refresh and one retry; if that
 *   refresh fails, the session is over and we redirect to the login page.
 *
 * Everything is same-origin: Vite proxies the API paths in development and
 * FastAPI serves the built bundle in production, so the cookie just works and
 * no CORS credential handling is needed here.
 */

import { streamChat as streamChatRequest } from "./streaming.js";

const LOGIN_URL = "/login";
const DISPLAY_TITLE_FALLBACK = "Untitled learning session";

/** In-memory only. Deliberately not persisted anywhere. */
let accessToken = null;
/** Single-flight guard so N concurrent 401s cause one refresh, not N. */
let refreshInFlight = null;

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

export function getAccessToken() {
  return accessToken;
}

function setAccessToken(token) {
  accessToken = token;
}

function redirectToLogin() {
  accessToken = null;
  if (!window.location.pathname.endsWith("/login")) window.location.replace(LOGIN_URL);
}

/** Pull a human-readable message out of a FastAPI error body. */
async function errorMessage(response, fallback) {
  try {
    const body = await response.json();
    const detail = body?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail) && detail[0]?.msg) return detail[0].msg;
  } catch {
    /* non-JSON body — fall through to the generic message */
  }
  return fallback;
}

/**
 * Exchange the refresh cookie for a fresh token pair.
 *
 * Sent with no body on purpose: the server reads the httpOnly cookie when the
 * body omits the token.
 */
async function refreshTokens() {
  const response = await fetch("/auth/refresh", {
    method: "POST",
    credentials: "same-origin",
  });
  if (!response.ok) throw new ApiError("Session expired", response.status);
  const pair = await response.json();
  setAccessToken(pair.access_token);
  return pair;
}

/** Refresh at most once concurrently; every caller awaits the same promise. */
function refreshOnce() {
  refreshInFlight ??= refreshTokens().finally(() => {
    refreshInFlight = null;
  });
  return refreshInFlight;
}

/** Make sure we hold an access token before a protected call. */
export async function ensureSession() {
  if (accessToken) return accessToken;
  await refreshOnce();
  return accessToken;
}

function authHeaders(extra = {}) {
  return accessToken ? { ...extra, Authorization: `Bearer ${accessToken}` } : { ...extra };
}

/**
 * Authenticated fetch returning the raw `Response`, with one transparent
 * refresh-and-retry on 401. Used directly by the streaming reader.
 */
export async function authFetch(path, options = {}, { retry = true } = {}) {
  if (!accessToken) await ensureSession();
  const send = () =>
    fetch(path, {
      ...options,
      credentials: "same-origin",
      headers: authHeaders(options.headers),
    });

  let response = await send();
  if (response.status === 401 && retry) {
    try {
      await refreshOnce();
    } catch {
      redirectToLogin();
      throw new ApiError("Session expired", 401);
    }
    response = await send();
    if (response.status === 401) {
      redirectToLogin();
      throw new ApiError("Session expired", 401);
    }
  }
  return response;
}

/** Authenticated JSON call. Returns the parsed body, or null for a 204. */
async function request(path, { method = "GET", body, fallbackError = "Request failed" } = {}) {
  const options = { method };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await authFetch(path, options);
  if (!response.ok) throw new ApiError(await errorMessage(response, fallbackError), response.status);
  if (response.status === 204) return null;
  return response.json();
}

/** Unauthenticated JSON call, for the register/login/logout endpoints. */
async function publicRequest(path, { method = "POST", body, fallbackError = "Request failed" } = {}) {
  const options = { method, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers = { "Content-Type": "application/json" };
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (!response.ok) throw new ApiError(await errorMessage(response, fallbackError), response.status);
  if (response.status === 204) return null;
  return response.json();
}

/* ------------------------------------------------------------------ *
 * Normalizers: backend shapes -> the shapes the UI modules already use
 * ------------------------------------------------------------------ */

/** Sidebar grouping label, derived from last activity (was a mock field). */
function activityGroup(isoDate) {
  const then = new Date(isoDate);
  const startOfToday = new Date();
  startOfToday.setHours(0, 0, 0, 0);
  const days = Math.floor((startOfToday - then) / 86400000);
  if (days < 0) return "Today";
  if (days === 0) return "Today";
  if (days === 1) return "Yesterday";
  if (days < 7) return "Previous 7 days";
  if (days < 30) return "Previous 30 days";
  return "Older";
}

function normalizeConversation(summary) {
  const updatedAt = summary.updated_at || summary.created_at || new Date().toISOString();
  return {
    id: summary.conversation_id || summary.id,
    title: summary.title || DISPLAY_TITLE_FALLBACK,
    untitled: !summary.title,
    group: activityGroup(updatedAt),
    updatedAt,
    messageCount: summary.message_count ?? 0,
  };
}

function normalizeStoredMessage(message) {
  return {
    id: message.id,
    role: message.role === "assistant" ? "agent" : "user",
    content: message.content,
    createdAt: message.created_at,
    // Stored history keeps the rendered text only; the working steps, concept
    // card and hint ladder of a past turn are live-stream decoration and are
    // intentionally not replayed from the database.
    adapted: message.role === "assistant",
  };
}

/** Deterministic-ish local id for the optimistic user bubble. */
function localId(prefix) {
  return `${prefix}_${crypto.randomUUID()}`;
}

/* ------------------------------------------------------------------ *
 * The api surface consumed by auth.js / chat.js / sessions.js
 * ------------------------------------------------------------------ */

export const api = {
  async login({ username, password }) {
    const pair = await publicRequest("/auth/login", {
      body: { username, password },
      fallbackError: "That username and password combination wasn’t recognized.",
    });
    setAccessToken(pair.access_token);
    const user = await this.getMe();
    return { user };
  },

  async register({ username, password }) {
    await publicRequest("/auth/register", {
      body: { username, password },
      fallbackError: "We couldn’t create that account.",
    });
    return { success: true };
  },

  async refresh() {
    return refreshOnce();
  },

  async getMe() {
    const me = await request("/auth/me", { fallbackError: "Unauthorized" });
    return { id: me.id, username: me.username, createdAt: me.created_at };
  },

  async logout() {
    try {
      await fetch("/auth/logout", { method: "POST", credentials: "same-origin" });
    } finally {
      accessToken = null;
    }
  },

  async getConversations() {
    const rows = await request("/conversations", { fallbackError: "Unable to load conversations" });
    return rows.map(normalizeConversation);
  },

  async getMessages(id) {
    const rows = await request(`/conversations/${id}/messages`, {
      fallbackError: "Unable to load this conversation",
    });
    return rows.map(normalizeStoredMessage);
  },

  async createConversation(title = null) {
    const created = await request("/conversations", {
      method: "POST",
      body: { title },
      fallbackError: "Unable to start a new session",
    });
    return normalizeConversation({
      id: created.conversation_id,
      title: created.title,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
      message_count: 0,
    });
  },

  async renameConversation(id, title) {
    const updated = await request(`/conversations/${id}`, {
      method: "PATCH",
      body: { title },
      fallbackError: "Unable to rename this conversation",
    });
    return normalizeConversation(updated);
  },

  async deleteConversation(id) {
    await request(`/conversations/${id}`, {
      method: "DELETE",
      fallbackError: "Unable to delete this conversation",
    });
    return { success: true };
  },

  async getProfile() {
    return request("/profile", { fallbackError: "Unable to load your learning profile" });
  },

  /**
   * The optimistic user bubble. The backend persists both turns itself as part
   * of the /chat turn, so this deliberately makes no request — posting the
   * message here as well would store it twice.
   */
  sendMessage(conversationId, content, attachment = null) {
    return {
      id: localId("msg"),
      role: "user",
      content,
      attachment,
      createdAt: new Date().toISOString(),
    };
  },

  /** One streamed teaching turn. See js/streaming.js for the event contract. */
  async streamChat(conversationId, content, options) {
    return streamChatRequest({ conversationId, content, ...options });
  },
};
