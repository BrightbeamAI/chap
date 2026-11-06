// OIDC for participant.join, with no dependencies.
//
// discover() reads the issuer's discovery document, which names its
// authorisation and token endpoints and where it publishes its keys.
// makeOidcVerifier() fetches those keys and returns the check the
// coordinator calls inside dispatch, which is synchronous: node:crypto
// verifies the signature with keys already in hand. A token is accepted
// when it is a compact JWT signed with RS256, ES256 or EdDSA by a key the
// issuer publishes, issued by the issuer for this client (`aud`, and `azp`
// where the token names it), inside its validity window, with a subject.
// The claims come back as the coordinator reads them at participant.join:
// sub, auth_time, acr, nonce, cnf.jwk and chap_participant_uri where the
// token carries them.
//
// forPeople() binds each token to a person chap.config.json names: by the
// chap_participant_uri an issuer that knows CHAP puts in it, or by the
// subject a person's entry gives as oidc_sub. A token that names nobody
// configured is refused, so an account at the provider is not by itself a
// way to join as a reviewer.
//
// A check that fails gives a reason, and the coordinator's verifier null,
// which it answers with -32403. It never throws: an exception inside a
// handler is answered as -32603 and says nothing about the token. A token
// naming a key the issuer does not publish is refused, and the keys are
// fetched again in the background for the next call, so a rotation at the
// issuer costs one refused join and not a restart.

import { createPublicKey, verify as cryptoVerify } from "node:crypto";

const ALGORITHMS = {
  RS256: { kty: "RSA", digest: "sha256", options: {} },
  ES256: { kty: "EC", crv: "P-256", digest: "sha256", options: { dsaEncoding: "ieee-p1363" } },
  EdDSA: { kty: "OKP", digest: null, options: {} },
};

/** Whether a URL's host is this machine's loopback address. */
const onLoopback = (url) => ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);

/**
 * The issuer's discovery document, from <issuer>/.well-known/openid-configuration.
 * Its `issuer` must be the issuer asked for, exactly (OpenID Connect
 * Discovery 4.3), and the issuer must be https, or on this machine.
 */
export async function discover(issuer, { fetchImpl = globalThis.fetch } = {}) {
  let url;
  try { url = new URL(issuer); } catch { throw new Error(`The OIDC issuer ${JSON.stringify(issuer)} is not a URL`); }
  if (url.protocol !== "https:" && !(url.protocol === "http:" && onLoopback(url))) throw new Error(`The OIDC issuer ${issuer} is not https; only an issuer on this machine, a development one, may be http`);
  const where = `${issuer.replace(/\/$/, "")}/.well-known/openid-configuration`;
  let doc;
  try {
    const res = await fetchImpl(where, { signal: AbortSignal.timeout(10_000) });
    if (!res.ok) throw new Error(`it answered ${res.status}`);
    doc = await res.json();
  } catch (e) {
    throw new Error(`The OIDC issuer's discovery document at ${where} could not be read: ${e.message}`);
  }
  if (doc?.issuer !== issuer) throw new Error(`The discovery document at ${where} names the issuer ${JSON.stringify(doc?.issuer)}, and the configuration names ${issuer}; set the issuer exactly as the provider states it`);
  for (const name of ["authorization_endpoint", "token_endpoint", "jwks_uri"]) {
    if (typeof doc[name] !== "string") throw new Error(`The discovery document at ${where} gives no ${name}`);
  }
  return doc;
}

/**
 * @param {object} config
 * @param {string} config.issuer     the `iss` a token must carry
 * @param {string} config.audience   the client id the desk signs in with, which a token must name in `aud`
 * @param {object} [config.discovery]  the issuer's discovery document; read from the issuer when not given
 * @param {number} [config.clock_skew_sec]  tolerance on exp and nbf, default 60
 * @param {number} [config.refresh_min_ms]  shortest gap between background refetches, default 30000
 * @param {typeof fetch} [config.fetchImpl]
 * @returns {Promise<((token: string) => Record<string, unknown> | null) & { check(token: string): { claims?: object, reason?: string } }>}
 */
export async function makeOidcVerifier({ issuer, audience, discovery = null, clock_skew_sec = 60, refresh_min_ms = 30_000, fetchImpl = globalThis.fetch }) {
  if (typeof issuer !== "string" || !issuer) throw new Error("makeOidcVerifier: issuer is required");
  if (typeof audience !== "string" || !audience) throw new Error("makeOidcVerifier: audience, the client id tokens are issued to, is required");
  const doc = discovery ?? await discover(issuer, { fetchImpl });
  const jwksUrl = doc.jwks_uri;
  // Google writes its issuer into a token with or without the scheme, and
  // says to accept either (developers.google.com/identity/openid-connect).
  const issuers = issuer === "https://accounts.google.com" ? [issuer, "accounts.google.com"] : [issuer];

  let keys = new Map();
  let refreshing = null;
  let lastRefresh = 0;

  async function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      try {
        const res = await fetchImpl(jwksUrl, { signal: AbortSignal.timeout(10_000) });
        if (!res.ok) throw new Error(`JWKS at ${jwksUrl} answered ${res.status}`);
        keys = indexKeys(await res.json());
      } finally {
        // Counted on failure too, so an issuer that is down is asked again
        // after the gap and a stream of unknown kids does not become a
        // stream of fetches.
        lastRefresh = Date.now();
        refreshing = null;
      }
    })();
    return refreshing;
  }

  function refreshInBackground() {
    if (refreshing || Date.now() - lastRefresh < refresh_min_ms) return;
    refresh().catch(() => { /* the next unknown kid tries again */ });
  }

  await refresh();

  /** The token's claims, or why it is refused. */
  function check(token) {
    try {
      const parsed = parseJwt(token);
      if (!parsed) return { reason: "the token is not a signed JWT" };
      const { header, payload, signingInput, signature } = parsed;
      if (header.crit !== undefined) return { reason: "the token names header extensions the verifier does not know (crit)" };
      const alg = ALGORITHMS[header.alg];
      if (!alg) return { reason: `the token is signed with ${JSON.stringify(header.alg)}, which the verifier does not take` };
      const candidates = candidateKeys(keys, header, alg);
      if (candidates.length === 0) {
        refreshInBackground();
        return { reason: "the token is signed with a key the issuer does not publish" };
      }
      const verified = candidates.some((k) => {
        try { return cryptoVerify(alg.digest, signingInput, { key: k.key, ...alg.options }, signature); } catch { return false; }
      });
      if (!verified) return { reason: "the token's signature does not verify" };
      if (!issuers.includes(payload.iss)) return { reason: `the token was issued by ${JSON.stringify(payload.iss)}, and the issuer here is ${issuer}` };
      if (!hasAudience(payload.aud, audience)) return { reason: `the token was issued to ${JSON.stringify(payload.aud)}, and the client here is ${audience}` };
      // A token issued to several audiences names the one it was issued for in azp (OpenID Connect Core 3.1.3.7).
      if ((Array.isArray(payload.aud) && payload.aud.length > 1 && payload.azp === undefined) || (payload.azp !== undefined && payload.azp !== audience)) {
        return { reason: `the token was issued for ${JSON.stringify(payload.azp ?? null)}, and the client here is ${audience}` };
      }
      const now = Math.floor(Date.now() / 1000);
      if (typeof payload.exp !== "number" || now >= payload.exp + clock_skew_sec) return { reason: "the token has expired" };
      if (payload.nbf !== undefined && (typeof payload.nbf !== "number" || now < payload.nbf - clock_skew_sec)) return { reason: "the token is not valid yet" };
      if (typeof payload.sub !== "string" || payload.sub === "") return { reason: "the token names no subject" };
      return { claims: claimsFor(payload) };
    } catch {
      return { reason: "the token could not be read" };
    }
  }

  const verify = (token) => check(token).claims ?? null;
  verify.check = check;
  verify.refresh = refresh;
  verify.kids = () => [...keys.keys()];
  return verify;
}

/**
 * A check that binds each token to a person in `humans`, chap.config.json's
 * list: one whose token names their URI in chap_participant_uri, which an
 * issuer that knows CHAP puts there, or whose entry gives the token's
 * subject as `oidc_sub`. A person whose entry gives an oidc_sub is held to
 * it either way. The claims come back with chap_participant_uri set, which
 * the coordinator then holds the join's `from` to; a token that names
 * nobody configured is refused, with its subject and email beside the
 * reason, to say who signed in and what to add.
 */
export function forPeople(check, humans = []) {
  return (token) => {
    const result = check(token);
    if (!result.claims) return result;
    const claims = result.claims;
    const refused = (reason) => ({ reason, subject: claims.sub, ...(typeof claims.email === "string" ? { email: claims.email } : {}) });
    const who = typeof claims.email === "string" ? `${claims.email}, subject ${claims.sub},` : `subject ${claims.sub}`;
    if (claims.chap_participant_uri !== undefined) {
      const named = humans.find((h) => h.uri === claims.chap_participant_uri);
      if (!named) return refused(`the token names ${JSON.stringify(claims.chap_participant_uri)}, whom chap.config.json does not name among its people`);
      if (named.oidc_sub !== undefined && named.oidc_sub !== claims.sub) return refused(`the token is for ${who} and ${named.uri} signs in as subject ${named.oidc_sub}`);
      return { claims };
    }
    const matched = humans.filter((h) => h.oidc_sub !== undefined && h.oidc_sub === claims.sub);
    if (matched.length > 1) return refused(`subject ${claims.sub} is given to ${matched.length} people in chap.config.json`);
    if (!matched.length) return refused(`signed in as ${who} whom chap.config.json does not name: add "oidc_sub": ${JSON.stringify(claims.sub)} to that person's entry in humans`);
    return { claims: { ...claims, chap_participant_uri: matched[0].uri } };
  };
}

/** The claims the coordinator reads, and the standard ones beside them. */
function claimsFor(payload) {
  const out = { iss: payload.iss, sub: payload.sub, aud: payload.aud, exp: payload.exp };
  for (const name of ["iat", "nbf", "auth_time", "acr", "amr", "azp", "nonce", "email", "email_verified", "name", "chap_participant_uri"]) {
    if (payload[name] !== undefined) out[name] = payload[name];
  }
  const jwk = payload.cnf && typeof payload.cnf === "object" ? payload.cnf.jwk : undefined;
  if (jwk && typeof jwk === "object") out.cnf = { jwk };
  return out;
}

function hasAudience(aud, wanted) {
  if (typeof aud === "string") return aud === wanted;
  if (Array.isArray(aud)) return aud.includes(wanted);
  return false;
}

/** header.payload.signature, base64url, both JSON parts objects. */
export function parseJwt(token) {
  if (typeof token !== "string") return null;
  const parts = token.split(".");
  if (parts.length !== 3) return null;
  const header = decodeJson(parts[0]);
  const payload = decodeJson(parts[1]);
  if (!header || !payload) return null;
  const signature = fromBase64Url(parts[2]);
  if (!signature) return null;
  return { header, payload, signingInput: Buffer.from(`${parts[0]}.${parts[1]}`, "utf8"), signature };
}

function decodeJson(segment) {
  const bytes = fromBase64Url(segment);
  if (!bytes) return null;
  try {
    const value = JSON.parse(bytes.toString("utf8"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : null;
  } catch {
    return null;
  }
}

function fromBase64Url(s) {
  if (typeof s !== "string" || s === "" || !/^[A-Za-z0-9_-]+$/.test(s)) return null;
  return Buffer.from(s, "base64url");
}

/** Index a JWKS document by kid. A key that node:crypto cannot read is left out. */
function indexKeys(doc) {
  const out = new Map();
  const list = doc && Array.isArray(doc.keys) ? doc.keys : [];
  for (const jwk of list) {
    if (!jwk || typeof jwk !== "object") continue;
    try {
      const key = createPublicKey({ key: jwk, format: "jwk" });
      const id = typeof jwk.kid === "string" ? jwk.kid : `nokid-${out.size}`;
      out.set(id, { key, kty: jwk.kty, crv: jwk.crv, alg: jwk.alg, use: jwk.use });
    } catch {
      // Not a public key this runtime can load: skipped, never trusted.
    }
  }
  return out;
}

/**
 * The keys a token's header may be checked against. With a kid, the one key
 * of that name; without, every key of the algorithm's type. A key marked for
 * another algorithm or for encryption is never a candidate.
 */
function candidateKeys(keys, header, alg) {
  const fits = (k) => k.kty === alg.kty && (!alg.crv || k.crv === alg.crv) && (!k.alg || k.alg === header.alg) && (!k.use || k.use === "sig");
  if (typeof header.kid === "string") {
    const k = keys.get(header.kid);
    return k && fits(k) ? [k] : [];
  }
  return [...keys.values()].filter(fits);
}
