// A development OIDC issuer. For tests and local runs only.
//
// It generates an ES256 key at start and serves what a provider serves for
// the authorisation code flow with PKCE: the discovery document, the JWKS,
// an authorisation endpoint and a token endpoint. Its ID tokens have the
// shape the identity-oidc/1.0 profile reads: iss, sub, aud, azp, iat, exp,
// auth_time, the nonce the sign-in asked for, and chap_participant_uri.
//
// Nothing here authenticates anyone. The authorisation endpoint signs in
// whoever its login_hint names, the participant URI the desk asks for,
// with no question asked, and mintToken signs whatever it is given: what a
// test or a local run needs, and what a deployment must never have. Its
// discovery document says so, as chap_development_issuer, and the console
// says so at each sign-in. A deployment's tokens come from its own
// provider, which asks the person who they are.
//
// `chap-server dev-issuer [port]` starts it and prints the environment the
// server reads.

import { createServer } from "node:http";
import { createHash, generateKeyPairSync, randomBytes, sign as cryptoSign } from "node:crypto";

export const DEV_AUDIENCE = "chap-dev";

export function toBase64Url(input) {
  const buf = Buffer.isBuffer(input) ? input : Buffer.from(input, "utf8");
  return buf.toString("base64url");
}

/** A compact JWT over `payload`, signed with ES256 by `privateKey` under `kid`. */
export function signJwt({ privateKey, kid, payload, alg = "ES256" }) {
  const header = toBase64Url(JSON.stringify({ alg, typ: "JWT", kid }));
  const body = toBase64Url(JSON.stringify(payload));
  const input = Buffer.from(`${header}.${body}`, "utf8");
  const signature = alg === "ES256"
    ? cryptoSign("sha256", input, { key: privateKey, dsaEncoding: "ieee-p1363" })
    : cryptoSign(alg === "EdDSA" ? null : "sha256", input, privateKey);
  return `${header}.${body}.${toBase64Url(signature)}`;
}

/** Whether a redirect URI is on this machine, the only place the development issuer sends a code. */
function loopbackRedirect(value) {
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) && ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) ? url : null;
  } catch { return null; }
}

/**
 * Start the issuer on `port` (0 picks a free one). Returns
 * { issuer, jwks_url, audience, kid, publicJwk, mintToken, close }.
 * `log` hears of each sign-in.
 */
export async function startDevIssuer(port = 0, { host = "127.0.0.1", audience = DEV_AUDIENCE, log = () => {} } = {}) {
  const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
  const kid = `dev-${randomBytes(4).toString("hex")}`;
  const publicJwk = { ...publicKey.export({ format: "jwk" }), kid, use: "sig", alg: "ES256" };
  /** The codes given out and not yet redeemed, each for one minute. */
  const codes = new Map();

  let issuer = "";
  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const json = (status, body) => {
      res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" });
      res.end(JSON.stringify(body));
    };
    if (url.pathname === "/.well-known/openid-configuration") {
      return json(200, {
        issuer,
        authorization_endpoint: `${issuer}/authorize`,
        token_endpoint: `${issuer}/token`,
        jwks_uri: `${issuer}/.well-known/jwks.json`,
        response_types_supported: ["code"],
        grant_types_supported: ["authorization_code"],
        code_challenge_methods_supported: ["S256"],
        token_endpoint_auth_methods_supported: ["none"],
        scopes_supported: ["openid", "email"],
        id_token_signing_alg_values_supported: ["ES256"],
        subject_types_supported: ["public"],
        claims_supported: ["sub", "aud", "azp", "exp", "iat", "auth_time", "nonce", "chap_participant_uri", "cnf"],
        chap_development_issuer: true,
      });
    }
    if (url.pathname === "/.well-known/jwks.json") return json(200, { keys: [publicJwk] });
    if (url.pathname === "/authorize" && req.method === "GET") return authorize(url.searchParams, res);
    if (url.pathname === "/token" && req.method === "POST") {
      const chunks = [];
      for await (const c of req) chunks.push(c);
      return json(...redeem(new URLSearchParams(Buffer.concat(chunks).toString("utf8"))));
    }
    json(404, { error: "not found" });
  });

  /** The authorisation endpoint: a code for whoever login_hint names, sent back to a redirect URI on this machine. */
  function authorize(q, res) {
    const redirect = loopbackRedirect(q.get("redirect_uri"));
    if (!redirect) {
      res.writeHead(400, { "content-type": "text/plain; charset=utf-8" });
      return res.end("The development issuer sends a code only to a redirect_uri on this machine.");
    }
    const back = (params) => {
      for (const [k, v] of Object.entries({ ...params, state: q.get("state") ?? undefined, iss: issuer })) if (v !== undefined) redirect.searchParams.set(k, v);
      res.writeHead(302, { location: redirect.href, "cache-control": "no-store" });
      res.end();
    };
    if (q.get("client_id") !== audience) return back({ error: "unauthorized_client", error_description: `the development issuer's client is ${audience}` });
    if (q.get("response_type") !== "code") return back({ error: "unsupported_response_type" });
    if (!(q.get("scope") ?? "").split(" ").includes("openid")) return back({ error: "invalid_scope", error_description: "openid is required" });
    if (!q.get("code_challenge") || q.get("code_challenge_method") !== "S256") return back({ error: "invalid_request", error_description: "PKCE with S256 is required" });
    const who = q.get("login_hint");
    if (!who) return back({ error: "login_required", error_description: "the development issuer signs in whoever login_hint names, and none was given" });
    const code = toBase64Url(randomBytes(32));
    codes.set(code, { redirect_uri: q.get("redirect_uri"), challenge: q.get("code_challenge"), nonce: q.get("nonce"), sub: who, auth_time: Math.floor(Date.now() / 1000), expires: Date.now() + 60_000 });
    log(`The development issuer signed in ${who} with no question asked: it authenticates nobody.`);
    back({ code });
  }

  /** The token endpoint: an ID token for a code, once, for the verifier its sign-in began with. */
  function redeem(form) {
    const code = codes.get(form.get("code"));
    codes.delete(form.get("code"));
    const refuse = (description) => [400, { error: "invalid_grant", error_description: description }];
    if (form.get("grant_type") !== "authorization_code") return [400, { error: "unsupported_grant_type" }];
    if (!code || code.expires < Date.now()) return refuse("the code is unknown, used or expired");
    if (form.get("client_id") !== audience || form.get("redirect_uri") !== code.redirect_uri) return refuse("the code was given to another client or redirect URI");
    if (toBase64Url(createHash("sha256").update(form.get("code_verifier") ?? "").digest()) !== code.challenge) return refuse("the code verifier does not match the challenge");
    const isUri = /^[a-z][a-z0-9+.-]*:\S+$/.test(code.sub);
    const id_token = mintToken({ sub: code.sub, ...(isUri ? { uri: code.sub } : {}), auth_time: code.auth_time, extra: { azp: audience, ...(code.nonce ? { nonce: code.nonce } : {}) } });
    return [200, { access_token: toBase64Url(randomBytes(24)), token_type: "Bearer", expires_in: 3600, id_token }];
  }

  await new Promise((resolve) => server.listen(port, host, resolve));
  issuer = `http://${host}:${server.address().port}`;

  /**
   * Mint an ID token. `uri` becomes chap_participant_uri and `cnfJwk` the
   * cnf.jwk the coordinator pins as the participant's signing key.
   */
  function mintToken({ sub, uri, cnfJwk, auth_time, acr, audience: aud = audience, expires_in_sec = 3600, extra = {} }) {
    if (typeof sub !== "string" || !sub) throw new Error("mintToken: sub is required");
    const iat = Math.floor(Date.now() / 1000);
    const payload = {
      iss: issuer, sub, aud, iat, exp: iat + expires_in_sec,
      auth_time: typeof auth_time === "number" ? auth_time : iat,
      ...(acr ? { acr } : {}),
      ...(uri ? { chap_participant_uri: uri } : {}),
      ...(cnfJwk ? { cnf: { jwk: cnfJwk } } : {}),
      ...extra,
    };
    return signJwt({ privateKey, kid, payload });
  }

  return {
    issuer,
    jwks_url: `${issuer}/.well-known/jwks.json`,
    audience,
    kid,
    publicJwk,
    mintToken,
    close: () => new Promise((resolve) => { server.closeAllConnections(); server.close(resolve); }),
  };
}
