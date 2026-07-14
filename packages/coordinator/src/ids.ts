/**
 * @brightbeamai/chap-coordinator/ids
 *
 * ULID-format identifier generation. Supports a deterministic mode
 * for tests/demos so byte-for-byte replay works.
 */

const ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ";  // Crockford

function encode(value: bigint, length: number): string {
  let out = "";
  for (let i = 0; i < length; i++) {
    const idx = Number(value & 31n);
    out = ALPHABET[idx] + out;
    value >>= 5n;
  }
  return out;
}

export class IdFactory {
  private counter: bigint;
  private clockMs: number;

  constructor(
    public readonly deterministic: boolean = false,
    seed: bigint = 0n,
    startMs: number = 1_700_000_000_000,
  ) {
    this.counter = seed;
    this.clockMs = startMs;
  }

  nowMs(): number {
    if (this.deterministic) {
      this.clockMs += 1;
      return this.clockMs;
    }
    return Date.now();
  }

  private randomness(): bigint {
    if (this.deterministic) {
      this.counter += 1n;
      return (this.counter * 0x9E3779B97F4A7C15n) & ((1n << 80n) - 1n);
    }
    // 80 bits of randomness
    const buf = new Uint8Array(10);
    if (typeof crypto !== "undefined" && crypto.getRandomValues) {
      crypto.getRandomValues(buf);
    } else {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      const nodeCrypto = require("node:crypto") as { randomBytes: (n: number) => Buffer };
      buf.set(nodeCrypto.randomBytes(10));
    }
    let n = 0n;
    for (const b of buf) n = (n << 8n) | BigInt(b);
    return n;
  }

  ulid(): string {
    const ts = BigInt(this.nowMs()) & ((1n << 48n) - 1n);
    const rand = this.randomness();
    return encode(ts, 10) + encode(rand, 16);
  }

  envelopeId():     string { return this.ulid(); }
  taskId():         string { return "tsk_" + this.ulid(); }
  artefactId():     string { return "art_" + this.ulid(); }
  logicalId():      string { return "lgl_" + this.ulid(); }
  workspaceId():    string { return "wsp_" + this.ulid(); }
  deliberationId(): string { return "dlb_" + this.ulid(); }
  handoffId():      string { return "hnd_" + this.ulid(); }
  snapshotId():     string { return "snp_" + this.ulid(); }
}
