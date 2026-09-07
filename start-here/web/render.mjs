// What the reviewer sees has to be what the reviewer signs.
//
// A CHAP decision carries a digest over the artefact, so the chain proves what
// was decided. It cannot prove what the decider saw. JSON.stringify passes
// U+202E RIGHT-TO-LEFT OVERRIDE and the zero-width characters through
// untouched, so a draft can render as "100.00 USD" while hashing as "1.00 USD",
// and the log will faithfully record a human approving text they never read.
//
// Everything an agent wrote goes through reveal() before it reaches the DOM.

const NAMES = new Map([
  [0x00ad, "SHY"], [0x061c, "ALM"], [0x180e, "MVS"],
  [0x200b, "ZWSP"], [0x200c, "ZWNJ"], [0x200d, "ZWJ"],
  [0x200e, "LRM"], [0x200f, "RLM"],
  [0x2028, "LS"], [0x2029, "PS"],
  [0x202a, "LRE"], [0x202b, "RLE"], [0x202c, "PDF"],
  [0x202d, "LRO"], [0x202e, "RLO"],
  [0x2060, "WJ"], [0x2061, "FA"], [0x2062, "IT"], [0x2063, "IS"], [0x2064, "IP"],
  [0x2066, "LRI"], [0x2067, "RLI"], [0x2068, "FSI"], [0x2069, "PDI"],
  [0xfeff, "BOM"],
]);

// Newline and tab are legitimate in a draft and render as themselves.
// Everything else here is invisible, or reorders the text that follows it.
const HIDDEN_SOURCE =
  "[\\u0000-\\u0008\\u000B\\u000C\\u000E-\\u001F\\u007F-\\u009F" +
  "\\u00AD\\u061C\\u180E\\u200B-\\u200F\\u2028\\u2029\\u202A-\\u202E" +
  "\\u2060-\\u2064\\u2066-\\u2069\\uFEFF]";
const HIDDEN = new RegExp(HIDDEN_SOURCE, "u");

/** A short label for one hidden code point, for example "RLO" or "U+0007". */
export function label(codePoint) {
  return NAMES.get(codePoint)
    ?? "U+" + codePoint.toString(16).toUpperCase().padStart(4, "0");
}

/** True when this single character renders as nothing, or reorders its neighbours. */
export function isHidden(character) {
  return new RegExp(HIDDEN_SOURCE, "u").test(character);
}

/** Split text into runs, marking every character that renders as nothing. */
export function reveal(text) {
  const runs = [];
  let plain = "";
  for (const character of String(text)) {
    if (HIDDEN.test(character)) {
      if (plain) { runs.push({ hidden: false, text: plain }); plain = ""; }
      runs.push({ hidden: true, text: label(character.codePointAt(0)) });
    } else {
      plain += character;
    }
  }
  if (plain) runs.push({ hidden: false, text: plain });
  return runs;
}

/** True when any string or key anywhere in this JSON value hides characters. */
export function hasHidden(value) {
  if (typeof value === "string") return HIDDEN.test(value);
  if (Array.isArray(value)) return value.some(hasHidden);
  if (value && typeof value === "object") {
    return Object.entries(value).some(([key, item]) => hasHidden(key) || hasHidden(item));
  }
  return false;
}

/**
 * Replace an element's contents with revealed text. Text nodes and elements
 * built here only: nothing in this module parses markup, so no draft can
 * introduce an element of its own.
 */
export function renderInto(element, text) {
  element.replaceChildren(...reveal(text).map(run => {
    if (!run.hidden) return document.createTextNode(run.text);
    const marker = document.createElement("span");
    marker.className = "hidden-char";
    marker.textContent = run.text;
    marker.title = "This character is invisible in normal text. It is shown "
                 + "here because it can change what the rest of the line reads as.";
    return marker;
  }));
}

/** Pretty JSON, for display only. */
export function pretty(value) {
  return JSON.stringify(value, null, 2);
}

/**
 * Serialise with object keys sorted, the way RFC 8785 does.
 *
 * Only for comparing two values in the browser. The coordinator decides what
 * counts as a change by canonical hash, so a UI that compares plain
 * JSON.stringify calls a reordered object "changed" and then watches the
 * server refuse it as "nothing changed".
 */
export function canonical(value) {
  if (value === null || typeof value !== "object") return JSON.stringify(value);
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  return "{" + Object.keys(value).sort()
    .map(key => JSON.stringify(key) + ":" + canonical(value[key])).join(",") + "}";
}

/** True when two JSON values differ only in key order, or not at all. */
export function sameJson(left, right) {
  return canonical(left) === canonical(right);
}
