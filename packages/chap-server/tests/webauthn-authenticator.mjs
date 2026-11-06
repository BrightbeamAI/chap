// A software WebAuthn authenticator for the desk's tests: what a browser's
// navigator.credentials.create and get do with a platform authenticator,
// with a real P-256 key per credential. It checks the relying party as a
// browser does, builds authenticatorData and clientDataJSON as WebAuthn
// Level 3 lays them out, and signs with ES256 over authenticatorData and
// the SHA-256 of clientDataJSON, in DER, as an authenticator does.
//
// `platform` is whether the browser has a platform authenticator that
// verifies its user; `verifies` is whether the person passes that check
// when asked, so a test can have them cancel. Each create and get is kept
// in `asked`, with the options the page passed.

import { createHash, generateKeyPairSync, randomBytes, sign as cryptoSign } from "node:crypto";

const sha256 = (bytes) => createHash("sha256").update(bytes).digest();
const b64url = (bytes) => Buffer.from(bytes).toString("base64url");
const bytesOf = (source) => Buffer.from(source instanceof ArrayBuffer ? new Uint8Array(source) : new Uint8Array(source.buffer, source.byteOffset, source.byteLength));
/** A copy of a Node buffer as an ArrayBuffer of its own, as the browser hands them out. */
const arrayBuffer = (buf) => buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);

/** What a browser checks before it lets a page use a relying party id. */
function relyingParty(rpId, location) {
  const host = location.hostname;
  if (/^\d+\.\d+\.\d+\.\d+$/.test(host) || host.startsWith("[")) throw new DOMException("The relying party id cannot be an IP address", "SecurityError");
  if (rpId !== host && !host.endsWith(`.${rpId}`)) throw new DOMException(`${rpId} is not ${host} or a domain it is under`, "SecurityError");
}

/** An assertion's authenticatorData: the relying party id's hash, the flags, and the signature counter. */
export function authenticatorData(rpId, flags, counter) {
  const data = Buffer.alloc(37);
  sha256(rpId).copy(data, 0);
  data[32] = flags;
  data.writeUInt32BE(counter, 33);
  return data;
}

/** The DER ES256 signature an authenticator makes for an assertion. */
export function assertionSignature(privateKey, authData, clientDataJSON) {
  return cryptoSign("sha256", Buffer.concat([authData, sha256(clientDataJSON)]), { key: privateKey, dsaEncoding: "der" });
}

export function softwareAuthenticator({ platform = true, verifies = true } = {}) {
  const credentials = new Map();
  const asked = [];
  const auth = {
    platform, verifies, asked, credentials,

    async create(options, location) {
      const o = options.publicKey;
      asked.push({ op: "create", publicKey: o });
      relyingParty(o.rp.id, location);
      if (!o.pubKeyCredParams.some((p) => p.alg === -7)) throw new DOMException("No algorithm this authenticator has", "NotSupportedError");
      const attachment = o.authenticatorSelection?.authenticatorAttachment;
      if ((attachment === "platform" && !auth.platform) || !auth.verifies) throw new DOMException("The operation either timed out or was not allowed.", "NotAllowedError");
      const { privateKey, publicKey } = generateKeyPairSync("ec", { namedCurve: "P-256" });
      const id = randomBytes(16);
      credentials.set(b64url(id), { privateKey, publicKey, rpId: o.rp.id, counter: 0, userId: bytesOf(o.user.id) });
      const clientDataJSON = Buffer.from(JSON.stringify({ type: "webauthn.create", challenge: b64url(bytesOf(o.challenge)), origin: location.origin, crossOrigin: false }));
      const spki = publicKey.export({ format: "der", type: "spki" });
      return {
        id: b64url(id), rawId: arrayBuffer(id), type: "public-key", authenticatorAttachment: "platform",
        response: {
          clientDataJSON: arrayBuffer(clientDataJSON),
          getPublicKey: () => arrayBuffer(spki),
          getPublicKeyAlgorithm: () => -7,
          getTransports: () => ["internal"],
        },
      };
    },

    async get(options, location) {
      const o = options.publicKey;
      asked.push({ op: "get", publicKey: o });
      relyingParty(o.rpId, location);
      const allowed = (o.allowCredentials ?? []).map((c) => b64url(bytesOf(c.id)));
      const id = allowed.find((k) => credentials.get(k)?.rpId === o.rpId);
      if (!id || (o.userVerification === "required" && !auth.verifies)) throw new DOMException("The operation either timed out or was not allowed.", "NotAllowedError");
      const cred = credentials.get(id);
      cred.counter += 1;
      const authData = authenticatorData(o.rpId, 0x05, cred.counter);
      const clientDataJSON = Buffer.from(JSON.stringify({ type: "webauthn.get", challenge: b64url(bytesOf(o.challenge)), origin: location.origin, crossOrigin: false }));
      return {
        id, rawId: arrayBuffer(Buffer.from(id, "base64url")), type: "public-key", authenticatorAttachment: "platform",
        response: {
          authenticatorData: arrayBuffer(authData),
          clientDataJSON: arrayBuffer(clientDataJSON),
          signature: arrayBuffer(assertionSignature(cred.privateKey, authData, clientDataJSON)),
          userHandle: arrayBuffer(cred.userId),
        },
      };
    },

    /** Install as a window's navigator.credentials and PublicKeyCredential. */
    install(window) {
      Object.defineProperty(window.navigator, "credentials", {
        configurable: true,
        value: { create: (options) => auth.create(options, window.location), get: (options) => auth.get(options, window.location) },
      });
      window.PublicKeyCredential = class PublicKeyCredential {
        static isUserVerifyingPlatformAuthenticatorAvailable() { return Promise.resolve(auth.platform); }
      };
      return window.PublicKeyCredential;
    },
  };
  return auth;
}
