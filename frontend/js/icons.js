/**
 * Inline stroke icons for the chat surface.
 *
 * One small, consistent set (24px grid, 1.75 stroke, currentColor) replaces the
 * mixed unicode glyphs the UI used before, so every symbol renders identically
 * across fonts and themes. Paths are static strings — nothing here is built
 * from server or learner text.
 */

const PATHS = {
  scan: '<path d="M3 7V5a2 2 0 0 1 2-2h2M17 3h2a2 2 0 0 1 2 2v2M21 17v2a2 2 0 0 1-2 2h-2M7 21H5a2 2 0 0 1-2-2v-2"/><path d="M7 12h10M7 8h6M7 16h8"/>',
  compass: '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2 5-5 2 2-5z"/>',
  user: '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
  map: '<path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z"/><path d="M9 4v14M15 6v14"/>',
  book: '<path d="M4 19.5V5a2 2 0 0 1 2-2h13v16H6.5A2.5 2.5 0 0 0 4 21.5"/><path d="M19 19v3H6.5"/>',
  branch: '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="8" r="2"/><path d="M6 7v10M18 10c0 4-6 3-11 7"/>',
  puzzle: '<path d="M10 3h4v3a2 2 0 1 0 4 0V3h3v7h-3a2 2 0 1 0 0 4h3v7h-7v-3a2 2 0 1 0-4 0v3H3v-7h3a2 2 0 1 0 0-4H3V3z"/>',
  bug: '<rect x="8" y="6" width="8" height="14" rx="4"/><path d="M12 20v-9M8 13H4M20 13h-4M9 6 7 3M15 6l2-3M8 17l-3 2M16 17l3 2M8 9 5 7M16 9l3-2"/>',
  bulb: '<path d="M9 18h6M10 21h4"/><path d="M12 3a6 6 0 0 0-3.5 10.9c.6.4 1 1.1 1 1.8V16h5v-.3c0-.7.4-1.4 1-1.8A6 6 0 0 0 12 3z"/>',
  target: '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5"/><circle cx="12" cy="12" r="1"/>',
  help: '<circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.6V14M12 17.5h.01"/>',
  terminal: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="m7 9 3 3-3 3M13 15h4"/>',
  shield: '<path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6z"/><path d="m9 12 2 2 4-4"/>',
  pen: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>',
  trend: '<path d="m3 17 6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
  checkCircle: '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16.5 9.5"/>',
  xCircle: '<circle cx="12" cy="12" r="9"/><path d="m9 9 6 6M15 9l-6 6"/>',
  alert: '<path d="M10.3 3.9 2.4 18a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
  sparkle: '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6"/>',
  key: '<circle cx="8" cy="15" r="4"/><path d="m11 12 9-9M17 6l3 3M14 9l2 2"/>',
  layers: '<path d="m12 3 9 5-9 5-9-5z"/><path d="m3 13 9 5 9-5"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  list: '<path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01"/>',
  code: '<path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14"/>',
  gauge: '<path d="M12 14 16 9"/><path d="M3.5 17a9 9 0 1 1 17 0"/>',
  wrench: '<path d="M14.7 6.3a4 4 0 0 0 5 5L21 13l-8 8-3-3 8-8-1.3-1.3a4 4 0 0 1-5-5L9 6l3 3-3 3-3-3 2.7-2.7a4 4 0 0 1 6 0z"/>',
  layout: '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M3 9h18M9 21V9"/>',
  clipboard: '<rect x="6" y="4" width="12" height="17" rx="2"/><path d="M9 4V3h6v1M9 11h6M9 15h4"/>',
  arrowRight: '<path d="M5 12h14M13 6l6 6-6 6"/>',
  link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  chevron: '<path d="m6 9 6 6 6-6"/>',
  copy: '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 0 0-2-2H6a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h2"/>',
  thumbUp: '<path d="M7 10v11H4V10zM7 10l4-7a2 2 0 0 1 3 2l-1 4h6a2 2 0 0 1 2 2.3l-1.3 7A2 2 0 0 1 17.7 20H7"/>',
  thumbDown: '<path d="M17 14V3h3v11zM17 14l-4 7a2 2 0 0 1-3-2l1-4H5a2 2 0 0 1-2-2.3l1.3-7A2 2 0 0 1 6.3 4H17"/>',
  logo: '<path d="M4 18 10 5l3.5 7.5"/><path d="M7 13h5"/><path d="m14 15 2.5 2.5L21 12"/>',
  dot: '<circle cx="12" cy="12" r="3.5" fill="currentColor" stroke="none"/>',
};

/** An inline SVG icon by name; unknown names fall back to a neutral dot. */
export function icon(name, className = "") {
  const body = PATHS[name] || PATHS.dot;
  return `<svg class="ico ${className}" viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">${body}</svg>`;
}

/**
 * Icon per graph node (`app/graph/stages.py::STAGE_LABELS`). The node key is
 * what the stream reports, so icons never depend on label wording.
 */
const STAGE_ICONS = {
  understand_input: "scan",
  classify_intent: "compass",
  load_learner_profile: "user",
  plan_teaching: "map",
  retrieve_knowledge: "book",
  route: "branch",
  dsa_agent: "puzzle",
  debug_agent: "bug",
  explain_agent: "bulb",
  practice_agent: "target",
  clarify: "help",
  execute_code: "terminal",
  verify: "shield",
  final_response: "pen",
  update_learner_model: "trend",
};

export function stageIcon(node) {
  return STAGE_ICONS[node] || "dot";
}

/**
 * Icon + tone per response section, keyed by the fixed titles in
 * `app/response/format.py::SECTION_TITLES` (plus "References", added client
 * side). Unlisted titles (e.g. headings an explanation writes itself) get the
 * neutral marker.
 */
const SECTION_STYLES = {
  "your next hint": ["bulb", "warn"],
  "understanding the problem": ["scan", ""],
  "constraints to keep in mind": ["layers", ""],
  "a brute-force approach": ["layers", ""],
  "why that's too slow": ["clock", "warn"],
  "the key insight": ["key", "accent"],
  pseudocode: ["list", ""],
  "solution code": ["code", "accent"],
  complexity: ["gauge", ""],
  "common mistakes to avoid": ["alert", "warn"],
  "how to recognize this pattern": ["target", ""],
  "the intuition": ["sparkle", "accent"],
  explanation: ["book", ""],
  "what the static checks found": ["scan", ""],
  "what your code is trying to do": ["target", ""],
  "a case where it fails": ["xCircle", "danger"],
  "what's going wrong": ["bug", "danger"],
  "suggested fix": ["wrench", "success"],
  "what the sandbox found": ["terminal", ""],
  "code structure": ["layout", ""],
  "line by line": ["list", ""],
  "review findings": ["clipboard", ""],
  correctness: ["checkCircle", ""],
  "next steps": ["arrowRight", "accent"],
  references: ["link", ""],
};

export function sectionStyle(title) {
  const key = String(title).replace(/[’]/g, "'").trim().toLowerCase();
  const [name, tone] = SECTION_STYLES[key] || ["dot", ""];
  return { name, tone };
}
