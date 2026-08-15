// lib/prompts.js — positive/negative prompt chip controller

export class PromptChips {
  constructor(root, initial = {}) {
    this.root = root;
    this.state = {
      positives: [],
      negatives: [],
    };
    this.hydrate(initial);
    this.bind();
    this.render();
  }

  hydrate(next = {}) {
    this.state.positives = [];
    this.state.negatives = [];
    for (const text of next.positives || []) this.add("positives", text, { render: false });
    for (const text of next.negatives || []) this.add("negatives", text, { render: false });
  }

  add(side, text, opts = {}) {
    if (!this.isSide(side)) return false;
    const prompt = String(text || "").trim();
    if (!prompt) return false;
    const key = prompt.toLowerCase();
    const exists = this.state[side].some((p) => p.toLowerCase() === key);
    if (exists) return false;
    this.state[side].push(prompt);
    if (opts.render !== false) this.render();
    return true;
  }

  remove(side, text) {
    if (!this.isSide(side)) return;
    const key = String(text || "").toLowerCase();
    this.state[side] = this.state[side].filter((p) => p.toLowerCase() !== key);
    this.render();
  }

  clear(side) {
    if (!this.isSide(side)) return;
    this.state[side] = [];
    this.render();
  }

  serialize() {
    const params = new URLSearchParams();
    for (const prompt of this.state.positives) params.append("positives", prompt);
    for (const prompt of this.state.negatives) params.append("negatives", prompt);
    return params;
  }

  bind() {
    if (!this.root) return;
    for (const side of ["positives", "negatives"]) {
      const input = this.root.querySelector(`[data-prompt-input="${side}"]`);
      const button = this.root.querySelector(`[data-prompt-add="${side}"]`);
      const add = () => {
        if (!input) return;
        if (this.add(side, input.value)) input.value = "";
      };
      button?.addEventListener("click", add);
      input?.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          add();
        }
      });
    }
  }

  render() {
    if (!this.root) return;
    for (const side of ["positives", "negatives"]) {
      const list = this.root.querySelector(`[data-prompt-chips="${side}"]`);
      if (!list) continue;
      list.innerHTML = "";
      const frag = document.createDocumentFragment();
      for (const prompt of this.state[side]) {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = `prompt-chip prompt-chip--${side === "positives" ? "positive" : "negative"}`;
        chip.dataset.promptChip = prompt;
        // `aria-label` makes the chip announce as "Remove prompt
        // X" rather than the bare text inside (which a screen reader
        // would otherwise read verbatim, with no indication the chip
        // is interactive).
        chip.setAttribute("aria-label", `Remove ${side === "positives" ? "include" : "exclude"} prompt "${prompt}"`);
        chip.innerHTML =
          `<span>${escapeHtml(prompt)}</span>` +
          `<span class="prompt-chip-remove" aria-hidden="true">&times;</span>`;
        chip.addEventListener("click", () => this.remove(side, prompt));
        frag.appendChild(chip);
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = side;
        hidden.value = prompt;
        frag.appendChild(hidden);
      }
      list.appendChild(frag);
    }
    this.root.dispatchEvent(new CustomEvent("promptschanged", { detail: this.state }));
  }

  isSide(side) {
    return side === "positives" || side === "negatives";
  }
}

export function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}
