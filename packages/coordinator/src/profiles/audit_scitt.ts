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
import { entryIsWellFormed, entryRecord, linkHash } from "../audit.js";
import { canonicalize, ZERO_HASH } from "../canonical.js";
import { E, rpcError } from "../jsonrpc.js";

/** The head after a malformed entry, which no stored head can match. */
const MALFORMED = "sha256:malformed";

/**
 * A statement's payload is what the entry's chain link hashes: the envelope
 * of an accepted call, or the outcome together with the request of a refusal.
 */
function buildStatement(workspaceId: string, record: unknown, issuer: string): Record<string, unknown> {
  return {
    protected: {
      alg: -8,  // Ed25519 per COSE
      iss: issuer,
      kid: "scitt-issuer",
      cwt_claims: { sub: workspaceId, iat: null },
      "content-type": "application/chap+json;version=0.3",
    },
    payload: canonicalize(record).toString("utf-8"),
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
        buildStatement(ws.id, entryRecord(e), issuer));
      return { result: {
        statements,
        note: "No scittSubmitter configured; submit these out-of-band",
      }};
    }
    const receipts: unknown[] = [];
    for (const entry of ws.audit.slice(fromSeq, toSeq)) {
      const statement = buildStatement(ws.id, entryRecord(entry), issuer);
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
    return { error: rpcError(E.SCITT_RECEIPT_INVALID, "No verifyScittReceipt hook configured; receipt not verified") };
  });

  coord.handlers.set("audit.verify_chain", (p) => {
    const ws = coord.workspaces.get(p.workspace as string);
    if (!ws) return { error: rpcError(E.PARAMS, "Unknown workspace") };
    if (!(ws.chain_enabled || coord.options.enableChain)) {
      return { error: rpcError(E.PARAMS, "Chain not enabled for this workspace") };
    }
    // Range verification is not implemented. Accepting the parameters and
    // replaying the whole log anyway would answer a wider question than the
    // caller asked while reporting counts scoped to the whole log, which is
    // the failure this method was just fixed to stop making.
    if (p.from_seq !== undefined || p.to_seq !== undefined) {
      return { error: rpcError(
        E.PARAMS,
        "Range verification is not implemented; omit from_seq and to_seq to " +
        "verify the whole log.",
      ) };
    }
    // Coverage begins at the first entry carrying a prev_hash. A workspace
    // may enable chaining part-way through its life, in which case every
    // earlier entry is outside the chain and cannot be evaluated against
    // anything. Those entries are not evidence of tampering and not
    // evidence of integrity: they were never checked, and the verdict
    // below has to say so rather than pass over them.
    let start = ws.audit.findIndex(e => e.prev_hash != null);
    if (start < 0) start = ws.audit.length;
    const errors: string[] = [];
    let prev = ZERO_HASH;
    let resync = false;
    for (const e of ws.audit.slice(start)) {
      // After a malformed entry the next link is taken as stored, so the
      // entries after it are judged on their own links. The chain is
      // reported broken either way.
      const expectedPrev = resync && typeof e.prev_hash === "string" ? e.prev_hash : prev;
      resync = false;
      // A chain-enabled workspace must have prev_hash on every entry; a
      // missing value is a defect, not a reason to skip the check.
      if (e.prev_hash !== expectedPrev) {
        errors.push(`seq ${e.seq}: prev_hash mismatch`);
      }
      // An entry that records neither one accepted envelope nor one refused
      // request with its outcome has been altered, and cannot be linked.
      if (!entryIsWellFormed(e)) {
        errors.push(`seq ${e.seq}: malformed entry`);
        prev = MALFORMED;
        resync = true;
        continue;
      }
      prev = linkHash(entryRecord(e), expectedPrev);
    }
    // The recomputed head must match the stored head; this is what makes
    // the final entry tamper-evident.
    // Explicit null/undefined check, matching Python. A falsy-but-present
    // head must not be silently replaced with ZERO_HASH, or the two
    // implementations return different verdicts for the same state.
    const storedHead = (ws.chain_head === undefined || ws.chain_head === null)
      ? ZERO_HASH : ws.chain_head;
    if (prev !== storedHead) {
      errors.push("chain_head mismatch: replay does not match stored head");
    }
    if (errors.length) {
      return { error: rpcError(E.PARAMS, errors.join("; ")) };
    }
    const entriesTotal     = ws.audit.length;
    const entriesChecked   = entriesTotal - start;
    const entriesUnchecked = start;
    // Three terminal verdicts, mutually exclusive: a broken chain returned
    // above as an error, an unevaluated range here, and a pass only when
    // coverage is complete. `not_evaluated` never rides alongside ok:true;
    // a reader that looks at ok alone fails closed rather than reading a
    // pass over entries nothing was checked against.
    if (entriesUnchecked > 0) {
      return { result: {
        status:            "not_evaluated",
        ok:                false,
        reason:            "unchained_prefix",
        entries_total:     entriesTotal,
        entries_checked:   entriesChecked,
        entries_unchecked: entriesUnchecked,
        checked_from_seq:  entriesChecked > 0 ? ws.audit[start].seq : null,
        chain_head:        storedHead,
      }};
    }
    return { result: {
      status:            "verified",
      ok:                true,
      entries_total:     entriesTotal,
      entries_checked:   entriesChecked,
      entries_unchecked: 0,
      checked_from_seq:  entriesChecked > 0 ? ws.audit[start].seq : null,
      chain_head:        storedHead,
    }};
  });
}
