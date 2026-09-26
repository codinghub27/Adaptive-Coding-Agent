import { api } from "./api.js";

/**
 * Client-side validation mirrors the server's own policy so the common
 * mistakes are caught without a round trip. The server remains the authority:
 * anything it rejects is surfaced verbatim in the form alert.
 */
const USERNAME_PATTERN = /^[A-Za-z0-9_.-]{3,64}$/;
const MIN_PASSWORD_LENGTH = 8;
const MAX_PASSWORD_LENGTH = 72;

const auth = {
  initTheme() {
    const theme = localStorage.getItem("adaptive_theme") || "dark";
    document.documentElement.dataset.theme = theme;
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
        document.documentElement.dataset.theme = next;
        localStorage.setItem("adaptive_theme", next);
      });
    });
  },

  bindPasswordToggles() {
    document.querySelectorAll(".password-toggle").forEach((button) => {
      button.addEventListener("click", () => {
        const input = button.previousElementSibling;
        const show = input.type === "password";
        input.type = show ? "text" : "password";
        button.setAttribute("aria-label", show ? "Hide password" : "Show password");
      });
    });
  },
};

function showToast(message, type = "") {
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  document.querySelector("#toast-region")?.append(toast);
  setTimeout(() => toast.remove(), 3200);
}

function setError(name, message = "") {
  const target = document.querySelector(`#${name}-error`);
  const input = document.querySelector(`#${name}`);
  if (target) target.textContent = message;
  if (input) input.setAttribute("aria-invalid", String(Boolean(message)));
}

function setLoading(form, loading) {
  form.querySelector(".primary-btn")?.classList.toggle("loading", loading);
  form.querySelectorAll("input, button").forEach((element) => (element.disabled = loading));
}

function initLogin() {
  const form = document.querySelector("#login-form");
  if (!form) return;
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const username = String(data.get("username")).trim();
    const password = String(data.get("password"));
    setError("username");
    setError("password");
    if (!username) setError("username", "Enter your username.");
    if (!password) setError("password", "Enter your password.");
    if (form.querySelector("[aria-invalid='true']")) return;
    const alert = document.querySelector("#form-alert");
    alert.hidden = true;
    setLoading(form, true);
    try {
      await api.login({ username, password });
      window.location.href = "/chat.html";
    } catch (error) {
      alert.textContent = error.message;
      alert.hidden = false;
    } finally {
      setLoading(form, false);
    }
  });
}

function passwordScore(password) {
  return [password.length >= MIN_PASSWORD_LENGTH, /[A-Z]/.test(password), /\d/.test(password), /[^A-Za-z0-9]/.test(password)].filter(Boolean).length;
}

function initRegister() {
  const form = document.querySelector("#register-form");
  if (!form) return;
  const password = form.querySelector("#password");
  const strength = form.querySelector(".strength");
  password.addEventListener("input", () => {
    const score = passwordScore(password.value);
    strength.dataset.score = score;
    strength.querySelector("span").textContent = ["Enter a password", "Weak", "Fair", "Strong", "Excellent"][score];
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const data = new FormData(form);
    const values = Object.fromEntries(data);
    const username = String(values.username).trim();
    ["username", "password", "confirm"].forEach((name) => setError(name));
    if (!USERNAME_PATTERN.test(username)) setError("username", "Use 3-64 characters: letters, numbers, dot, dash or underscore.");
    if (String(values.password).length < MIN_PASSWORD_LENGTH || String(values.password).length > MAX_PASSWORD_LENGTH) {
      setError("password", `Use between ${MIN_PASSWORD_LENGTH} and ${MAX_PASSWORD_LENGTH} characters.`);
    }
    if (values.password !== values.confirm) setError("confirm", "Passwords do not match.");
    if (form.querySelector("[aria-invalid='true']")) return;
    setLoading(form, true);
    const alert = document.querySelector("#form-alert");
    try {
      await api.register({ username, password: String(values.password) });
      alert.textContent = "Workspace created. Taking you to sign in…";
      alert.hidden = false;
      showToast("Account created successfully");
      setTimeout(() => (window.location.href = `/login.html?username=${encodeURIComponent(username)}`), 900);
    } catch (error) {
      alert.classList.remove("success");
      alert.textContent = error.message;
      alert.hidden = false;
      setLoading(form, false);
    }
  });
}

auth.initTheme();
auth.bindPasswordToggles();
initLogin();
initRegister();
document.querySelectorAll("[data-toast]").forEach((link) => link.addEventListener("click", (event) => {
  event.preventDefault();
  showToast(link.dataset.toast);
}));

const queryUsername = new URLSearchParams(location.search).get("username");
if (queryUsername && document.querySelector("#username")) document.querySelector("#username").value = queryUsername;
