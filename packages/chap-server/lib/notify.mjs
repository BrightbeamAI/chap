// Telling people that a review or a question waits for them (A6, #180).
//
// The server listens to the coordinator's log, which tells it of each entry
// once the store has taken it, and notices two things in its workspace: a
// review opened, or opened again, by an accepted task.complete or
// review.request that leaves its task under review; and a question asked,
// by an accepted whisper.ask. Each notice is a summary and a link to the
// task in the desk. Neither grants anything: whoever follows the link
// decides only as the desk lets them, and the notifier holds no key and
// makes no CHAP call.
//
// "notify" in chap.config.json takes either part, or both:
//
//   "console": true    a line on the server's console
//   "webhook": { "url": "https://...", "secret": "..." }
//                      a POST of the notice as JSON to url, with
//                      X-Chap-Signature: sha256=<hex HMAC-SHA256 of the body under secret>
//
// Notices go out in order, once the call that caused them is answered, so
// no call waits on them; one the webhook does not take, or that cannot
// reach it, is logged and dropped.

import { createHmac } from "node:crypto";

const NOTIFY_SHAPE = 'notify in chap.config.json takes {"console": true, "webhook": {"url": ..., "secret": ...}}';

/**
 * `notify` from chap.config.json, checked: { console, webhook } or null. A
 * setting that says something else is refused, so a slip in the file does
 * not leave people untold.
 */
export function notifySettings(value) {
  if (value === undefined || value === null) return null;
  if (typeof value !== "object" || Array.isArray(value) || Object.keys(value).some((k) => k !== "console" && k !== "webhook")) throw new Error(NOTIFY_SHAPE);
  const console_ = value.console ?? false;
  if (typeof console_ !== "boolean") throw new Error("notify.console in chap.config.json is true or false");
  let webhook = null;
  if (value.webhook !== undefined && value.webhook !== null) {
    const w = value.webhook;
    if (typeof w !== "object" || Array.isArray(w) || Object.keys(w).sort().join() !== "secret,url") throw new Error('notify.webhook in chap.config.json takes {"url": ..., "secret": ...}');
    let url = null;
    try { url = new URL(w.url); } catch { /* refused below */ }
    if (!url || !["http:", "https:"].includes(url.protocol)) throw new Error(`notify.webhook.url in chap.config.json is ${JSON.stringify(w.url)}; it is an http or https URL`);
    if (typeof w.secret !== "string" || !w.secret) throw new Error("notify.webhook.secret in chap.config.json is the secret each notice is signed with: a string of one character or more");
    webhook = { url: w.url, secret: w.secret };
  }
  return { console: console_, webhook };
}

/** The signature header's value for a body: sha256= and the hex HMAC-SHA256 under the secret. */
export function notifySignature(body, secret) {
  return `sha256=${createHmac("sha256", secret).update(body).digest("hex")}`;
}

/** Text for a console line: control and bidirectional characters, which could rewrite the terminal, shown as their code points. */
const printable = (s) => String(s).replace(/[\u0000-\u001f\u007f-\u009f‎‏‪-‮⁦-⁩]/g, (c) => `<U+${c.codePointAt(0).toString(16).toUpperCase().padStart(4, "0")}>`);

/**
 * The notice an entry of the log calls for, or null: `ws` is the workspace
 * as the coordinator holds it as the call ends, and `deskUrl` the desk's
 * address, which the link names.
 */
export function noticeFor(ws, entry, deskUrl) {
  const call = entry?.envelope;
  const p = call?.params;
  if (!call || entry.outcome || !p || typeof p.task_id !== "string") return null;
  const task = ws.tasks.get(p.task_id);
  if (!task) return null;
  const from = p.from;
  let event, to, summary, extra = {};
  if (call.method === "task.complete" || call.method === "review.request") {
    if (task.state !== "review_requested" || !task.review) return null;
    event = "review.requested";
    to = [...task.review.requested_to];
    summary = `${from} asks ${to.join(", ")} to review ${task.kind} ${task.id}`;
  } else if (call.method === "whisper.ask") {
    const asked = [...ws.whispers.values()].filter((w) => w.task_id === task.id && w.asker === from).at(-1);
    if (!asked) return null;
    event = "whisper.asked";
    to = [...asked.askee];
    extra = { whisper_id: asked.id };
    summary = `${from} asks ${to.join(", ")} a question on ${task.kind} ${task.id}`;
  } else {
    return null;
  }
  return { event, workspace: ws.id, task_id: task.id, kind: task.kind, from, to, ...extra, summary,
    link: deskUrl ? `${deskUrl}#task=${encodeURIComponent(task.id)}` : null, seq: entry.seq, at: entry.arrived };
}

/**
 * The audit listener that notifies, for the settings notifySettings gives.
 * `deskUrl()` gives the desk's address when a notice is made. `log` takes
 * the console lines and the reports of a webhook that failed.
 */
export function makeNotifier(settings, workspace, { deskUrl, log = console.log, fetchImpl = globalThis.fetch, userAgent = "chap-server" } = {}) {
  let queue = Promise.resolve();
  const send = async (notice) => {
    if (settings.console) log(`${printable(notice.summary)}${notice.link ? `: ${notice.link}` : ""}`);
    if (!settings.webhook) return;
    const { url, secret } = settings.webhook;
    // The URL can hold a secret of its own, a hook's path say, so a report names its origin alone.
    const where = new URL(url).origin;
    const body = JSON.stringify(notice);
    try {
      const res = await fetchImpl(url, {
        method: "POST", body, redirect: "manual", signal: AbortSignal.timeout(10_000),
        headers: { "content-type": "application/json", "x-chap-signature": notifySignature(body, secret), "user-agent": userAgent },
      });
      await res.arrayBuffer().catch(() => {});
      if (res.status < 200 || res.status >= 300) log(`The webhook at ${where} answered HTTP ${res.status} to the notice of ${notice.event} on ${notice.task_id}; it is not sent again.`);
    } catch (e) {
      const why = e.name === "TimeoutError" ? "no answer in 10 s" : e.cause?.code ?? e.cause?.message ?? e.message;
      log(`The webhook at ${where} could not be reached (${why}); the notice of ${notice.event} on ${notice.task_id} is not sent again.`);
    }
  };
  return (ws, entry) => {
    if (ws.id !== workspace) return;
    const notice = noticeFor(ws, entry, deskUrl());
    if (notice) queue = queue.then(() => send(notice));
  };
}
