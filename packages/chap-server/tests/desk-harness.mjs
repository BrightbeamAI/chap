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
// The cookies each server set, kept as a browser profile keeps them: a desk
// opened again on the same server sends them.
const JAR = new Map();

/**
 * Open the desk at `base` (with `path`, a query and a hash after the
 * slash), as a browser that already holds `storage` (localStorage entries),
 * `session` (sessionStorage entries) and `indexedDB` (an IDBFactory), with
 * `authenticator` as its WebAuthn authenticator, if it has one
 * (webauthn-authenticator.mjs), at the link that carries `enrol`, the
 * enrolment code, and with `prompt` answering what the desk asks. Waits `waitMs` for it to settle. Returns
 * the window, a log of the CHAP calls it made, what a policy would have
 * blocked, and close().
 */
export async function openDesk(base, { hash = "", path = "", waitMs = 1500, storage = {}, session = {}, indexedDB = new IDBFactory(), authenticator = null, enrol = process.env.CHAP_ENROLMENT_CODE ?? null, prompt = () => null } = {}) {
  const html = await (await NODE_FETCH(`${base}/`)).text();
  // Opened by the link the server prints, which carries the enrolment code;
  // `enrol: null` opens it without one.
  if (enrol) hash = `#${[hash.replace(/^#/, ""), `enrol=${encodeURIComponent(enrol)}`].filter(Boolean).join("&")}`;
  const dom = new JSDOM(html, { url: `${base}/${path}${hash}`, pretendToBeVisual: true });
  const w = dom.window;
  for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v);
  for (const [k, v] of Object.entries(session)) w.sessionStorage.setItem(k, v);
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
  for (const k of ["window", "document", "localStorage", "sessionStorage", "location", "history", "Node", "HTMLElement", "getSelection", "navigator", "MutationObserver"]) install(k, k === "window" ? w : w[k]);
  install("indexedDB", indexedDB);
  install("PublicKeyCredential", authenticator ? authenticator.install(w) : undefined);
  install("matchMedia", () => ({ matches: false, addEventListener() {} }));
  // What the person types when the desk asks; nothing, by default.
  install("prompt", prompt);
  w.Element.prototype.scrollIntoView = () => {};
  const calls = [];
  // The desk polls for ever; its timers are the harness's to stop.
  const timers = new Set();
  let closed = false;
  // A closed page gets no answer: a request it makes, or one still on its
  // way when it closes, never settles, so nothing the desk would do next
  // runs once its document is gone.
  const gone = () => new Promise(() => {});
  // The browser resolves the desk's paths against the page, and sends the
  // page's origin with a POST. The answer is read in full here, so the desk
  // reads it from memory.
  install("fetch", async (input, init = {}) => {
    if (closed) return gone();
    const url = new URL(String(input), w.location.href).href;
    const jar = JAR.get(new URL(url).origin) ?? new Map();
    const cookie = [...jar].map(([k, v]) => `${k}=${v}`).join("; ");
    const headers = { ...(init.headers ?? {}), ...(init.method === "POST" ? { origin: new URL(base).origin } : {}), ...(cookie ? { cookie } : {}) };
    const res = await NODE_FETCH(url, { ...init, headers });
    for (const set of res.headers.getSetCookie?.() ?? []) {
      const [pair] = set.split(";");
      const at = pair.indexOf("=");
      if (at > 0) { jar.set(pair.slice(0, at).trim(), pair.slice(at + 1).trim()); JAR.set(new URL(url).origin, jar); }
    }
    const body = await res.arrayBuffer();
    if (closed) return gone();
    if (init.method === "POST" && new URL(url).pathname.endsWith("/chap")) {
      const envelope = JSON.parse(init.body);
      calls.push({ method: envelope.method, params: envelope.params, envelope, reply: JSON.parse(new TextDecoder().decode(body)) });
    }
    return new Response(res.status === 204 || res.status === 304 ? null : body, { status: res.status, statusText: res.statusText, headers: res.headers });
  });
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
