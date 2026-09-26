import { showModal, showToast } from "./ui.js";

export class SessionManager {
  constructor(api, { list, onSelect }) {
    this.api = api;
    this.list = list;
    this.onSelect = onSelect;
    this.conversations = [];
    this.activeId = null;
  }

  async load() {
    this.conversations = await this.api.getConversations();
    this.render();
    return this.conversations;
  }

  setActive(id) {
    this.activeId = id;
    this.render();
  }

  render(filter = "") {
    const visible = this.conversations.filter((item) => item.title.toLowerCase().includes(filter.toLowerCase()));
    const groups = [...new Set(visible.map((item) => item.group))];
    this.list.innerHTML = groups.map((group) => `<div class="session-group"><span class="session-group-title">${group}</span>${visible.filter((item) => item.group === group).map((item) => `<div class="session-item ${item.id === this.activeId ? "active" : ""}" data-id="${item.id}"><button class="session-select"><span>${item.title}</span></button><button class="session-more" aria-label="Actions for ${item.title}">···</button></div>`).join("")}</div>`).join("") || `<div class="error-state">No conversations found.</div>`;
  }

  async create() {
    // An untitled session with no messages is already "a new session" — open
    // it instead of stacking another empty one beside it.
    const empty = this.conversations.find((item) => item.untitled && !item.messageCount);
    if (empty) {
      this.activeId = empty.id;
      this.render();
      await this.onSelect(empty);
      return empty;
    }
    const conversation = await this.api.createConversation();
    this.conversations.unshift(conversation);
    this.activeId = conversation.id;
    this.render();
    await this.onSelect(conversation);
    return conversation;
  }

  openMenu(item, button) {
    document.querySelector(".session-menu")?.remove();
    const menu = document.createElement("div");
    menu.className = "session-menu";
    menu.innerHTML = `<button data-action="rename">Rename</button><button class="delete" data-action="delete">Delete</button>`;
    const box = button.getBoundingClientRect();
    menu.style.left = `${Math.min(box.left, innerWidth - 145)}px`;
    menu.style.top = `${box.bottom + 3}px`;
    document.body.append(menu);
    menu.addEventListener("click", async (event) => {
      const action = event.target.dataset.action;
      menu.remove();
      if (action === "rename") await this.rename(item);
      if (action === "delete") await this.delete(item);
    });
    setTimeout(() => document.addEventListener("click", () => menu.remove(), { once: true }), 0);
  }

  async rename(item) {
    const title = await showModal({ title: "Rename conversation", description: "Choose a name that will be easy to find later.", value: item.title, input: true, confirmText: "Save" });
    if (!title) return;
    await this.api.renameConversation(item.id, title);
    item.title = title;
    this.render();
    if (item.id === this.activeId) document.querySelector("#chat-title").textContent = title;
    showToast("Conversation renamed");
  }

  async delete(item) {
    const confirmed = await showModal({ title: "Delete conversation?", description: "This permanently deletes the conversation and every message in it. This action cannot be undone.", confirmText: "Delete", danger: true });
    if (!confirmed) return;
    await this.api.deleteConversation(item.id);
    this.conversations = this.conversations.filter((conversation) => conversation.id !== item.id);
    if (this.activeId === item.id) {
      const fallback = this.conversations[0] || (await this.api.createConversation());
      if (!this.conversations.length) this.conversations.push(fallback);
      await this.onSelect(fallback);
    }
    this.render();
    showToast("Conversation deleted");
  }

  bind() {
    this.list.addEventListener("click", (event) => {
      const row = event.target.closest(".session-item");
      if (!row) return;
      const item = this.conversations.find((conversation) => conversation.id === row.dataset.id);
      if (event.target.closest(".session-more")) this.openMenu(item, event.target.closest(".session-more"));
      else if (event.target.closest(".session-select")) this.onSelect(item);
    });
  }
}
