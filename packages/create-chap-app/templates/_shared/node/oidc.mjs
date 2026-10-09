// OIDC token verification for participant.join, with no dependencies.
//
// makeOidcVerifier fetches the issuer's JWKS once and returns the
// synchronous verifier the coordinator calls inside dispatch. A token is
// accepted when it is a compact JWT signed with RS256, ES256 or EdDSA by a
// key in that set, its issuer matches, its audience matches where one is
// configured, and the time is inside its validity window. The claims come
// back as the coordinator reads them at participant.join: sub, auth_time,
// acr, cnf.jwk and chap_participant_uri where the token carries them.
//
// A token that fails any check gives null, which the coordinator answers
// with -32403. The verifier never throws: an exception inside a handler is
// answered as -32603 and says nothing about the token. A token naming a kid
// the set does not hold is refused, and the set is fetched again in the
// background for the next call, so a rotation at the issuer costs one
// refused join and not a restart.

import { createPublicKey, verify as cryptoVerify } from "node:crypto";

const ALGORITHMS = {
  RS256: { kty: "RSA", digest: "sha256", options: {} },
  ES256: { kty: "EC", crv: "P-256", digest: "sha256", options: { dsaEncoding: "ieee-p1363" } },
  EdDSA: { kty: "OKP", digest: null, options: {} },
};

/**
 * @param {object} config
 * @param {string} config.issuer     the `iss` a token must carry
 * @param {string} config.jwks_url   where the issuer publishes its keys
 * @param {string|null} [config.audience]  the `aud` a token must carry; unchecked when null
 * @param {number} [config.clock_skew_sec]  tolerance on exp and nbf, default 60
 * @param {number} [config.refresh_min_ms]  shortest gap between background refetches, default 30000
 * @param {typeof fetch} [config.fetchImpl]
 * @returns {Promise<(token: string) => Record<string, unknown> | null>}
 */
export async function makeOidcVerifier({ issuer, jwks_url, audience = null, clock_skew_sec = 60, refresh_min_ms = 30_000, fetchImpl = globalThis.fetch }) {
  if (typeof issuer !== "string" || !issuer) throw new Error("makeOidcVerifier: issuer is required");
  if (typeof jwks_url !== "string" || !jwks_url) throw new Error("makeOidcVerifier: jwks_url is required");

  let keys = new Map();
  let refreshing = null;
  let lastRefresh = 0;

  async function refresh() {
    if (refreshing) return refreshing;
    refreshing = (async () => {
      try {
        const res = await fetchImpl(jwks_url);
        if (!res.ok) throw new Error(`JWKS at ${jwks_url} answered ${res.status}`);
        const body = await res.json();
        keys = indexKeys(body);
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

  function verify(token) {
    try {
      const parsed = parseJwt(token);
      if (!parsed) return null;
      const { header, payload, signingInput, signature } = parsed;
      const alg = ALGORITHMS[header.alg];
      if (!alg) return null;

      const candidates = candidateKeys(keys, header, alg);
      if (candidates.length === 0) {
        refreshInBackground();
        return null;
      }
      const verified = candidates.some((k) => {
        try {
          return cryptoVerify(alg.digest, signingInput, { key: k.key, ...alg.options }, signature);
        } catch {
          return false;
        }
      });
      if (!verified) return null;

      if (payload.iss !== issuer) return null;
      if (audience !== null && audience !== undefined && !hasAudience(payload.aud, audience)) return null;
      const now = Math.floor(Date.now() / 1000);
      if (typeof payload.exp !== "number" || now >= payload.exp + clock_skew_sec) return null;
      if (payload.nbf !== undefined && (typeof payload.nbf !== "number" || now < payload.nbf - clock_skew_sec)) return null;
      if (typeof payload.sub !== "string" || payload.sub === "") return null;

      return claimsFor(payload);
    } catch {
      return null;
    }
  }

  verify.refresh = refresh;
  verify.kids = () => [...keys.keys()];
  return verify;
}

/** The claims the coordinator reads, and the standard ones beside them. */
function claimsFor(payload) {
  const out = { iss: payload.iss, sub: payload.sub, aud: payload.aud, exp: payload.exp };
  for (const name of ["iat", "nbf", "auth_time", "acr", "amr", "email", "name", "chap_participant_uri"]) {
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
  const pad = "=".repeat((4 - (s.length % 4)) % 4);
  return Buffer.from(s.replace(/-/g, "+").replace(/_/g, "/") + pad, "base64");
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
