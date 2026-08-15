/**
 * Per-request protocol envelope (MCP 2026-07-28).
 *
 * The 2026 revision is stateless: instead of negotiating once through an
 * `initialize` handshake, every request declares its protocol version and
 * client capabilities in `_meta`, and the server accepts or rejects each
 * request on its own. This module implements that classification so the
 * TypeScript adapter behaves the same way the Python `mcp` 2.x SDK does,
 * which enforces the same rules natively.
 *
 * Two version sets, deliberately different:
 *
 * - `MODERN_PROTOCOL_VERSIONS` is what may be declared per request. Only
 *   revisions that use per-request metadata qualify, so `2025-11-25` is
 *   not a legal per-request declaration: it is negotiated by handshake.
 *   This is the set named in an `UnsupportedProtocolVersionError`, so a
 *   client retrying from that list always retries with something usable.
 *
 * - `SUPPORTED_PROTOCOL_VERSIONS` is what `server/discover` advertises. It
 *   spans both eras because CHAP serves both: a modern client uses
 *   `2026-07-28` per request, a handshake-era client negotiates
 *   `2025-11-25` through `initialize`.
 */

import { ErrorCode } from "@modelcontextprotocol/sdk/types.js";

/**
 * A JSON-RPC error carrying a spec-exact `message`.
 *
 * The SDK's own `McpError` rewrites the message to `MCP error <code>: <text>`
 * and the protocol layer puts that rewritten string on the wire, which would
 * make CHAP answer `MCP error -32022: Unsupported protocol version` where the
 * specification, and the Python implementation, both say `Unsupported
 * protocol version`. The protocol layer reads `code`, `message` and `data`
 * off whatever is thrown, so a plain `Error` carrying those fields
 * serialises exactly as written.
 */
export class ProtocolError extends Error {
  readonly code: number;
  readonly data?: unknown;

  constructor(code: number, message: string, data?: unknown) {
    super(message);
    this.name = "ProtocolError";
    this.code = code;
    if (data !== undefined) this.data = data;
  }
}

/** `_meta` key carrying the protocol version of a single request. */
export const META_PROTOCOL_VERSION = "io.modelcontextprotocol/protocolVersion";
/** `_meta` key carrying client identity. Optional per the specification. */
export const META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo";
/** `_meta` key carrying client capabilities. Required on a modern request. */
export const META_CLIENT_CAPABILITIES = "io.modelcontextprotocol/clientCapabilities";

/**
 * `UnsupportedProtocolVersionError`. The specification reserves -32020 to
 * -32099 for its own codes; -32022 is the one it assigns to a version the
 * server does not implement.
 */
export const UNSUPPORTED_PROTOCOL_VERSION = -32022;

/** Revisions that may be declared in a per-request `_meta` envelope. */
export const MODERN_PROTOCOL_VERSIONS = ["2026-07-28"] as const;

/**
 * Revisions advertised by `server/discover`, newest first. The tool surface
 * is identical across both: CHAP exposes plain tools with JSON Schema
 * inputs, which the 2026-07-28 stateless core carries unchanged.
 */
export const SUPPORTED_PROTOCOL_VERSIONS = ["2026-07-28", "2025-11-25"] as const;

/**
 * Which era a single request belongs to. `modern` means it carried a valid
 * per-request envelope; `legacy` means it carried no protocol version at
 * all and therefore belongs to a handshake-negotiated session.
 */
export type ProtocolEra = "modern" | "legacy";

/**
 * Classify one request by its `_meta` envelope, rejecting a malformed or
 * unsupported one.
 *
 * The rejection ladder matches the Python SDK's, rung for rung, so the two
 * implementations answer an identical probe identically:
 *
 * 1. no `_meta`, or no protocol version in it, is a handshake-era request
 * 2. a declared version with no `clientCapabilities` is malformed: -32602
 * 3. a non-string version is malformed: -32602
 * 4. a version outside `MODERN_PROTOCOL_VERSIONS`: -32022
 *
 * @throws ProtocolError on a malformed or unsupported envelope.
 */
export function classifyEnvelope(params: unknown): ProtocolEra {
  const meta = readMeta(params);
  if (meta === null) return "legacy";

  if (!(META_PROTOCOL_VERSION in meta)) return "legacy";
  const declared = meta[META_PROTOCOL_VERSION];
  if (declared === undefined || declared === null) return "legacy";

  // A declared version makes this a modern request, so the rest of the
  // required envelope must be present. The specification marks
  // clientCapabilities required and clientInfo optional, and requires a
  // missing required field to be rejected as invalid params.
  if (!(META_CLIENT_CAPABILITIES in meta)) {
    throw new ProtocolError(
      ErrorCode.InvalidParams,
      `params._meta is missing the required envelope key(s): ${META_CLIENT_CAPABILITIES}`,
    );
  }

  if (typeof declared !== "string") {
    throw new ProtocolError(
      ErrorCode.InvalidParams,
      "the protocol-version envelope value must be a string",
    );
  }

  if (!(MODERN_PROTOCOL_VERSIONS as readonly string[]).includes(declared)) {
    throw new ProtocolError(UNSUPPORTED_PROTOCOL_VERSION, "Unsupported protocol version", {
      supported: [...MODERN_PROTOCOL_VERSIONS],
      requested: declared,
    });
  }

  return "modern";
}

/** Read `params._meta` as a plain object, or null when it is absent. */
function readMeta(params: unknown): Record<string, unknown> | null {
  if (typeof params !== "object" || params === null) return null;
  const meta = (params as Record<string, unknown>)._meta;
  if (typeof meta !== "object" || meta === null || Array.isArray(meta)) return null;
  return meta as Record<string, unknown>;
}
