// The review desk, driven against a live server in a real DOM.
//
//   npm --prefix start-here/tests install
//   node --test start-here/tests/desk.test.mjs
//
// jsdom builds the document from the served index.html; Node then imports the
// shipped web/app.js and lets it drive that document. No bundling and no copy
// of the source, so this exercises the same file the browser loads.
//
// It covers what a screenshot cannot: that the verification badge is written on
// every path including the failing ones, and that an invisible character in a
// draft is drawn where the reviewer is looking.

import test from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join, resolve } from "node:path";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";

const HERE = dirname(fileURLToPath(import.meta.url));
const STARTER = resolve(HERE, "..");
const APP = pathToFileURL(resolve(STARTER, "web/app.js")).href;
// Captured before anything is swapped onto globalThis, or the wrappers below
// end up calling themselves.
const NODE_TIMEOUT = globalThis.setTimeout;
const NODE_FETCH = globalThis.fetch;

let JSDOM;
try {
  ({ JSDOM } = await import("jsdom"));
} catch {
  console.log("# jsdom is not installed; skipping. "
            + "npm --prefix start-here/tests install");
  process.exit(0);
}

const RLO = String.fromCodePoint(0x202e);
const PDF = String.fromCodePoint(0x202c);

/** Start start.py on a free port and read the reviewer link off its stdout.
 *
 * Each call gets its own database, so one test cannot see another's tasks and
 * none of them touch the .data/ folder a reader is using for the real demo.
 */
async function startDesk() {
  const python = process.env.PYTHON || "python3";
  const scratch = mkdtempSync(join(tmpdir(), "chap-desk-"));
  const child = spawn(python, ["start.py", "--no-browser", "--port", "0",
                               "--db", join(scratch, "browser.db"),
                               "--connection", join(scratch, "agent.json")],
                      { cwd: STARTER, stdio: ["ignore", "pipe", "pipe"] });
  let buffer = "";
  const url = await new Promise((fulfil, fail) => {
    const timer = setTimeout(() => fail(new Error("start.py printed no link:\n" + buffer)), 30_000);
    const look = chunk => {
      buffer += chunk;
      const found = buffer.match(/http:\/\/127\.0\.0\.1:\d+\/#reviewer=\S+/);
      if (found) { clearTimeout(timer); fulfil(found[0]); }
    };
    child.stdout.on("data", look);
    child.stderr.on("data", chunk => { buffer += chunk; });
    child.on("exit", code => { clearTimeout(timer); fail(new Error(`start.py exited ${code}\n${buffer}`)); });
  });
  let stopped = false;
  return {
    url,
    base: new URL(url).origin,
    token: new URLSearchParams(new URL(url).hash.slice(1)).get("reviewer"),
    async stop() {
      if (stopped) return;
      stopped = true;
      child.kill("SIGKILL");
      await once(child, "exit");
      rmSync(scratch, { recursive: true, force: true });
    },
  };
}

/** Load the served page into jsdom, then let the shipped app.js drive it. */
async function openDesk(desk) {
  const html = await NODE_FETCH(desk.base + "/").then(response => response.text());
  const dom = new JSDOM(html, { url: desk.url });
  const { window } = dom;

  const saved = {};
  const install = (name, value) => {
    saved[name] = Object.getOwnPropertyDescriptor(globalThis, name);
    Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
  };
  install("window", window);
  install("document", window.document);
  install("location", window.location);
  install("history", window.history);
  install("Blob", window.Blob);
  install("URLSearchParams", window.URLSearchParams);
  // The browser resolves "/api/desk" against the page's origin. Node's fetch
  // wants that spelled out.
  install("fetch", (input, init) => NODE_FETCH(new URL(input, desk.base), init));

  // app.js reschedules itself forever, which would keep Node alive after the
  // test ends. Hand it a setTimeout whose handles this harness can cancel.
  // (Handing it window.setTimeout instead recurses: jsdom's own timers call
  // back into the global one.)
  let closed = false;
  const handles = new Set();
  install("setTimeout", (callback, delay, ...rest) => {
    if (closed) return 0;
    const handle = NODE_TIMEOUT(callback, delay, ...rest);
    handles.add(handle);
    return handle;
  });

  await import(APP + "?t=" + Math.random());     // a fresh module per test

  return {
    document: window.document,
    badge: () => window.document.getElementById("verification"),
    async settle(predicate, { tries = 150, everyMs = 100 } = {}) {
      for (let attempt = 0; attempt < tries; attempt += 1) {
        if (predicate()) return true;
        await new Promise(done => NODE_TIMEOUT(done, everyMs));
      }
      return false;
    },
    close() {
      closed = true;
      for (const handle of handles) clearTimeout(handle);
      handles.clear();
      window.close();
      for (const [name, descriptor] of Object.entries(saved)) {
        if (descriptor) Object.defineProperty(globalThis, name, descriptor);
        else delete globalThis[name];
      }
    },
  };
}

function api(desk, path, options = {}) {
  return NODE_FETCH(desk.base + path, {
    ...options,
    headers: {
      "X-CHAP-Reviewer": desk.token,
      ...(options.body ? { "Content-Type": "application/json" } : {}),
    },
  });
}

test("the desk renders the draft and reports a verified chain", async t => {
  const desk = await startDesk();
  t.after(() => desk.stop());
  const ui = await openDesk(desk);
  t.after(() => ui.close());

  assert.ok(await ui.settle(() => /Chain verified/.test(ui.badge().textContent)),
            `badge never turned green: ${ui.badge().textContent}`);
  assert.equal(ui.badge().className, "badge good");
  assert.match(ui.document.getElementById("draft").textContent, /guaranteed to arrive tomorrow/);
  assert.match(ui.document.getElementById("digest").textContent, /^sha256:[0-9a-f]{64}$/);
  assert.equal(ui.document.getElementById("state").textContent, "waiting for you");
  assert.ok(ui.document.querySelectorAll("#timeline .event").length >= 4,
            "the timeline should carry the opening events and this task's events");
  assert.match(ui.document.getElementById("hint").textContent, /promises a date/);
});

test("an invisible character in a draft is drawn where the reviewer looks", async t => {
  const desk = await startDesk();
  t.after(() => desk.stop());

  const spoofed = { amount: `100${RLO}00.1${PDF} USD` };
  const created = await api(desk, "/api/drafts", {
    method: "POST",
    body: JSON.stringify({ kind: "payment", draft: spoofed }),
  }).then(response => response.json());
  assert.ok(created.task_id, JSON.stringify(created));

  const ui = await openDesk(desk);
  t.after(() => ui.close());
  assert.ok(await ui.settle(() => ui.document.querySelectorAll("#draft .hidden-char").length > 0),
            "no hidden-character markers appeared in the draft panel");

  const markers = [...ui.document.querySelectorAll("#draft .hidden-char")].map(n => n.textContent);
  assert.deepEqual(markers, ["RLO", "PDF"]);
  assert.equal(ui.document.getElementById("hidden-warning").hidden, false,
               "the warning above the draft must be showing");
  // The raw code points must not survive into the rendered text, or the browser
  // reorders what the reviewer reads no matter what is written beside it.
  assert.equal(ui.document.getElementById("draft").textContent.includes(RLO), false);
  assert.equal(ui.document.getElementById("draft").textContent.includes(PDF), false);
});

test("reloading the page does not lock the reviewer out", async t => {
  const desk = await startDesk();
  t.after(() => desk.stop());

  const first = await openDesk(desk);
  assert.ok(await first.settle(() => first.badge().className === "badge good"));
  first.close();

  // openDesk loads the same URL again, which is what a reload does. If the app
  // strips the fragment on boot, the reloaded page has no capability and every
  // request comes back 403.
  const second = await openDesk(desk);
  t.after(() => second.close());
  assert.ok(await second.settle(() => second.badge().className === "badge good"),
            `after a reload the desk showed "${second.badge().textContent}"`);
  assert.match(second.document.getElementById("draft").textContent, /guaranteed/);
});

test("the badge goes red when no verdict arrives, and never stays green", async t => {
  const desk = await startDesk();
  t.after(() => desk.stop());
  const ui = await openDesk(desk);
  t.after(() => ui.close());

  assert.ok(await ui.settle(() => ui.badge().className === "badge good"));

  // Stopping the server is the case a fail-open UI gets wrong: the request
  // throws, the render never runs, and the last good verdict stays on screen.
  await desk.stop();
  assert.ok(await ui.settle(() => ui.badge().className === "badge bad"),
            `badge stayed "${ui.badge().textContent}" after the chain became uncheckable`);
  assert.doesNotMatch(ui.badge().textContent, /Chain verified/);
  assert.equal(ui.document.getElementById("connection").textContent, "Not connected");
});
