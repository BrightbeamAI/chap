/**
 * security-signed/1.0 profile (profiles/security-signed.md).
 *
 * Top-level `sig` field on every envelope: ed25519:<kid>:<base64>.
 * Verification happens at dispatch (see Coordinator.verifySignature).
 *
 * Methods:
 *   - participant.rotate_key
 *   - participant.revoke_key
 */
import type { Coordinator } from "../coordinator.js";
import { E, rpcError } from "../jsonrpc.js";
import type { KeyRecord } from "../types.js";

export function registerSecuritySigned(coord: Coordinator): void {
  coord.handlers.set("participant.rotate_key", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    for (const f of ["from", "old_kid", "new_jwk"]) {
      if (!(f in p)) return { error: rpcError(E.PARAMS, `Missing field: ${f}`) };
    }
    const member = ws.members.get(p.from as string);
    if (!member) return { error: rpcError(E.SIG_KEY_NOT_FOUND, `Unknown participant: ${p.from}`) };
    const oldKid = p.old_kid as string;
    const oldKey = member.keys.find(k => k.kid === oldKid);
    if (!oldKey) return { error: rpcError(E.SIG_KEY_NOT_FOUND, `No key ${oldKid} for ${p.from}`) };
    if (oldKey.revoked_at) return { error: rpcError(E.SIG_KEY_REVOKED, `Key ${oldKid} is revoked`) };

    const newJwk = p.new_jwk as Record<string, unknown>;
    if (!newJwk || typeof newJwk.kid !== "string") {
      return { error: rpcError(E.PARAMS, "new_jwk must include kid") };
    }
    const now = coord.now();
    oldKey.valid_until = now;
    member.keys.push({
      jwk: newJwk as unknown as KeyRecord["jwk"],
      kid: newJwk.kid as string,
      valid_from: now,
    });
    return { result: { rotated: true, old_kid: oldKid, new_kid: newJwk.kid, valid_from: now } };
  });

  coord.handlers.set("participant.revoke_key", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    for (const f of ["target_uri", "kid"]) {
      if (!(f in p)) return { error: rpcError(E.PARAMS, `Missing field: ${f}`) };
    }
    const member = ws.members.get(p.target_uri as string);
    if (!member) return { error: rpcError(E.SIG_KEY_NOT_FOUND, `Unknown target: ${p.target_uri}`) };
    const key = member.keys.find(k => k.kid === p.kid);
    if (!key) return { error: rpcError(E.SIG_KEY_NOT_FOUND, `No key ${p.kid} for target`) };
    const now = coord.now();
    key.revoked_at = now;
    key.revoked_reason = (p.reason as string) || "unspecified";
    return { result: { revoked: true, kid: key.kid, revoked_at: now, reason: key.revoked_reason } };
  });
}
