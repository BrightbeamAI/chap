/**
 * The reference server answers under its own names only, refuses a call
 * from another origin, and takes JSON only, so a page in a browser cannot
 * act on it. The same checks guard reference/core and the playground.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import type { IncomingMessage } from "node:http";

import { httpRefusal } from "./server.ts";

const PORT = 45000;
const JSON_TYPE = { "content-type": "application/json" };

function request(headers: Record<string, string>, method = "POST"): IncomingMessage {
  return { method, headers, socket: { localPort: PORT } } as unknown as IncomingMessage;
}

test("a call under one of the server's own names, from no origin or its own, is let through", () => {
  for (const host of [`127.0.0.1:${PORT}`, `localhost:${PORT}`, `[::1]:${PORT}`, `LOCALHOST:${PORT}`]) {
    assert.equal(httpRefusal(request({ host, ...JSON_TYPE })), undefined, host);
    assert.equal(httpRefusal(request({ host, origin: `http://localhost:${PORT}`, ...JSON_TYPE })), undefined, host);
  }
  assert.equal(httpRefusal(request({ host: `127.0.0.1:${PORT}`, "content-type": "application/json; charset=utf-8" })), undefined);
});

test("a call under another host name is refused with 421", () => {
  for (const host of ["elsewhere.example", `elsewhere.example:${PORT}`, "localhost", "127.0.0.1:45001", ""]) {
    assert.deepEqual(httpRefusal(request({ host, ...JSON_TYPE })),
      { status: 421, message: "unknown host; set CHAP_ALLOWED_HOSTS to serve under another name" }, host);
  }
});

test("a call from another origin is refused with 403", () => {
  for (const origin of ["https://elsewhere.example", "null", `http://localhost:${PORT}, https://elsewhere.example`, "http://localhost:45001"]) {
    assert.deepEqual(httpRefusal(request({ host: `localhost:${PORT}`, origin, ...JSON_TYPE })),
      { status: 403, message: "cross-origin requests are refused" }, origin);
  }
});

test("a POST that is not JSON is refused with 415", () => {
  for (const type of ["text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x", ""]) {
    const headers: Record<string, string> = { host: `localhost:${PORT}` };
    if (type) headers["content-type"] = type;
    assert.deepEqual(httpRefusal(request(headers)), { status: 415, message: "POST /chap takes application/json" }, type);
  }
  assert.equal(httpRefusal(request({ host: `localhost:${PORT}` }, "GET")), undefined, "a GET carries no call and gets the 404");
});

test("CHAP_ALLOWED_HOSTS names further hosts, for a proxy in front", () => {
  process.env.CHAP_ALLOWED_HOSTS = "chap.internal, Desk.Example:8443";
  try {
    assert.equal(httpRefusal(request({ host: "desk.example:8443", ...JSON_TYPE })), undefined);
    assert.equal(httpRefusal(request({ host: "chap.internal", origin: "https://desk.example:8443", ...JSON_TYPE })), undefined);
    assert.equal(httpRefusal(request({ host: "chap.internal", origin: "https://other.example", ...JSON_TYPE }))?.status, 403);
  } finally {
    delete process.env.CHAP_ALLOWED_HOSTS;
  }
});
