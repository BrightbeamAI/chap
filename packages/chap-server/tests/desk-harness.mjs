// The desk as a browser runs it: the page the server sends, loaded into
// jsdom, with the desk's own modules driving it against a live server. Node
// gives the modules WebCrypto, and fake-indexeddb gives them IndexedDB.
//
// A browser enforces the Content-Security-Policy and jsdom does not, so the
// harness records what such a policy would block: a style attribute set
// from script, and markup with a style attribute, an event handler, a
// script or a style element written through innerHTML.

import { JSDOM } from "jsdom";
import { IDBFactory } from "fake-indexeddb";
import { pathToFileURL } from "node:url";
import { DESK_DIR } from "../index.mjs";

const NODE_FETCH = globalThis.fetch;
const NODE_SET_TIMEOUT = globalThis.setTimeout;
const NODE_SET_INTERVAL = globalThis.setInterval;
const BLOCKED_MARKUP = /<(script|style)\b|\sstyle\s*=|\son[a-z]+\s*=/i;

/**
 * Open the desk at `base` (with `hash`), as a browser that already holds
 * `storage` (localStorage entries) and `indexedDB` (an IDBFactory). Waits
 * `waitMs` for it to settle. Returns the window, a log of the CHAP calls it
 * made, what a policy would have blocked, and close().
 */
export async function openDesk(base, { hash = "", waitMs = 1500, storage = {}, indexedDB = new IDBFactory() } = {}) {
  const html = await (await NODE_FETCH(`${base}/`)).text();
  const dom = new JSDOM(html, { url: `${base}/${hash}`, pretendToBeVisual: true });
  const w = dom.window;
  for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v);
  const blocked = [];
  const setAttribute = w.Element.prototype.setAttribute;
  w.Element.prototype.setAttribute = function (name, value) {
    if (/^(style|on[a-z]+)$/i.test(name)) blocked.push(`${this.tagName.toLowerCase()} ${name}="${value}"`);
    return setAttribute.call(this, name, value);
  };
  const innerHTML = Object.getOwnPropertyDescriptor(w.Element.prototype, "innerHTML");
  Object.defineProperty(w.Element.prototype, "innerHTML", {
    get() { return innerHTML.get.call(this); },
    set(value) { if (BLOCKED_MARKUP.test(String(value))) blocked.push(`innerHTML ${String(value).slice(0, 80)}`); innerHTML.set.call(this, value); },
  });
  const saved = new Map();
  const install = (name, value) => {
    saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name));
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  };
  for (const k of ["window", "document", "localStorage", "location", "history", "Node", "HTMLElement", "getSelection", "navigator", "MutationObserver"]) install(k, k === "window" ? w : w[k]);
  install("indexedDB", indexedDB);
  install("matchMedia", () => ({ matches: false, addEventListener() {} }));
  w.Element.prototype.scrollIntoView = () => {};
  const calls = [];
  // The browser resolves the desk's paths against the page, and sends the
  // page's origin with a POST.
  install("fetch", async (input, init = {}) => {
    const url = new URL(String(input), w.location.href).href;
    const headers = { ...(init.headers ?? {}), ...(init.method === "POST" ? { origin: new URL(base).origin } : {}) };
    const res = await NODE_FETCH(url, { ...init, headers });
    if (init.method === "POST" && new URL(url).pathname.endsWith("/chap")) {
      const copy = res.clone();
      calls.push({ method: JSON.parse(init.body).method, params: JSON.parse(init.body).params, reply: await copy.json() });
    }
    return res;
  });
  // The desk polls for ever; its timers are the harness's to stop.
  const timers = new Set();
  let closed = false;
  install("setTimeout", (fn, ms, ...rest) => { if (closed) return 0; const t = NODE_SET_TIMEOUT(fn, ms, ...rest); timers.add(t); return t; });
  install("setInterval", (fn, ms, ...rest) => { if (closed) return 0; const t = NODE_SET_INTERVAL(fn, ms, ...rest); timers.add(t); return t; });
  const errors = [];
  const onError = (e) => errors.push(e);
  process.on("unhandledRejection", onError);
  await import(`${pathToFileURL(DESK_DIR).href}/desk.js?${Date.now()}-${Math.random()}`);
  const settle = (ms) => new Promise((r) => NODE_SET_TIMEOUT(r, ms));
  await settle(waitMs);
  return {
    w, calls, blocked, errors, settle,
    $: (id) => w.document.getElementById(id),
    text: (id) => w.document.getElementById(id)?.textContent ?? null,
    key: (key) => w.document.dispatchEvent(new w.KeyboardEvent("keydown", { key, bubbles: true })),
    async until(predicate, timeoutMs = 4000) {
      const end = Date.now() + timeoutMs;
      while (Date.now() < end) { if (await predicate()) return true; await settle(50); }
      return false;
    },
    close() {
      closed = true;
      for (const t of timers) { clearTimeout(t); clearInterval(t); }
      process.off("unhandledRejection", onError);
      w.close();
      for (const [name, d] of saved) { if (d) Object.defineProperty(globalThis, name, d); else delete globalThis[name]; }
    },
  };
}
