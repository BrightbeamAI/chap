// The review desk. One page, served by the process that owns the store,
// the same in every template. It lists what waits for the reviewer, shows
// the artefact under review in the form its shape calls for, and sends
// decide.approve, decide.reject or decide.override as CHAP calls to
// POST /chap, each carrying the digest of the artefact shown, so a decision
// on a draft that changed under the reviewer is refused. Under
// security-signed/1.0 it signs in the browser with a key it generates there
// and keeps where no script can read it out (keystore.js), and signs only
// once the workspace holds that key for the reviewer, which it reads from
// the members the server lists. Activity reads the chain; Insights counts
// what happened.

import { contentHash, deepEqual, fingerprint, jsonPatch, makeClient, thumbprint } from "./chap-client.mjs";
import { browserKey, keepSigner, makePasskey, storedSigner } from "./keystore.js";
import { atLocalhost, isAddress, signingFor } from "./signing.js";
import { returnedSignIn, signInUrl } from "./login.js";
import { ago, artefactAnomalies, el, renderArtefact, renderDiff, renderJson, shapeOf, stateBadge, titleOf } from "./render.js";
import { parsePatch } from "./diff.js";
import { decisionsOf, outcomeOf, renderInsights, renderMarkdown } from "./insights.js";
import { followUpPrompt, proposeAgainCommand } from "./followup.js";

const $ = (id) => document.getElementById(id);
const POLL_MS = 4000;
const CHAIN_PAGE = 200;

const state = {
  config: null, client: null, tasks: [], reviews: [], selectedId: null, view: "review",
  editing: null, health: null, chain: { entries: [], from: null, total: 0, verified: null }, chainShown: -1,
  analytics: { report: false, refine: null, cases: false }, busy: false, lost: false,
  // The lists are brief; the task on show is read whole. A branch remembers
  // the commit on show and the commits opened.
  detail: null, range: { taskId: null, index: 0, read: new Set() }, reads: new Map(),
  // Comments on lines, by task, until they go with a decision; the reviews
  // already seen waiting, so a new one is brought forward.
  comments: new Map(), waitingSeen: null,
  // The review rounds acknowledged from this page, by task and the time the
  // round opened, and whether the coordinator has review.acknowledge at all.
  acked: new Set(), ackUnsupported: false,
  // The reviewer's signing key and whether the workspace holds it for them:
  // "unchecked" until the members are read, then "on record", "another key",
  // "no key" or "not joined"; "unsigned" where signatures are off. `refused`
  // says the workspace refused the key at the join because another key
  // holds the name.
  key: { signer: null, fingerprint: null, status: "unchecked", refused: false },
};

// -- helpers --------------------------------------------------------------------

const hashParams = () => new URLSearchParams(location.hash.slice(1));
function setHash(changes) {
  const p = hashParams();
  for (const [k, v] of Object.entries(changes)) { if (v === null || v === undefined || v === "") p.delete(k); else p.set(k, v); }
  const next = p.toString();
  if (next !== location.hash.slice(1)) history.replaceState(null, "", `#${next}`);
}
async function getJson(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} answered ${res.status}`);
  return res.json();
}
/** POST JSON to the server; resolves { ok, body }. */
async function postJson(path, body) {
  const res = await fetch(path, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  return { ok: res.ok, body: await res.json().catch(() => ({})) };
}
let toastTimer = null;
function toast(message, kind = "") {
  document.querySelector(".toast")?.remove();
  const t = el("div", { class: `toast ${kind}`, text: message });
  document.body.append(t);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.remove(), 4000);
}
function notice(message, kind = "") { const n = $("notice"); n.textContent = message; n.className = `notice ${kind}`; }
function chip(id, text, cls) { const c = $(id); c.className = `chip ${cls}`; c.lastElementChild.textContent = text; }

const selected = () => state.tasks.find((t) => t.task_id === state.selectedId) ?? null;

/** The whole view of the selected task, once read. */
const current = () => (state.detail?.id === state.selectedId ? state.detail.view : null);

/** What changes when a task does: its state, its last update, the submission under review, its decisions and acknowledgements. */
const detailKey = (t) => `${t.state}|${t.updated_at}|${t.submission?.seq ?? ""}|${(t.decision_log ?? []).length}|${(t.review?.acknowledgements ?? []).length}`;

/** Read the selected task whole. Returns the view it replaced, if it was of the same task. */
async function loadDetail(t) {
  const prior = state.detail?.id === t.task_id ? state.detail.view : null;
  const view = await getJson(`api/tasks/${encodeURIComponent(t.task_id)}`);
  if (state.selectedId !== t.task_id) return prior;
  state.detail = { id: t.task_id, key: detailKey(t), view };
  return prior;
}

/** When the current review round opened: the last time the task entered review_requested. */
const roundStart = (t) => (t.history ?? []).filter((h) => h.state === "review_requested").at(-1)?.ts ?? t.review?.requested_at ?? "";

/**
 * Whether a task waits for this reviewer: under review, addressed to them,
 * and not yet decided by them in this round. A rule that needs more than
 * one approval keeps the task under review after the first.
 */
const mine = (t) => t.state === "review_requested" && !!t.review?.requested_to?.includes(state.client?.from)
  && !decisionsOf(t).some((d) => d.reviewer === state.client?.from && String(d.ts) >= roundStart(t));

// -- boot -----------------------------------------------------------------------

async function boot() {
  const saved = localStorage.getItem("chap-desk-theme");
  if (saved) document.documentElement.dataset.theme = saved;
  $("theme").onclick = () => {
    const dark = matchMedia("(prefers-color-scheme: dark)").matches;
    const current = document.documentElement.dataset.theme || (dark ? "dark" : "light");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("chap-desk-theme", next);
  };
  // The text size: a step on the scale the root size is multiplied by,
  // kept in this browser for next time.
  const steps = [0.875, 1, 1.125, 1.25, 1.375, 1.5];
  let step = steps.indexOf(Number(localStorage.getItem("chap-desk-text") ?? 1));
  if (step < 0) step = 1;
  const applyText = (say) => {
    document.documentElement.style.setProperty("--scale", String(steps[step]));
    $("text-smaller").disabled = step === 0;
    $("text-larger").disabled = step === steps.length - 1;
    if (say) { localStorage.setItem("chap-desk-text", String(steps[step])); toast(`Text size ${Math.round(steps[step] * 100)}%`); }
  };
  applyText(false);
  $("text-smaller").onclick = () => { step = Math.max(0, step - 1); applyText(true); };
  $("text-larger").onclick = () => { step = Math.min(steps.length - 1, step + 1); applyText(true); };
  $("help-btn").onclick = () => { $("help").hidden = false; };
  $("help-close").onclick = () => { $("help").hidden = true; };
  $("help").onclick = (e) => { if (e.target === $("help")) $("help").hidden = true; };
  for (const tab of document.querySelectorAll(".tab")) tab.onclick = () => showView(tab.dataset.view);
  $("search").oninput = renderQueue;
  $("act-filter").oninput = renderActivity;
  $("act-refused").onchange = renderActivity;
  $("act-more").onclick = () => loadChain({ earlier: true });
  $("approve").onclick = () => decide("approve");
  $("changes").onclick = () => decide("changes");
  $("reject").onclick = () => decide("reject");
  $("override").onclick = () => decide("override");
  $("r-edit").onclick = toggleEdit;
  $("cancel-edit").onclick = () => { state.editing = null; renderReview(); };
  $("prompt-copy").onclick = () => copyText($("prompt-text").textContent);
  $("prompt-close").onclick = () => { $("prompt").hidden = true; };
  $("r-prompt-copy").onclick = () => copyText($("r-prompt").textContent);
  document.addEventListener("keydown", onKey);

  state.config = await getJson("api/config");
  // Back from the provider: the page's address loses the code at once, and
  // the server redeems it for the ID token the join presents.
  const back = returnedSignIn();
  if (back) {
    history.replaceState(null, "", `${location.pathname}${back.hash}`);
    state.signIn = back.code ? await redeemSignIn(back) : back;
  }
  $("workspace").textContent = state.config.workspace;
  const short = state.config.profiles.map((p) => p.replace(/\/1\.0$/, "").replace("security-signed", "signed").replace("audit-scitt", "scitt"));
  $("profiles").replaceChildren(el("span", { class: "chip profiles", title: state.config.profiles.join(", "), text: short.join(" · ") }));
  chip("chain", state.config.chain_enabled ? "chain on" : "chain off", state.config.chain_enabled ? "good" : "");
  // Each reviewer by name, and by URI where two share a name; the URI is
  // in the tooltip.
  const select = $("reviewer");
  const humans = state.config.humans;
  const shared = (name) => humans.filter((h) => h.display_name === name).length > 1;
  for (const h of humans) select.append(el("option", { value: h.uri, title: h.uri, text: h.display_name ? (shared(h.display_name) ? `${h.display_name} (${h.uri})` : h.display_name) : h.uri }));
  const wanted = state.signIn?.uri ?? hashParams().get("reviewer");
  if (wanted && humans.some((h) => h.uri === wanted)) select.value = wanted;
  const titleSelect = () => { select.title = `Deciding as ${select.value}`; };
  titleSelect();
  select.onchange = () => { titleSelect(); return setReviewer(select.value); };
  await setReviewer(select.value);
  const task = hashParams().get("task");
  if (task) select_(task);
  showView(hashParams().get("view") || "review");
  $("signing").onclick = () => { $("key").hidden = false; };
  $("key-close").onclick = () => { $("key").hidden = true; };
  $("setup-action").onclick = () => makeKey();
  $("setup-close").onclick = () => { localStorage.setItem(`chap-desk-browser-key-note:${state.config.workspace}`, "seen"); showSetup(); };
  setInterval(() => { if (!document.hidden && !state.busy) refresh().catch(lost); }, POLL_MS);
  probeAnalytics();
  setInterval(() => { if (!document.hidden) probeAnalytics(); }, 30_000);
}

// Under security-signed/1.0 the desk signs what it sends in the reviewer's
// name. It signs with a passkey or with a key it makes and keeps in the
// browser, as desk_signing and the browser allow (signing.js), and
// registers the public key at participant.join, which the coordinator
// accepts unsigned. Whoever joins first under a URI holds it: a later join
// that brings another key is refused with -32011, and the desk then signs
// nothing and says so. The desk joins only while the workspace does not
// hold its key, and reads that from the members the server lists, with no
// signed call.
async function setReviewer(uri) {
  setHash({ reviewer: uri });
  let signer = null;
  let setup = null;
  if (state.config.require_signatures) {
    const ws = state.config.workspace;
    signer = await storedSigner(ws, uri);
    // A key a desk from before 0.3.5 left in localStorage is moved first,
    // whatever this desk would make now: it may be the key on record.
    if (!signer && localStorage.getItem(`chap-desk-key:${ws}:${uri}`)) {
      signer = (await browserKey(ws, uri)).signer;
      toast("Your signing key was in this browser's local storage, where any page on this address could read it. It is now kept where no page can read it out.", "ok");
    }
    const want = await signingFor(state.config.desk_signing, location);
    state.passkeyHere = want === "passkey";
    if (!signer) {
      if (want === "browser-key") signer = (await browserKey(ws, uri)).signer;
      else setup = { need: want };
    } else if (signer.scheme === "ed25519" && state.config.desk_signing === "webauthn") {
      setup = { need: want === "passkey" ? "move" : want };
    }
  }
  await useSigner(uri, signer, setup);
}

/** Sign as `uri` with `signer`, joining where the workspace does not hold its key; `setup` is what the reviewer must do first. */
async function useSigner(uri, signer, setup = null) {
  state.client = makeClient({ url: "chap", workspace: state.config.workspace, from: uri, signer });
  state.key = { signer, fingerprint: signer ? await fingerprint(signer.publicJwk) : null, status: !state.config.require_signatures ? "unsigned" : signer ? "unchecked" : "setup", refused: false, setup };
  if (signer) {
    await checkKey();
    if (state.key.status !== "on record" && !setup) {
      if (state.config.oidc) await joinSignedIn();
      else await join();
    }
  }
  if (setup?.need === "localhost") location.replace(atLocalhost(location));
  showKey();
  state.chainShown = -1;
  await refresh().catch(lost);
}

/** The configured person a URI names, for the role and name a join gives. */
const humanOf = (uri) => state.config.humans.find((h) => h.uri === uri);

/**
 * Make a passkey for the reviewer, on their click, and sign with it. Where
 * the workspace holds the browser key this desk kept for them before, the
 * name is moved to the passkey with participant.rotate_key, which that key
 * signs, and the browser key is given up once the move is accepted.
 */
async function makeKey() {
  const uri = state.client.from;
  const ws = state.config.workspace;
  const before = state.key.signer;
  // Where people sign in, the key this browser signs with is bound by the
  // sign-in: the one held, or a passkey made now, in this click.
  if (state.key.setup?.need === "sign in" && before) { await startSignIn(before); return; }
  let passkey;
  try {
    passkey = await makePasskey(ws, uri, { displayName: humanOf(uri)?.display_name, platform: state.config.desk_signing === "auto" });
  } catch (e) {
    notice(e.name === "NotAllowedError" ? "No passkey was made: the browser's check was cancelled, or timed out." : `No passkey was made: ${e.message}`, "bad");
    return;
  }
  if (before && state.key.status === "on record") {
    try {
      await state.client.call("participant.rotate_key", { old_kid: before.kid, new_jwk: passkey.publicJwk });
    } catch (e) {
      notice(`Your name stays with the key this browser kept: the move to the passkey was refused, ${e.message}`, "bad");
      return;
    }
    toast("Your name is held by the passkey now; the key this browser kept is given up.", "ok");
  }
  await keepSigner(ws, uri, passkey);
  if (state.config.oidc && !(before && state.key.status === "on record")) {
    state.client = makeClient({ url: "chap", workspace: ws, from: uri, signer: passkey });
    await startSignIn(passkey);
    return;
  }
  await useSigner(uri, passkey);
}

/**
 * Join the reviewer with this browser's key, and with `token`, an ID token,
 * where people sign in. A refusal because the name is held by keys the
 * member brought before, or by a member who joined with none and so takes
 * none later (-32011), leaves the desk signing nothing; a token refused
 * (-32403, -32404) is said in the sign-in banner; any other refusal is
 * shown, and the join is tried again when the reviewer is chosen again.
 */
async function join(token = null) {
  const uri = state.client.from;
  const me = humanOf(uri);
  try {
    await state.client.call("participant.join", { type: "human", role: me?.role ?? "reviewer", display_name: me?.display_name, jwks: { keys: [state.key.signer.publicJwk] }, ...(token ? { oidc_token: token } : {}) });
  } catch (e) {
    if (e.code === -32011) {
      state.key.refused = true;
      await checkKey();
      if (state.key.status !== "no key") state.key.status = "another key";
      return;
    }
    if (token && (e.code === -32403 || e.code === -32404)) { state.key.setup = { need: "sign in", error: `The workspace refused the sign-in: ${e.message}` }; return; }
    toast(`Could not join as ${uri}: ${e.message}`, "bad");
  }
  await checkKey();
}

/**
 * Where people sign in at a provider, a join presents an ID token for this
 * reviewer whose nonce names this browser's key. One the browser came back
 * with is presented; without one, the desk asks the reviewer to sign in,
 * which also binds this browser's key where the workspace holds another for
 * them from a sign-in elsewhere.
 */
async function joinSignedIn() {
  const uri = state.client.from;
  const back = state.signIn;
  state.signIn = null;
  if (back?.token && back.uri === uri && back.nonce === (await thumbprint(state.key.signer.publicJwk))) {
    if (back.as !== uri) { state.key.setup = { need: "sign in", error: `You signed in as ${back.as}, and the desk decides as ${uri}: sign in as ${uri}, or choose ${back.as} above.` }; return; }
    await join(back.token);
    return;
  }
  state.key.setup = { need: "sign in", error: back?.uri === uri ? back.error ?? null : null };
}

/** Redeem the code a sign-in came back with, through the server: { uri, token, nonce, as }, or { uri, error }. */
async function redeemSignIn(back) {
  const { ok, body } = await postJson("api/oidc/token", { code: back.code, code_verifier: back.verifier, redirect_uri: back.redirect_uri, nonce: back.nonce });
  return ok ? { uri: back.uri, token: body.id_token, nonce: back.nonce, as: body.as } : { uri: back.uri, error: body.error ?? "The server could not redeem the sign-in's code." };
}

/** Send the browser to the provider to sign the reviewer in, for the key `signer` holds. */
async function startSignIn(signer) {
  const uri = state.client.from;
  const oidc = state.config.oidc;
  const loginHint = oidc.development ? uri : humanOf(uri)?.email ?? null;
  location.assign(await signInUrl({ oidc, uri, signer, loginHint }));
}

/**
 * The codes a signed call is refused with when the workspace cannot verify
 * it under a key it holds for the sender: -32070 when the signature does not
 * verify under the key its kid names, -32071 when the sender holds no key of
 * that kid, or none any longer, and -32072 when the key is revoked.
 */
const KEY_REFUSALS = new Set([-32070, -32071, -32072]);

/** Whether two public keys are one key. */
const sameKey = (a, b) => !!a && !!b && a.kty === b.kty && a.crv === b.crv && a.x === b.x && (a.y ?? null) === (b.y ?? null);

/**
 * Whether the workspace holds this browser's key for the reviewer, from the
 * members and keys the server lists: "on record"; "another key" where the
 * member signs with other keys; "no key" where it holds none now, revoked
 * or rotated away; or "not joined". A read that fails leaves the question
 * open, and the next poll asks again.
 */
async function checkKey() {
  const { signer } = state.key;
  if (!signer) return;
  try {
    const { members } = await getJson("api/members");
    const me = members.find((m) => m.uri === state.client.from);
    state.key.status = !me ? "not joined" : me.keys.some((k) => sameKey(k, signer.publicJwk)) ? "on record" : me.keys.length ? "another key" : "no key";
  } catch { /* asked again on the next poll */ }
}

/** Whether decisions from this browser would count: signatures are off, or the workspace holds this key and the desk has nothing left to set up. */
const canSign = () => state.key.status === "unsigned" || (state.key.status === "on record" && !state.key.setup);

/** Whether the browser key is what this desk signs with under desk_signing "auto": the browser had no passkey to offer. */
const browserKeyUnderAuto = () => state.config.desk_signing === "auto" && state.key.signer?.scheme === "ed25519";

/** The signing chip, the key dialog, the banner when another key holds the reviewer's name, and what the reviewer must set up. */
function showKey() {
  const { signer, fingerprint: print, status } = state.key;
  const uri = state.client.from;
  const kind = signer?.scheme === "webauthn" ? "passkey" : "browser key";
  if (!state.config.require_signatures) {
    chip("signing", "unsigned", "");
    $("signing").title = "This workspace does not require signatures";
  } else if (!signer) {
    chip("signing", "no key yet", "bad");
    $("signing").title = `Nothing signs for ${uri} in this browser yet`;
  } else if (status === "another key") {
    chip("signing", "not signing", "bad");
    $("signing").title = `Another key holds ${uri} in this workspace, so this browser signs nothing`;
  } else if (status === "not joined" || status === "no key") {
    chip("signing", status, "bad");
    $("signing").title = status === "no key" ? `The workspace holds no key for ${uri} now, so this browser signs nothing` : `${uri} has not joined this workspace with this browser's key`;
  } else {
    chip("signing", `${kind} · ${print.slice(7, 15)}`, status !== "on record" ? "" : browserKeyUnderAuto() ? "warn" : "good");
    $("signing").title = `Decisions are signed in this browser as ${uri}, with the ${kind} whose fingerprint is ${print}`;
  }
  $("key-uri").textContent = uri;
  $("key-kind").textContent = !signer ? "none yet" : signer.scheme === "webauthn"
    ? "A passkey: the browser's authenticator signs, and asks for your fingerprint, face or PIN each time."
    : "A key the desk made and keeps in this browser, which signs without asking you.";
  $("key-fingerprint").textContent = print ?? (state.config.require_signatures ? "none yet" : "none: this workspace does not require signatures");
  $("key-id").textContent = signer?.kid ?? "none";
  $("key-status").textContent = !state.config.require_signatures ? "Decisions from this browser are not signed." : !signer ? "Not set up yet." : status === "on record"
    ? `The workspace holds this key for ${uri}, so decisions signed here count.`
    : status === "another key" ? `The workspace holds another key for ${uri}, so decisions signed here would not count.`
      : status === "not joined" ? `${uri} has not joined the workspace with this key, so decisions signed here would not count.`
        : status === "no key" ? `The workspace holds no key for ${uri} now, so decisions signed here would not count.`
          : "Not checked with the workspace yet.";
  // Where people sign in, another key on record for the reviewer is one a
  // sign-in elsewhere bound, and signing in here binds this browser's: the
  // sign-in banner says so.
  $("key-alert").hidden = !(status === "no key" || (status === "another key" && (!state.config.oidc || state.key.refused)));
  if (status === "another key") {
    const how = state.key.refused ? `The workspace refused this browser's key when the desk joined you, because another key already holds ${uri} in ${state.config.workspace}.` : `Another key holds ${uri} in ${state.config.workspace}.`;
    $("key-alert-title").textContent = "This browser's key is not the reviewer's.";
    // Desks before 0.3.5 were opened at 127.0.0.1, and the browser keeps a
    // key made there for that address alone.
    const before = location.hostname === "localhost" ? ` Desks before 0.3.5 were opened at http://127.0.0.1${location.port ? `:${location.port}` : ""}/, and this browser keeps a key it made there for that address alone: if you decided there, open the desk there.` : "";
    $("key-alert-text").textContent = `${how} Decisions signed in this browser would not count, so the desk sends none. If that key is yours, in another browser or a profile since cleared, decide there.${before} If that key is not yours, someone joined under your name first: whoever runs the workspace must find out who, and a trust policy that pins your key keeps them out.`;
  } else if (status === "no key" && state.key.refused) {
    $("key-alert-title").textContent = "The workspace takes no key for you.";
    $("key-alert-text").textContent = `The workspace refused this browser's key when the desk joined you: ${uri} is a member of ${state.config.workspace} with no key, and a member with no key takes none later. Decisions signed in this browser would not count, so the desk sends none. If this workspace was made while signatures were off, it joined you that way, and a new store lets the desk join you with this key; if not, someone joined under your name first, and whoever runs the workspace must find out who.`;
  } else if (status === "no key") {
    $("key-alert-title").textContent = "The workspace holds no key for you now.";
    $("key-alert-text").textContent = `The key this browser signed with for ${uri} in ${state.config.workspace} was revoked or rotated away, so decisions signed here would not count, and the desk sends none. Whoever runs the workspace can say why.`;
  }
  showSetup();
  setDecisionButtons();
}

/** The setup banner: what the reviewer does before the desk can sign, or what the browser key cannot protect against. */
function showSetup() {
  const { setup, status } = state.key;
  const box = $("setup");
  const action = $("setup-action");
  const close = $("setup-close");
  action.hidden = true;
  close.hidden = true;
  box.className = "alert setup";
  const ws = state.config.workspace;
  const noteSeen = localStorage.getItem(`chap-desk-browser-key-note:${ws}`) === "seen";
  let text = null;
  if (setup?.need === "passkey") {
    text = `Your decisions here are signed with a passkey, which asks for your fingerprint, face or PIN each time, so nothing signs in your name without you. ${state.config.oidc ? `Make one, and sign in at ${state.config.oidc.issuer} to bind it to you.` : "Make one to start."}`;
    action.textContent = state.config.oidc ? "Make a passkey and sign in" : "Make a passkey";
    action.hidden = false;
  } else if (setup?.need === "sign in") {
    const name = humanOf(state.client.from)?.display_name ?? state.client.from;
    text = `${setup.error ? `${setup.error} ` : ""}Sign in at ${state.config.oidc.issuer} to decide as ${name}: the desk joins you with your sign-in, which binds the key this browser signs with to you.`;
    if (setup.error) box.className = "alert setup bad";
    action.textContent = "Sign in";
    action.hidden = false;
  } else if (setup?.need === "move") {
    text = "This desk signs with a passkey, and the workspace holds the key this browser kept for you before. Make a passkey, and the desk moves your name to it with a request that key signs.";
    action.textContent = "Move to a passkey";
    action.hidden = status !== "on record";
  } else if (setup?.need === "localhost") {
    text = `A passkey is made for a name, never an IP address, so the desk opens at ${atLocalhost(location)}.`;
  } else if (setup?.need === "no passkey") {
    text = "This desk signs with a passkey, and this browser can make none here. Open the desk by its name in a browser that offers passkeys or a security key.";
    box.className = "alert setup bad";
  } else if (browserKeyUnderAuto() && !noteSeen) {
    const risk = "A program running as you on this machine, a coding agent with a shell among them, can have the browser sign with that key, or copy it from the browser's files, and so approve in your name.";
    if (state.passkeyHere && status === "on record") {
      text = `The desk signs with a key it kept in this browser before. ${risk} This browser can make a passkey, which asks for your fingerprint, face or PIN each time: move your name to it.`;
      action.textContent = "Move to a passkey";
      action.hidden = false;
    } else if (isAddress(location.hostname)) {
      text = `The desk is open at an IP address, where no passkey can sign, so it signs with the key it kept in this browser for this address. ${risk}`;
    } else {
      text = `This browser has no passkey with a fingerprint, face or PIN check, so the desk signs with a key it keeps in the browser. ${risk} A passkey asks you each time.`;
    }
    box.className = "alert setup warn";
    close.hidden = false;
  }
  box.hidden = !text;
  $("setup-text").textContent = text ?? "";
}

function lost(e) {
  state.lost = true;
  chip("connection", "connection lost", "bad");
  console.warn(e);
}

// -- data -----------------------------------------------------------------------

async function refresh() {
  if (state.key.signer && state.key.status !== "on record") { await checkKey(); showKey(); }
  // Every open task, and the most recent ones for the decided list.
  const [health, open, recent] = await Promise.all([getJson("api/health?desk=1"), getJson("api/tasks?state=created,in_progress,review_requested&brief=1"), getJson("api/tasks?limit=200&brief=1")]);
  if (state.lost) { state.lost = false; toast("Connected again", "ok"); }
  chip("connection", "live", "good");
  $("connection").title = `${health.members} members, ${health.tasks} tasks, ${health.audit} chain entries`;
  state.health = health;
  const before = selected();
  const byId = new Map();
  for (const t of [...open.tasks, ...recent.tasks]) byId.set(t.task_id, t);
  state.tasks = [...byId.values()].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? ""));
  renderQueue();
  bringForward();
  const now = selected();
  if (before && !now) { state.selectedId = null; state.detail = null; state.editing = null; renderReview(); }
  else if (now && detailKey(now) !== state.detail?.key) {
    const prior = await loadDetail(now);
    const fresh = current();
    if (prior && fresh && prior.state === fresh.state && !deepEqual(prior.artefact, fresh.artefact)) {
      if (state.editing) notice("The artefact changed while you were editing it; your edit is kept, the decision binds to the new artefact.", "bad");
      toast("The artefact under review changed; this is the current one.");
    }
    renderReview();
  }
  if (health.audit !== state.chainShown) {
    state.chain.total = health.audit;
    $("activity-count").textContent = String(health.audit);
    if (state.view === "activity") await loadChain();
    if (state.config.chain_enabled) await verifyChain();
    state.chainShown = health.audit;
  }
  if (state.view === "insights") renderInsightsView();
}

/**
 * A review that has just arrived for this reviewer is brought forward: it
 * opens when nothing waiting is open, and is announced otherwise. The tab's
 * title counts what waits.
 */
function bringForward() {
  const waiting = state.tasks.filter((t) => mine(t));
  document.title = waiting.length ? `(${waiting.length}) CHAP review desk` : "CHAP review desk";
  const ids = new Set(waiting.map((t) => t.task_id));
  const first = state.waitingSeen === null;
  const arrived = first ? [] : waiting.filter((t) => !state.waitingSeen.has(t.task_id));
  state.waitingSeen = ids;
  const current = selected();
  const busy = current && mine(current);
  if (first) {
    if (!state.selectedId && waiting.length) select_(waiting.at(-1).task_id);
    return;
  }
  if (!arrived.length) return;
  const next = arrived.at(-1);
  if (!busy) { select_(next.task_id); toast(`Waiting for your review: ${titleOf(next)}`, "ok"); }
  else if (next.task_id !== state.selectedId) toast(`Also waiting for your review: ${titleOf(next)}. It is in the queue.`);
}

async function loadChain({ earlier = false } = {}) {
  const total = state.health?.audit ?? state.chain.total;
  const from = earlier ? Math.max(0, (state.chain.from ?? total) - CHAIN_PAGE) : Math.max(0, total - CHAIN_PAGE);
  const to = earlier ? state.chain.from : total;
  try {
    const { entries } = await getJson(`api/chain?from_seq=${from}${to !== undefined && to !== null && to < total ? `&to_seq=${to}` : ""}`);
    if (earlier) state.chain.entries = [...entries, ...state.chain.entries];
    else {
      // Keep the earlier pages already loaded, replace the tail.
      const kept = state.chain.entries.filter((e) => e.seq < from);
      state.chain.entries = [...kept, ...entries];
    }
    state.chain.from = Math.min(from, state.chain.from ?? from);
    renderActivity();
  } catch (e) {
    $("activity").replaceChildren(el("div", { class: "empty", text: `The chain could not be read: ${e.message}` }));
  }
}

/** The chain, as the coordinator verifies it; a broken chain is the coordinator's refusal, with the reason. */
async function verifyChain() {
  try {
    const { result: v, error } = await getJson("api/chain/verify");
    if (error) { state.chain.verified = false; chip("chain", "chain broken", "bad"); $("chain").title = error.message; return; }
    state.chain.verified = v.status === "verified" && v.ok === true;
    chip("chain", state.chain.verified ? `chain verified · ${v.entries_checked}` : `chain ${v.status}`, state.chain.verified ? "good" : "bad");
  } catch { state.chain.verified = false; chip("chain", "chain not verified", "bad"); }
}

// The pages chap-analytics writes, when the analytics script has run:
// summary.json says when and from how much, and the rest are linked.
async function probeAnalytics() {
  let summary = null, refine = null;
  try { const r = await fetch("analytics/summary.json"); summary = r.ok ? await r.json() : null; } catch { /* none */ }
  if (summary) { try { const r = await fetch("analytics/refine.md"); refine = r.ok ? await r.text() : null; } catch { /* none */ } }
  const changed = summary?.written !== state.analytics.summary?.written;
  state.analytics = { report: !!summary, cases: !!summary, summary, refine };
  if (changed && state.view === "insights") renderInsightsView();
}

// -- the queue ------------------------------------------------------------------

function renderQueue() {
  const q = $("queue");
  const needle = $("search").value.trim().toLowerCase();
  const match = (t) => !needle || [titleOf(t), t.task_id, t.assignee, t.kind, t.state].join(" ").toLowerCase().includes(needle);
  const waiting = state.tasks.filter((t) => mine(t) && match(t));
  const open = state.tasks.filter((t) => !mine(t) && ["created", "in_progress", "review_requested"].includes(t.state) && match(t));
  const decided = state.tasks.filter((t) => ["completed", "declined", "cancelled", "superseded"].includes(t.state) && match(t)).slice(0, 60);
  q.replaceChildren();
  const group = (title, list, empty) => {
    q.append(el("h3", {}, title, el("span", { class: "count", text: String(list.length) })));
    if (!list.length) { q.append(el("div", { class: "empty", text: empty })); return; }
    for (const t of list) {
      const o = outcomeOf(t);
      const approvals = t.state === "review_requested" ? (t.review?.decisions ?? []).filter((d) => d.kind === "approve").length : 0;
      q.append(el("button", { class: `item${t.task_id === state.selectedId ? " selected" : ""}`, onclick: () => select_(t.task_id, { byUser: true }), dataset: { id: t.task_id } },
        el("div", { class: "title" }, el("span", { text: titleOf(t) })),
        el("div", { class: "meta" },
          el("span", { text: t.assignee?.replace(/^agent:/, "") ?? "" }),
          el("span", { text: t.kind }),
          el("span", { text: ago(t.updated_at ?? t.created_at) }),
          approvals ? el("span", { text: `${approvals} approval${approvals === 1 ? "" : "s"}, ${t.review.rule}` }) : null,
          !o ? el("span", { class: "state" }, stateBadge(t.state)) : el("span", { class: "state" }, el("span", { class: `badge ${o === "reject" ? "bad" : o === "override" ? "warn" : "ok"}`, text: o === "override" ? "edited" : o === "reject" ? "rejected" : "approved" })))));
    }
  };
  group("Waiting for you", waiting, "Nothing is waiting for you.");
  group("In progress", open, "No other task is open.");
  group("Decided", decided, "Nothing has been decided yet.");
}

/**
 * Open a task. `byUser` says the reviewer opened it themselves, from the
 * queue or with j and k; otherwise the desk did, for a review that arrived.
 */
function select_(id, { byUser = false } = {}) {
  state.selectedId = id;
  state.editing = null;
  if (state.range.taskId !== id) {
    if (!state.reads.has(id)) state.reads.set(id, new Set());
    state.range = { taskId: id, index: 0, read: state.reads.get(id) };
  }
  setHash({ task: id });
  renderQueue();
  renderReview();
  if (state.view !== "review") showView("review");
  const t = selected();
  if (t && state.detail?.id !== id) loadDetail(t).then(() => { if (state.selectedId === id) renderReview(); }).catch(lost);
  acknowledge(t, { byUser });
}

/**
 * Tell the workspace the reviewer has begun a review (review.acknowledge),
 * the first time it is opened for them in a round, so the record shows when
 * they took it up as well as when they decided it. The call is signed like
 * any the reviewer makes; a passkey asks for each signature, so with one
 * the desk acknowledges only a review the reviewer opened themselves. An
 * acknowledgement on record for the reviewer, one already tried from this
 * page, and a coordinator without the method (-32601) leave it be; so does
 * any other refusal, which says the review is not theirs to take up.
 */
async function acknowledge(t, { byUser = false } = {}) {
  if (!t || !mine(t) || state.ackUnsupported || !canSign() || document.hidden) return;
  if (state.key.signer?.scheme === "webauthn" && !byUser) return;
  const round = `${t.task_id} ${t.review.requested_at}`;
  if (state.acked.has(round) || (t.review.acknowledgements ?? []).some((a) => a.reviewer === state.client.from)) return;
  state.acked.add(round);
  try {
    await state.client.call("review.acknowledge", { task_id: t.task_id });
    if (state.selectedId === t.task_id) { await loadDetail(t); renderReview(); }
  } catch (e) {
    if (e.code === -32601) state.ackUnsupported = true;
  }
}

/** Open another commit of the branch on show. */
function selectCommit(i) {
  const t = current();
  const commits = t?.artefact?.commits;
  if (!Array.isArray(commits) || i < 0 || i >= commits.length) return;
  state.range.index = i;
  renderReview();
  document.querySelector(".commit-detail")?.scrollIntoView({ block: "start", behavior: "smooth" });
}

// -- the review -----------------------------------------------------------------

function renderReview() {
  const t = current() ?? selected();
  $("review-empty").hidden = !!t;
  $("review").hidden = !t;
  $("decision-bar").hidden = !(t && mine(t));
  if (!t) return;
  const loading = !current();
  const artefact = t.artefact ?? t.output ?? null;
  $("r-title").textContent = titleOf(t);
  $("r-state").replaceWith(Object.assign(stateBadge(t.state), { id: "r-state" }));
  $("r-kind").textContent = t.kind;
  // A branch's own header says what it is now; the task's input is its first proposal.
  $("r-input-card").hidden = t.kind === "commit_range";
  const approvalsSoFar = t.state === "review_requested" && t.review?.rule && t.review.rule !== "any_one_approves"
    ? `${(t.review.decisions ?? []).filter((d) => d.kind === "approve").length} so far` : null;
  const named = (uri) => state.config?.humans?.find((h) => h.uri === uri)?.display_name ?? uri;
  const seen = t.state === "review_requested" ? (t.review?.acknowledgements ?? []).map((a) => named(a.reviewer)).join(", ") : "";
  const facts = [["agent", t.assignee], ["reviewers", t.review?.requested_to?.join(", ")], ["rule", t.review?.rule], ["approvals", approvalsSoFar], ["opened", t.review?.requested_at ? `${ago(t.review.requested_at)} ago` : null], ["acknowledged by", seen || null], ["mode", t.mode], ["task", t.task_id]];
  $("r-facts").replaceChildren(...facts.filter(([, v]) => v).map(([k, v]) => el("span", {}, `${k} `, el("b", { text: v }))));
  const input = t.input ?? {};
  const inputBody = $("r-input");
  const plain = (v) => typeof v === "string" || typeof v === "number" || typeof v === "boolean" || (Array.isArray(v) && v.every((x) => typeof x === "string" || typeof x === "number"));
  const simple = Object.entries(input).filter(([, v]) => plain(v));
  const rest = Object.fromEntries(Object.entries(input).filter(([, v]) => !plain(v)));
  inputBody.replaceChildren(el("div", { class: "stack" },
    simple.length ? el("dl", { class: "kv" }, simple.map(([k, v]) => [el("dt", { text: k }), el("dd", { text: Array.isArray(v) ? v.join(", ") : String(v) })])) : null,
    Object.keys(rest).length ? renderJson(rest) : null,
    !simple.length && !Object.keys(rest).length ? el("span", { class: "muted small", text: "No input." }) : null,
  ));
  $("r-artefact-title").textContent = t.state === "review_requested" ? "The artefact under review" : t.state === "completed" ? "The artefact as decided" : "The artefact";
  // Under a rule that needs more than one approval, an edit would settle the
  // review with one reviewer's decision, so the desk offers no edit there:
  // request changes, and the edited version is approved again by everyone.
  const multi = multiRule(t);
  const branch = shapeOf(artefact) === "commits";
  const blocked = !loading && anomaliesOf(t).length > 0;
  $("r-edit").hidden = !(mine(t) && artefact !== null && !multi && !blocked && !branch && !loading);
  $("r-edit-hint").hidden = !(mine(t) && (multi || blocked || branch));
  $("r-edit-hint").textContent = blocked ? (branch ? "A commit of this branch cannot be shown faithfully, so the branch cannot be approved here." : "This patch cannot be shown faithfully, so it cannot be approved or edited here.")
    : branch ? "A branch is approved as its commits stand. Request changes, and the agent amends them."
    : multi ? `Under ${t.review.rule} an edit would settle the review alone. Request changes, and the agent revises.` : "";
  setDecisionButtons();
  $("r-edit").textContent = state.editing ? "Editing" : "Edit";
  $("override").hidden = !state.editing;
  $("cancel-edit").hidden = !state.editing;
  if (branch && !loading) {
    const commits = artefact.commits;
    state.range.index = Math.min(state.range.index, commits.length - 1);
    state.range.read.add(commits[state.range.index].sha);
  }
  const body = $("r-artefact");
  const commenting = mine(t) && !loading && !state.editing ? commentingFor(t.task_id) : null;
  body.replaceChildren(artefact === null ? el("span", { class: "muted small", text: "Nothing submitted yet." })
    : loading ? el("span", { class: "muted small", text: "Reading the artefact." })
    : renderArtefact(artefact, { editing: state.editing, commenting, range: branch ? { index: state.range.index, read: state.range.read, onSelect: selectCommit } : null }));
  renderContext(t, artefact, loading);
  countComments();
  const history = $("r-history");
  const events = [];
  for (const h of t.history ?? []) events.push({ ts: h.ts, who: h.from, what: h.state.replace(/_/g, " "), note: h.note });
  for (const d of decisionsOf(t)) events.push({ ts: d.ts, who: d.reviewer, what: d.kind === "override" ? "approved with an edit" : d.kind === "reject" ? (d.request_revision ? "sent back for a revision" : "rejected") : d.kind === "approve" ? "approved" : d.kind, note: d.rationale ?? d.comment, tags: d.tags });
  for (const a of t.review?.acknowledgements ?? []) events.push({ ts: a.ts, who: a.reviewer, what: "acknowledged" });
  events.sort((a, b) => a.ts.localeCompare(b.ts));
  history.replaceChildren(...events.map((e) => el("div", { class: "event" }, el("span", { class: "when", title: e.ts, text: `${ago(e.ts)} ago` }), el("span", {}, el("b", { text: e.what }), e.who ? el("span", { class: "muted", text: ` by ${e.who}` }) : null, e.note ? el("div", { class: "note", text: e.note }) : null, e.tags?.length ? el("div", { class: "small muted", text: e.tags.join(", ") }) : null))));
  $("r-history-card").hidden = !events.length;
}

/** The comments on lines of a task, kept until they go with a decision. */
function commentsOf(id) {
  if (!state.comments.has(id)) state.comments.set(id, []);
  return state.comments.get(id);
}

/** What the diff needs to take comments on its lines for a task. */
function commentingFor(id) {
  return {
    comments: commentsOf(id),
    onAdd: (c) => { const saved = { ...c, id: `c${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}` }; commentsOf(id).push(saved); countComments(); return saved; },
    onRemove: (cid) => { const list = commentsOf(id); const i = list.findIndex((x) => x.id === cid); if (i >= 0) list.splice(i, 1); countComments(); },
  };
}

/** Say how many comments go with the decision. */
function countComments() {
  const t = current();
  const n = t ? commentsOf(t.task_id).length : 0;
  $("comment-count").textContent = n ? `${n} comment${n === 1 ? "" : "s"} on lines go with your decision` : "";
}

/** The prompt the last decision on a task makes for the agent, or null. */
function promptFor(t) {
  const decision = (t.decision_log ?? []).at(-1);
  if (!decision) return null;
  const name = state.config?.humans?.find((h) => h.uri === decision.reviewer)?.display_name ?? decision.reviewer;
  return followUpPrompt({ task: t, decision, reviewer: name, command: proposeAgainCommand(t, state.config?.gate_dir) });
}

/** The agent's own note, what changed since the last look, and the decision as a prompt. */
function renderContext(t, artefact, loading) {
  const note = !loading && typeof artefact?.context === "string" ? artefact.context : null;
  $("r-context-card").hidden = !note;
  if (note) {
    $("r-context-title").textContent = `Context from ${artefact.drafted_by ?? t.assignee ?? "the agent"}`;
    const box = $("r-context");
    box.innerHTML = renderMarkdown(note);
  }
  const since = !loading && typeof artefact?.since?.patch === "string" && artefact.since.patch.trim() ? artefact.since : null;
  $("r-since-card").hidden = !since;
  if (since) $("r-since").replaceChildren(el("div", { class: "small muted since-note", text: `What changed since the version you reviewed${since.head ? ` (up to ${String(since.head).slice(0, 7)})` : ""}.` }), renderDiff(since.patch, {}));
  // The prompt of a decision that closed the round; a new round waiting has none yet.
  const prompt = !loading && t.state !== "review_requested" ? promptFor(t) : null;
  $("r-prompt-card").hidden = !prompt;
  if (prompt) $("r-prompt").textContent = prompt;
}

/** Show the prompt a decision makes, to copy and give to the agent. */
function showPrompt(prompt) {
  $("prompt-text").textContent = prompt;
  $("prompt").hidden = false;
  $("prompt-copy").focus();
}

async function copyText(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied the prompt", "ok"); }
  catch {
    const range = document.createRange();
    range.selectNodeContents($("prompt-text"));
    const sel = getSelection(); sel.removeAllRanges(); sel.addRange(range);
    toast("Selected the prompt: copy it with the keyboard");
  }
}

/**
 * The decision buttons, enabled where a decision can be sent: the
 * workspace holds this browser's key, or signatures are off, and for an
 * approval the task on show is read whole and the desk can show its patch
 * faithfully.
 */
function setDecisionButtons() {
  const t = current();
  $("approve").disabled = !canSign() || !t || anomaliesOf(t).length > 0;
  for (const id of ["changes", "reject", "override"]) $(id).disabled = !canSign();
}

/** Whether a task's review needs more than one approval, so an edit would settle it alone. */
const multiRule = (t) => !!t.review?.rule && t.review.rule !== "any_one_approves";

/** What the desk cannot show faithfully of a code change or a branch. */
const anomaliesOf = (t) => artefactAnomalies(t.artefact);

function toggleEdit() {
  const t = current();
  if (!t || !mine(t)) return;
  if (shapeOf(t.artefact) === "commits") { notice("A branch is approved as its commits stand. Request changes, and the agent amends them.", "bad"); return; }
  if (multiRule(t)) { notice(`Under ${t.review.rule} an edit would settle the review alone. Request changes, and the agent revises.`, "bad"); return; }
  if (anomaliesOf(t).length) { notice("This patch cannot be shown faithfully, so it cannot be edited here.", "bad"); return; }
  state.editing = state.editing ? null : { state: {}, value: () => t.artefact };
  renderReview();
  if (state.editing) { notice("Edit the artefact, give a rationale, then approve your edit.", ""); $("comment").focus(); }
  else notice("");
}

const tags = () => $("tags").value.split(",").map((s) => s.trim()).filter(Boolean);

async function decide(kind) {
  const t = current();
  if (!t || !mine(t) || state.busy) return;
  if (!canSign()) {
    const why = { "another key": `Another key holds ${state.client.from} in this workspace`, "no key": `The workspace holds no key for ${state.client.from} now`, "not joined": `${state.client.from} has not joined with this browser's key` }[state.key.status];
    notice(why ? `${why}; the desk sends no decision signed here.` : "The desk has not yet confirmed with the workspace that it holds this browser's key.", "bad");
    return;
  }
  const comment = $("comment").value.trim();
  const artefact = t.artefact;
  // Every decision names the artefact it was made on and the submission that
  // opened this review round, so it counts in this round and no other.
  const round = t.submission?.envelope ? { round: await contentHash(t.submission.envelope) } : {};
  const lineComments = commentsOf(t.task_id).map(({ id, ...c }) => c);
  const base = { task_id: t.task_id, approved_artefact_digest: await contentHash(artefact), ...round, ...(tags().length ? { tags: tags() } : {}), ...(lineComments.length ? { comments: lineComments } : {}) };
  if ((kind === "approve" || kind === "override") && anomaliesOf(t).length) { notice("This patch cannot be shown faithfully. Reject it, or request changes.", "bad"); return; }
  if (kind === "override" && shapeOf(artefact) === "commits") { notice("A branch is approved as its commits stand. Request changes, and the agent amends them.", "bad"); return; }
  // An approval of a branch covers every commit in it: one not opened yet is
  // shown before the approval is sent.
  if (kind === "approve" && shapeOf(artefact) === "commits" && !(await confirmBranch(artefact))) return;
  if (kind === "override" && multiRule(t)) { notice(`Under ${t.review.rule} an edit would settle the review alone. Request changes, and the agent revises.`, "bad"); return; }
  let call;
  if (kind === "approve") call = ["decide.approve", { ...base, ...(comment ? { comment } : {}) }];
  else if (kind === "changes") {
    if (!comment && !lineComments.length) { notice("Say what to change, in the note or on the lines; the agent reads both.", "bad"); $("comment").focus(); return; }
    call = ["decide.reject", { ...base, ...(comment ? { comment } : {}), request_revision: true }];
  }
  else if (kind === "reject") { if (!comment) { notice("A rejection needs a reason.", "bad"); $("comment").focus(); return; } call = ["decide.reject", { ...base, comment }]; }
  else if (kind === "override") {
    if (!comment) { notice("An edit needs a rationale.", "bad"); $("comment").focus(); return; }
    let edited;
    try { edited = state.editing.value(); } catch (e) { notice(`The edit is not usable: ${e.message}`, "bad"); return; }
    const diff = jsonPatch(artefact, edited);
    if (!diff.length) { notice("Nothing changed. Approve as written, or edit the artefact.", "bad"); return; }
    // A code change is shown as it will be committed, and sent only once the
    // reviewer confirms it: the patch written from the edit is what they approve.
    if (shapeOf(edited) === "code" && !(await confirmEdit(edited))) return;
    call = ["decide.override", { ...base, diff, rationale: comment, intent_preserved: true }];
  }
  state.busy = true;
  for (const id of ["approve", "changes", "reject", "override"]) $(id).disabled = true;
  notice("");
  try {
    const r = await state.client.call(call[0], call[1]);
    const said = { approve: "Approved", changes: "Sent back for a revision", reject: "Rejected", override: "Approved with your edit" }[kind];
    toast(`${said}: ${titleOf(t)}${r.state ? ` (${r.state.replace(/_/g, " ")})` : ""}`, kind === "reject" ? "" : "ok");
    $("comment").value = ""; $("tags").value = "";
    state.comments.delete(t.task_id);
    state.editing = null;
    // The decision as a prompt for the agent, when it asks something of it.
    try {
      const decided = await getJson(`api/tasks/${encodeURIComponent(t.task_id)}`);
      const prompt = promptFor(decided);
      if (prompt) showPrompt(prompt);
    } catch { /* the review shows it once it is read again */ }
    await refresh();
    // Move on to the next task waiting, if there is one.
    const next = state.tasks.find((x) => mine(x));
    if (next) select_(next.task_id); else { state.selectedId = null; setHash({ task: null }); renderQueue(); renderReview(); }
  } catch (e) {
    // A passkey the reviewer did not confirm signs nothing, and nothing is sent.
    notice(e.name === "NotAllowedError" ? "Nothing was sent: the passkey check was cancelled, or timed out." : e.message, "bad");
    // A refusal of the signature itself: the workspace may no longer hold
    // this key, revoked or rotated away since it was last read.
    if (KEY_REFUSALS.has(e.code)) { await checkKey(); showKey(); }
  } finally {
    state.busy = false;
    setDecisionButtons();
  }
}

/**
 * Ask before an approval made with the a key, so a single key press, made
 * by mistake or taken by a page the desk sits under, never decides. Resolve
 * true to approve.
 */
function confirmKeyApproval(t) {
  return new Promise((resolve) => {
    const done = (ok) => { $("confirm").hidden = true; $("confirm-send").onclick = null; $("confirm-cancel").onclick = null; resolve(ok); };
    $("confirm-title").textContent = "Approve as written?";
    $("confirm-cancel").textContent = "Back";
    $("confirm-send").textContent = "Approve";
    $("confirm-send").disabled = false;
    $("confirm-body").replaceChildren(el("p", { text: `${titleOf(t)}: your approval is signed and sent for the artefact as it stands.` }));
    $("confirm").hidden = false;
    $("confirm-send").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
    $("confirm-send").focus();
  });
}

/** The a key: approve the task on show once the reviewer confirms. */
async function approveByKey() {
  const t = current();
  if (!t || !mine(t) || state.busy || $("approve").disabled) return;
  if (await confirmKeyApproval(t)) await decide("approve");
}

/** Ask before approving a branch whose commits were not all opened; resolve true to approve. */
function confirmBranch(artefact) {
  const unread = artefact.commits.filter((c) => !state.range.read.has(c.sha));
  if (!unread.length) return Promise.resolve(true);
  return new Promise((resolve) => {
    const done = (ok) => { $("confirm").hidden = true; $("confirm-send").onclick = null; $("confirm-cancel").onclick = null; resolve(ok); };
    $("confirm-title").textContent = `Approve all ${artefact.commits.length} commits?`;
    $("confirm-cancel").textContent = "Back to the branch";
    $("confirm-send").textContent = `Approve all ${artefact.commits.length}`;
    $("confirm-send").disabled = false;
    const list = el("ol", { class: "commits compact" });
    for (const c of unread) {
      const open = () => { done(false); selectCommit(artefact.commits.indexOf(c)); };
      list.append(el("li", {}, el("button", { class: "commit-row", onclick: open },
        el("span", { class: "sha mono", text: String(c.sha).slice(0, 7) }),
        el("span", { class: "subject", text: c.message.split("\n")[0] }))));
    }
    const opened = artefact.commits.length - unread.length;
    $("confirm-body").replaceChildren(el("p", { text: `You have opened ${opened} of the ${artefact.commits.length} commits. The approval covers every one of them as it stands. Not read yet:` }), list);
    $("confirm").hidden = false;
    $("confirm-send").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
  });
}

/** Show the patch an edit produces, and resolve true when the reviewer confirms it. */
function confirmEdit(edited) {
  return new Promise((resolve) => {
    $("confirm-title").textContent = "The change you are approving";
    $("confirm-cancel").textContent = "Back to the edit";
    $("confirm-send").textContent = "Approve this version";
    const anomalies = parsePatch(edited.patch).anomalies;
    const box = $("confirm-body");
    box.replaceChildren(...[
      anomalies.length ? el("div", { class: "alert" }, "The edit produced a patch the desk cannot show faithfully: ", anomalies.join("; "), ". It cannot be sent.") : null,
      renderDiff(edited.patch, { files: edited.files ?? [] }),
    ].filter(Boolean));
    $("confirm-send").disabled = anomalies.length > 0;
    $("confirm").hidden = false;
    const done = (ok) => { $("confirm").hidden = true; $("confirm-send").onclick = null; $("confirm-cancel").onclick = null; resolve(ok); };
    $("confirm-send").onclick = () => done(true);
    $("confirm-cancel").onclick = () => done(false);
  });
}

// -- activity -------------------------------------------------------------------

/** A few words on what a call did, for its row in the activity list. */
function gist(call) {
  const p = call.params ?? {};
  const a = p.output ?? p.artefact;
  switch (call.method) {
    case "decide.approve": return p.comment ?? "";
    case "decide.reject": return `${p.request_revision ? "revision requested: " : ""}${p.comment ?? ""}`;
    case "decide.override": return p.rationale ?? "";
    case "task.create": return p.input?.summary ?? p.input?.subject ?? p.kind ?? "";
    case "task.complete": case "review.request": return a?.summary ?? a?.subject ?? (typeof a === "string" ? a : "");
    case "participant.join": return [p.type, p.role].filter(Boolean).join(", ");
    case "control.pause": case "control.resume": return [p.scope, p.participant_uri, p.reason].filter(Boolean).join(", ");
    default: return "";
  }
}

function renderActivity() {
  const box = $("activity");
  const needle = $("act-filter").value.trim().toLowerCase();
  const refusedOnly = $("act-refused").checked;
  const rows = state.chain.entries.filter((e) => {
    const call = e.envelope ?? e.request ?? {};
    if (refusedOnly && !e.outcome) return false;
    if (!needle) return true;
    return [call.method, call.params?.from, call.params?.task_id, call.params?.workspace, e.outcome?.code].join(" ").toLowerCase().includes(needle);
  }).slice().reverse();
  const loaded = state.chain.entries.length;
  $("act-summary").textContent = loaded ? `${rows.length} of ${loaded} loaded, ${state.chain.total} on the chain` : "";
  $("act-more").hidden = !(state.chain.from > 0);
  if (!rows.length) { box.replaceChildren(el("div", { class: "empty", text: loaded ? "No entry matches." : "No entry yet." })); return; }
  box.replaceChildren(...rows.map((e) => {
    const call = e.envelope ?? e.request ?? {};
    const d = el("details", { class: "entry" },
      el("summary", {},
        el("span", { class: "seq", text: `#${e.seq}` }),
        el("span", { class: `method m-${String(call.method ?? "").split(".")[0]} m-${String(call.method ?? "").replace(".", "-")}` }, call.method ?? "?", e.outcome ? el("span", { class: "badge bad refusal", text: `refused ${e.outcome.code}` }) : null),
        el("span", { class: "who", title: call.params?.task_id ?? "", text: call.params?.from ?? "" }),
        el("span", { class: "gist", title: gist(call), text: gist(call) }),
        el("span", { class: "when", title: e.arrived, text: e.arrived ? `${ago(e.arrived)} ago` : "" })));
    // An entry can carry a whole branch, so its JSON is written when it is opened.
    d.addEventListener("toggle", () => { if (d.open && !d.querySelector("pre")) d.append(el("pre", { class: "json", text: JSON.stringify(e, null, 2) })); });
    return d;
  }));
}

// -- insights -------------------------------------------------------------------

// Insights is drawn again only when what it counts has changed, so a
// reader scrolling or selecting text is not interrupted by the poll.
async function loadAllTasks() {
  // Every task, for counting. The queue holds the open ones and the most
  // recent; Insights counts the whole workspace, read when it is shown.
  if (state.allTasksAt && Date.now() - state.allTasksAt < 30_000 && state.allTasksAudit === state.health?.audit) return;
  state.allTasks = (await getJson("api/tasks?brief=1")).tasks;
  state.allTasksAt = Date.now();
  state.allTasksAudit = state.health?.audit;
}

async function renderInsightsView({ force = false } = {}) {
  try { await loadAllTasks(); } catch { /* Insights then counts the queue's tasks */ }
  const tasks = state.allTasks ?? state.tasks;
  const key = JSON.stringify([tasks.length, tasks.map((t) => t.updated_at).sort().at(-1), state.health?.audit, state.chain.verified, state.analytics.summary?.written]);
  if (!force && key === state.insightsKey) return;
  state.insightsKey = key;
  const chain = state.config.chain_enabled ? { entries: state.health?.audit ?? 0, verified: state.chain.verified } : null;
  const view = renderInsights(tasks, { chain, analytics: state.analytics, onOpenTask: (t) => select_(t.task_id) });
  $("insights").replaceChildren(view);
  if (state.analytics.refine) { const r = view.querySelector("#refine"); if (r) r.innerHTML = renderMarkdown(state.analytics.refine); }
}

// -- views and keys -------------------------------------------------------------

function showView(name) {
  state.view = name;
  for (const tab of document.querySelectorAll(".tab")) tab.setAttribute("aria-selected", String(tab.dataset.view === name));
  for (const v of ["review", "activity", "insights"]) $(`view-${v}`).hidden = v !== name;
  $("decision-bar").hidden = !(name === "review" && selected() && mine(selected()));
  setHash({ view: name === "review" ? null : name });
  if (name === "activity") loadChain();
  if (name === "insights") renderInsightsView({ force: true });
}

function onKey(e) {
  if (e.metaKey || e.ctrlKey || e.altKey) return;
  const typing = ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName);
  if (e.key === "Escape") { if (!$("help").hidden) $("help").hidden = true; else if (!$("key").hidden) $("key").hidden = true; else if (!$("confirm").hidden) $("confirm-cancel").click(); else if (!$("prompt").hidden) $("prompt").hidden = true; else if (typing) e.target.blur(); return; }
  // While a dialog is open, the page under it does not move.
  if (!$("confirm").hidden || !$("prompt").hidden || !$("key").hidden || (!$("help").hidden && e.key !== "?")) return;
  if (typing) return;
  const ordered = [...document.querySelectorAll("#queue .item")].map((b) => b.dataset.id);
  const at = ordered.indexOf(state.selectedId);
  switch (e.key) {
    case "j": if (ordered.length) select_(ordered[Math.min(ordered.length - 1, at + 1)], { byUser: true }); break;
    case "k": if (ordered.length) select_(ordered[Math.max(0, at - 1)], { byUser: true }); break;
    case "a": approveByKey(); break;
    case "r": if (selected() && mine(selected())) { $("comment").focus(); notice("Write the note, then press Request changes.", ""); } break;
    case "x": decide("reject"); break;
    case "e": toggleEdit(); break;
    case "n": if (state.view === "review") selectCommit(state.range.index + 1); break;
    case "p": if (state.view === "review") selectCommit(state.range.index - 1); break;
    case "1": showView("review"); break;
    case "2": showView("activity"); break;
    case "3": showView("insights"); break;
    case "/": e.preventDefault(); $("search").focus(); break;
    case "?": $("help").hidden = !$("help").hidden; break;
    default: return;
  }
}

boot().catch((e) => { chip("connection", "could not start", "bad"); toast(e.message, "bad"); console.error(e); });
