/**
 * @brightbeamai/coordinator
 *
 * Public entry point. Re-exports the Coordinator class and the
 * supporting types so consumers can `import { Coordinator } from "@brightbeamai/coordinator"`.
 *
 * Architecture:
 *
 *   coordinator.ts   The Coordinator class with Core + review/1.0 handlers
 *                    and the dispatch/registration infrastructure.
 *   profiles/*.ts    One module per optional profile, each registers its
 *                    method handlers on a Coordinator instance.
 *   canonical.ts     JCS canonicalisation (RFC 8785).
 *   crypto.ts        Ed25519 signing/verification (Node built-ins).
 *   ids.ts           ULID-format id generation.
 *   jsonrpc.ts       Error codes and helpers.
 *   patch.ts         RFC 6902 JSON Patch.
 *   types.ts         Wire and in-memory types.
 */

export { Coordinator, PRIVILEGED_METHODS } from "./coordinator.js";
export type {
  CoordinatorOptions,
  Handler,
  TokenVerifier,
  CredentialVerifier,
  ScittSubmitter,
  ScittReceiptVerifier,
  RoutingPolicyFn,
  ReviewDepthPolicyFn,
  EscalationPolicyFn,
} from "./coordinator.js";

export { call, makeApi, CoordinatorError } from "./api.js";
export type { CoordinatorApi } from "./api.js";
export type {
  MethodName,
  MethodParams,
  MethodResult,
  MethodTable,
} from "./methods.js";

export { MemoryStore } from "./storage/store.js";
export type { Store, WorkspaceRecord } from "./storage/store.js";

export * from "./types.js";
export * from "./jsonrpc.js";
export * from "./patch.js";
export {
  canonicalize, sha256Hex, contentHash, ZERO_HASH,
} from "./canonical.js";
export {
  deriveKeypair, deriveSeed, jwkFromPrivateKey,
  publicKeyBytes, publicKeyFromJwk, publicKeyFromRaw,
  signEnvelope, verifyEnvelope,
} from "./crypto.js";
export type { Jwk as CryptoJwk } from "./crypto.js";
export { IdFactory } from "./ids.js";
export { makeDefaultPolicy } from "./policy.js";

export {
  wrapMcpToolCall,
  wrapA2aMessageExchange,
} from "./transports/wrap.js";
export type {
  WrapMcpToolCallOptions,
  WrapA2aExchangeOptions,
  WrapResult,
  WrapA2aResult,
} from "./transports/wrap.js";
