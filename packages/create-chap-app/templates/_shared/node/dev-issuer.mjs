// A development OIDC issuer. For tests and local runs only.
//
// It generates an ES256 key at start, serves the two discovery documents a
// verifier reads, and mints ID tokens in the shape the identity-oidc/1.0
// profile expects (integrations/CHAP-with-OIDC-OAuth2.md, section 2):
// iss, sub, aud, iat, exp, auth_time, acr, chap_participant_uri and cnf.jwk.
//
// Nothing here authenticates anyone. mintToken signs whatever it is given,
// which is what a test needs and what a deployment must never have. A
// deployment obtains its tokens from its own identity provider through its
// own login, with the participant's public key carried in cnf.jwk.
//
// As a script: `node lib/dev-issuer.mjs [port]` starts the issuer and prints
// the environment the coordinator reads.

import { createServer } from "node:http";
import { generateKeyPairSync, randomBytes, sign as cryptoSign } from "node:crypto";
import { fileURLToPath } from "node:url";

export const DEV_AUDIENCE = "chap-dev";

export function toBase64Url(input) {
  const buf = Buffer.isBuffer(input) ? input : Buffer.from(input, "utf8");
  return buf.toString("base64").replace(/=+$/, "").replace(/\+/g, "-").replace(/\//g, "_");
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

/**
 * Start the issuer on `port` (0 picks a free one). Returns
 * { issuer, jwks_url, audience, kid, mintToken, close }.
 */
export async function startDevIssuer(port = 0, { host = "127.0.0.1", audience = DEV_AUDIENCE } = {}) {
  const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
  const kid = `dev-${randomBytes(4).toString("hex")}`;
  const publicJwk = { ...publicKey.export({ format: "jwk" }), kid, use: "sig", alg: "ES256" };

  let issuer = "";
  const server = createServer((req, res) => {
    const path = new URL(req.url ?? "/", "http://localhost").pathname;
    const json = (status, body) => {
      res.writeHead(status, { "content-type": "application/json", "cache-control": "no-store" });
      res.end(JSON.stringify(body));
    };
    if (path === "/.well-known/openid-configuration") {
      return json(200, {
        issuer,
        jwks_uri: `${issuer}/.well-known/jwks.json`,
        id_token_signing_alg_values_supported: ["ES256"],
        subject_types_supported: ["public"],
        response_types_supported: ["id_token"],
        claims_supported: ["sub", "aud", "exp", "iat", "auth_time", "acr", "chap_participant_uri", "cnf"],
      });
    }
    if (path === "/.well-known/jwks.json") return json(200, { keys: [publicJwk] });
    json(404, { error: "not found" });
  });
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
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === process.argv[1]) {
  const port = Number(process.argv[2] ?? 8791);
  const dev = await startDevIssuer(port);
  console.log("Development OIDC issuer. Tests and local runs only: it signs any claims it is asked to.");
  console.log(`OIDC_ISSUER=${dev.issuer}`);
  console.log(`OIDC_JWKS_URL=${dev.jwks_url}`);
  console.log(`OIDC_AUDIENCE=${dev.audience}`);
}
