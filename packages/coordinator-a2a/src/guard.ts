/**
 * @brightbeamai/chap-coordinator-a2a/guard
 *
 * The checks the create-chap-app servers make on every request, for an A2A
 * server on the user's machine. A browser will send a request to a
 * loopback address from any page, so without them a page the user visits
 * can call the coordinator:
 *
 * - a Host that is not one of the server's own names is refused with 421,
 *   so a page that rebinds its own name to this address (DNS rebinding)
 *   gets nothing;
 * - an Origin naming another site is refused with 403; a client that is
 *   not a browser sends no Origin, and is let through;
 * - a POST must be application/json (415), which a page cannot send to
 *   another origin without a preflight it would fail;
 * - a body over the limit is refused with 413 before more of it is read,
 *   and every refusal is a short JSON body.
 *
 * The body is read and parsed here, so the routes after it find it parsed.
 */

import type { IncomingMessage, ServerResponse } from "node:http";

/** The largest body read: the coordinator's 1 MiB envelope limit, and room for the A2A message around it. */
const DEFAULT_MAX_BODY_BYTES = 1_048_576 + 65_536;

export interface ChapA2aGuardOptions {
  /**
   * Names the server also answers to, each as a Host header carries it:
   * `a2a.internal:9090`, or a bare name behind a proxy on the default port.
   * Its loopback names on its own port, 127.0.0.1, localhost and [::1], are
   * always allowed.
   */
  allowedHosts?: string[];
  /** The largest request body read. Default 1,114,112 bytes. */
  maxBodyBytes?: number;
}

type Next = (err?: unknown) => void;

/**
 * Connect-style middleware: `app.use(chapA2aGuard())` in Express, before
 * the A2A routes. A request it refuses gets its answer here and goes no
 * further; one it accepts reaches the routes with its body parsed.
 */
export function chapA2aGuard(options: ChapA2aGuardOptions = {}) {
  const extra = (options.allowedHosts ?? []).map((h) => h.toLowerCase());
  const limit = options.maxBodyBytes ?? DEFAULT_MAX_BODY_BYTES;

  return function guard(req: IncomingMessage & { body?: unknown }, res: ServerResponse, next: Next): void {
    const port = req.socket.localPort;
    const allowed = new Set([`127.0.0.1:${port}`, `localhost:${port}`, `[::1]:${port}`, ...extra]);
    const host = (req.headers.host ?? "").toLowerCase();
    if (!allowed.has(host)) {
      return refuse(res, 421, { error: "unknown host; add it to the allowed hosts to serve under that name" });
    }
    if (foreignOrigin(req.headers.origin, allowed, host)) {
      return refuse(res, 403, { error: "cross-origin requests are refused" });
    }
    if (req.method !== "POST") return next();
    if (!(req.headers["content-type"] ?? "").toLowerCase().startsWith("application/json")) {
      return refuse(res, 415, { error: "POST takes application/json" });
    }
    readBody(req, limit).then(
      (text) => {
        try {
          req.body = JSON.parse(text);
        } catch {
          return refuse(res, 400, { jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } });
        }
        next();
      },
      (err) => {
        // The rest of the body is never read, so the connection cannot carry
        // another request: it closes after the answer.
        if (err instanceof BodyTooLarge) {
          res.setHeader("connection", "close");
          return refuse(res, 413, { error: `the request body is over ${limit} bytes` });
        }
        next(err);
      },
    );
  };
}

/** An Origin naming a host other than this server's own names; none at all is a client that is not a browser. */
function foreignOrigin(origin: string | undefined, allowed: Set<string>, host: string): boolean {
  if (origin === undefined) return false;
  let originHost: string;
  try { originHost = new URL(origin).host.toLowerCase(); } catch { return true; }
  return !(allowed.has(originHost) || originHost === host);
}

class BodyTooLarge extends Error {}

async function readBody(req: IncomingMessage, limit: number): Promise<string> {
  if (Number(req.headers["content-length"]) > limit) throw new BodyTooLarge();
  const chunks: Buffer[] = [];
  let size = 0;
  for await (const chunk of req) {
    size += (chunk as Buffer).length;
    if (size > limit) throw new BodyTooLarge();
    chunks.push(chunk as Buffer);
  }
  return Buffer.concat(chunks).toString("utf8");
}

function refuse(res: ServerResponse, status: number, body: unknown): void {
  res.writeHead(status, { "content-type": "application/json" }).end(JSON.stringify(body));
}
