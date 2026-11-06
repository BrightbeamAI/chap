// Signing a person in at the configured OpenID provider, where the server
// checks a token at participant.join (identity-oidc/1.0). The desk uses the
// authorisation code flow with PKCE (RFC 7636): it sends the browser to the
// provider with a code challenge, a state and a nonce, and the provider
// sends it back here with a code, which the server redeems for an ID token
// and checks (POST api/oidc/token). The nonce is the RFC 7638 thumbprint of
// the key the desk signs with, so the token attests that key, and the
// coordinator binds it to the person at the join.

import { thumbprint } from "./chap-client.mjs";

/** Where the sign-in in progress is kept, for the page the provider sends the browser back to. */
const PENDING = "chap-desk-sign-in";

const b64url = (bytes) => btoa(String.fromCharCode(...new Uint8Array(bytes))).replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");

/**
 * The provider's authorisation URL to sign `uri` in at, for the key
 * `signer` holds, which the nonce names. What the return needs is kept in
 * `storage`: the state, the code verifier, the nonce, and the page's
 * address and hash. `loginHint` is passed on where given.
 */
export async function signInUrl({ oidc, uri, signer, loginHint = null, storage = sessionStorage, loc = location }) {
  const verifier = b64url(crypto.getRandomValues(new Uint8Array(32)));
  const challenge = b64url(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier)));
  const state = b64url(crypto.getRandomValues(new Uint8Array(16)));
  const nonce = await thumbprint(signer.publicJwk);
  const redirectUri = `${loc.origin}${loc.pathname}`;
  storage.setItem(PENDING, JSON.stringify({ state, verifier, nonce, uri, redirect_uri: redirectUri, hash: loc.hash, issuer: oidc.issuer }));
  const url = new URL(oidc.authorization_endpoint);
  const params = { response_type: "code", client_id: oidc.client_id, redirect_uri: redirectUri, scope: "openid email", state, nonce, code_challenge: challenge, code_challenge_method: "S256", ...(loginHint ? { login_hint: loginHint } : {}) };
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  return url.href;
}

/**
 * The sign-in the browser has come back from, read from the page's query
 * and the record kept for it, which is then cleared: { uri, code, verifier,
 * nonce, redirect_uri, hash }, or { uri, error, hash } where it did not
 * complete or came back for a sign-in this page did not begin; null where
 * the page did not come back from one.
 */
export function returnedSignIn({ storage = sessionStorage, loc = location } = {}) {
  const q = new URLSearchParams(loc.search);
  if (!q.has("code") && !q.has("error")) return null;
  let pending = null;
  try { pending = JSON.parse(storage.getItem(PENDING) ?? "null"); } catch { /* none kept */ }
  storage.removeItem(PENDING);
  if (!pending || q.get("state") !== pending.state) return { uri: pending?.uri ?? null, error: "The browser came back from a sign-in this desk did not begin; sign in again.", hash: pending?.hash ?? "" };
  if (q.has("iss") && q.get("iss") !== pending.issuer) return { uri: pending.uri, error: `The sign-in came back from ${q.get("iss")}, and the desk signs in at ${pending.issuer}; sign in again.`, hash: pending.hash };
  if (q.has("error")) return { uri: pending.uri, error: `The sign-in at ${pending.issuer} did not complete: ${q.get("error_description") ?? q.get("error")}.`, hash: pending.hash };
  return { uri: pending.uri, code: q.get("code"), verifier: pending.verifier, nonce: pending.nonce, redirect_uri: pending.redirect_uri, hash: pending.hash };
}
