// How the desk signs for a reviewer, from desk_signing in chap.config.json
// and what this browser can do at this address:
//
//   "browser-key"  a key the desk makes and keeps in the browser
//   "webauthn"     a passkey, which asks the person to verify on each signature
//   "auto"         a passkey where the browser has an authenticator built in
//                  that verifies its user, the browser key otherwise
//
// WebAuthn names its relying party by a domain name, never an IP address,
// so a passkey needs the desk opened at a name, which for this machine's
// loopback address is localhost.

/** Whether a page's hostname is an IP address. */
export const isAddress = (host) => /^\d{1,3}(\.\d{1,3}){3}$/.test(host) || host.startsWith("[");

/** Whether a page's hostname is this machine's loopback address. */
export const isLoopbackAddress = (host) => /^127(\.\d{1,3}){3}$/.test(host) || host === "[::1]";

/** The same page at localhost. */
export const atLocalhost = (loc) => `${loc.protocol}//localhost${loc.port ? `:${loc.port}` : ""}${loc.pathname}${loc.search}${loc.hash}`;

/**
 * What the desk signs with, for a reviewer it holds no key for yet:
 * "passkey"; "browser-key"; "localhost" where a passkey is wanted and the
 * page is at the loopback address, so the desk goes to localhost; or "no
 * passkey" where desk_signing "webauthn" asks for one and none can be made
 * here.
 */
export async function signingFor(mode, loc, PKC = globalThis.PublicKeyCredential, credentials = globalThis.navigator?.credentials) {
  if (mode !== "auto" && mode !== "webauthn") return "browser-key";
  const webauthn = typeof PKC === "function" && !!credentials;
  const platform = webauthn && await PKC.isUserVerifyingPlatformAuthenticatorAvailable().catch(() => false);
  if (mode === "auto" && !platform) return "browser-key";
  if (isAddress(loc.hostname)) return isLoopbackAddress(loc.hostname) ? "localhost" : mode === "auto" ? "browser-key" : "no passkey";
  return webauthn ? "passkey" : "no passkey";
}
