/**
 * audit-scitt/1.0 profile (profiles/audit-scitt.md).
 *
 * Methods:
 *   - audit.submit_to_scitt   -> build COSE_Sign1-shaped statements, pass to submitter
 *   - audit.verify_receipt    -> delegate to deployment hook
 *   - audit.verify_chain      -> replay local prev_hash chain
 *
 * External SCITT integration is the deployment's job; the Coordinator
 * builds the statement shape and routes through CoordinatorOptions.scittSubmitter.
 */
import type { Coordinator } from "../coordinator.js";
import { canonicalize, sha256Hex, ZERO_HASH } from "../canonical.js";
import { E, rpcError } from "../jsonrpc.js";
import type { Envelope } from "../types.js";

function buildStatement(workspaceId: string, envelope: Envelope, issuer: string): Record<string, unknown> {
  return {
    protected: {
      alg: -8,  // Ed25519 per COSE
      iss: issuer,
      kid: "scitt-issuer",
      cwt_claims: { sub: workspaceId, iat: null },
      "content-type": "application/chap+json;version=0.2",
    },
    payload: canonicalize(envelope).toString("utf-8"),
    signature: "<deployment-supplied>",
  };
}

export function registerAuditScitt(coord: Coordinator): void {
  coord.handlers.set("audit.submit_to_scitt", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const range = (p.range as { from_seq?: number; to_seq?: number } | undefined) ?? {};
    const fromSeq = range.from_seq ?? 0;
    const toSeq = range.to_seq ?? ws.audit.length;
    const issuer = (p.issuer as string) ?? "service:coordinator";

    if (!coord.options.scittSubmitter) {
      const statements = ws.audit.slice(fromSeq, toSeq).map(e =>
        buildStatement(ws.id, e.envelope, issuer));
      return { result: {
        statements,
        note: "No scittSubmitter configured; submit these out-of-band",
      }};
    }
    const receipts: unknown[] = [];
    for (const entry of ws.audit.slice(fromSeq, toSeq)) {
      const statement = buildStatement(ws.id, entry.envelope, issuer);
      let receipt: Record<string, unknown> | null;
      try {
        receipt = coord.options.scittSubmitter(statement);
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return { error: rpcError(E.SCITT_UNREACHABLE, `SCITT submission error: ${msg}`) };
      }
      if (receipt === null) {
        return { error: rpcError(E.SCITT_STATEMENT_REJECTED,
          `Statement rejected at seq ${entry.seq}`) };
      }
      receipts.push({ seq: entry.seq, receipt });
    }
    return { result: { receipts } };
  });

  coord.handlers.set("audit.verify_receipt", (p) => {
    const receipt = p.receipt;
    if (typeof receipt !== "object" || receipt === null) {
      return { error: rpcError(E.PARAMS, "receipt must be an object") };
    }
    if (coord.options.verifyScittReceipt) {
      let ok: boolean;
      try { ok = !!coord.options.verifyScittReceipt(receipt as Record<string, unknown>); }
      catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        return { error: rpcError(E.SCITT_RECEIPT_INVALID, `verify error: ${msg}`) };
      }
      if (!ok) return { error: rpcError(E.SCITT_RECEIPT_INVALID, "Receipt did not verify") };
      return { result: { verified: true } };
    }
    return { result: { verified: null, note: "No verifyScittReceipt hook configured" } };
  });

  coord.handlers.set("audit.verify_chain", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    const errors: string[] = [];
    let prev = ZERO_HASH;
    for (const e of ws.audit) {
      const expectedPrev = prev;
      if (e.prev_hash !== undefined && e.prev_hash !== expectedPrev) {
        errors.push(`seq ${e.seq}: prev_hash mismatch`);
      }
      prev = sha256Hex(Buffer.concat([canonicalize(e.envelope), Buffer.from(expectedPrev, "utf-8")]));
    }
    if (errors.length) {
      return { error: rpcError(E.PARAMS, errors.join("; ")) };
    }
    return { result: {
      ok: true,
      entries_checked: ws.audit.length,
      chain_head: ws.chain_head ?? ZERO_HASH,
    }};
  });
}
