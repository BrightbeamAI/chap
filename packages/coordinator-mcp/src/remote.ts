/**
 * Forwarding to a running CHAP server.
 *
 * `chap-mcp-server --url <endpoint>` serves MCP on stdio and sends each call
 * on to the JSON-RPC endpoint of a server that already runs a coordinator,
 * such as a desk server's `POST /chap`. The workspace, its store and the
 * people deciding in the desk all stay with that server.
 */

import type { Envelope } from "@brightbeamai/chap-coordinator";

/** How long a call waits for the server's answer: 30 seconds, as the Python client waits. */
export const REMOTE_TIMEOUT_MS = 30_000;

/**
 * The endpoint could not be reached, or did not answer in JSON-RPC. The
 * message is written for the MCP client, which shows it to the assistant.
 */
export class ChapEndpointError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ChapEndpointError";
  }
}

/**
 * A dispatcher that POSTs each envelope to `endpoint` and returns the JSON-RPC
 * response. A refusal comes back as the server's own error envelope; only a
 * failure to get a JSON-RPC answer at all throws, and so does a server that
 * has not answered within `timeoutMs`.
 */
export function remoteDispatcher(
  endpoint: string,
  timeoutMs: number = REMOTE_TIMEOUT_MS,
): { dispatch(envelope: Envelope): Promise<Envelope> } {
  return {
    async dispatch(envelope: Envelope): Promise<Envelope> {
      let response: Response;
      let text: string;
      try {
        response = await fetch(endpoint, {
          method: "POST",
          headers: { "content-type": "application/json", accept: "application/json" },
          body: JSON.stringify(envelope),
          signal: AbortSignal.timeout(timeoutMs),
        });
        text = await response.text();
      } catch (err) {
        throw new ChapEndpointError(`The CHAP server at ${endpoint} could not be reached (${reason(err)}). Start it, or check --url.`);
      }
      let body: unknown;
      try {
        body = JSON.parse(text);
      } catch {
        throw new ChapEndpointError(`The CHAP server at ${endpoint} answered HTTP ${response.status} with a body that is not JSON.`);
      }
      if (isResponseEnvelope(body)) return body;
      const said = typeof (body as { error?: unknown })?.error === "string" ? `: ${(body as { error: string }).error}` : "";
      throw new ChapEndpointError(`The CHAP server at ${endpoint} refused the call with HTTP ${response.status}${said}`);
    },
  };
}

/**
 * Why a request got no answer, in the words the Python client uses: "timed
 * out", or the network's own reason, such as ECONNREFUSED. fetch wraps that
 * reason as the cause of its "fetch failed", by code where it has one.
 */
function reason(err: unknown): string {
  if (err instanceof Error && err.name === "TimeoutError") return "timed out";
  const cause = (err as { cause?: { code?: unknown; message?: unknown } }).cause;
  if (typeof cause?.code === "string") return cause.code;
  if (typeof cause?.message === "string" && cause.message) return cause.message;
  return err instanceof Error ? err.message : String(err);
}

function isResponseEnvelope(body: unknown): body is Envelope {
  if (typeof body !== "object" || body === null || Array.isArray(body)) return false;
  const b = body as Record<string, unknown>;
  return b.jsonrpc === "2.0" && ("result" in b || (typeof b.error === "object" && b.error !== null));
}
