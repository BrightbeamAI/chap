/**
 * Type checks for the params a method reads.
 *
 * A param of the wrong type would reach code written for the right one, and
 * the two references would do different things with it: JavaScript reads
 * `[]` and `{}` as true where Python reads them as false, a Python dict
 * cannot be looked up with a list, and a value written into a message reads
 * one way in each language. Checking the type first gives both the same
 * refusal. packages/coordinator-py/chap_coordinator/params.py is the mirror.
 */
import { E, rpcError } from "./jsonrpc.js";

/** What a param must be when it is present and not null. */
export type ParamKind = "string" | "strings" | "boolean" | "object" | "list" | "integer";

const NAMES: Record<ParamKind, string> = {
  string: "a string",
  strings: "a list of strings",
  boolean: "a boolean",
  object: "an object",
  list: "a list",
  integer: "an integer",
};

function isKind(value: unknown, kind: ParamKind): boolean {
  switch (kind) {
    case "string": return typeof value === "string";
    case "strings": return Array.isArray(value) && value.every((v) => typeof v === "string");
    case "boolean": return typeof value === "boolean";
    case "object": return typeof value === "object" && value !== null && !Array.isArray(value);
    case "list": return Array.isArray(value);
    case "integer": return typeof value === "number" && Number.isInteger(value);
  }
}

/**
 * The refusal for the first param in `checks` that holds a value of another
 * kind, or null when every one is absent, null or of its kind.
 * `checks` names params of `params`, or of an object inside it as
 * `outer.inner`.
 */
export function paramsError(
  params: Record<string, unknown>,
  checks: ReadonlyArray<readonly [string, ParamKind]>,
): { error: ReturnType<typeof rpcError> } | null {
  for (const [name, kind] of checks) {
    let value: unknown = params;
    for (const part of name.split(".")) {
      value = typeof value === "object" && value !== null && !Array.isArray(value)
        ? (value as Record<string, unknown>)[part] : undefined;
    }
    if (value !== undefined && value !== null && !isKind(value, kind)) {
      return { error: rpcError(E.PARAMS, `${name} must be ${NAMES[kind]}`) };
    }
  }
  return null;
}
