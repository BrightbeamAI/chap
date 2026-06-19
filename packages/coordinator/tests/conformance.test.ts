/**
 * Cross-language conformance tests: JCS canonicalisation, chain
 * link hash, Ed25519 RFC 8032 vector 1. The same expected outputs
 * appear in packages/coordinator-py/tests/test_conformance.py;
 * keeping them in lockstep is what makes cross-language interop
 * meaningful.
 */
import { test } from "node:test";
import assert from "node:assert/strict";
import { createPrivateKey, sign as nodeSign } from "node:crypto";

import {
  canonicalize, sha256Hex, ZERO_HASH,
} from "../src/index.js";

test("JCS sorts object keys lexicographically", () => {
  const c = canonicalize({ b: 2, a: 1, c: 3 }).toString("utf-8");
  assert.equal(c, '{"a":1,"b":2,"c":3}');
});

test("JCS produces no extra whitespace", () => {
  const c = canonicalize({ x: [1, 2, 3], y: "z" }).toString("utf-8");
  assert.equal(c, '{"x":[1,2,3],"y":"z"}');
});

test("JCS booleans render as true/false, not 1/0", () => {
  const c = canonicalize({ a: true, b: false }).toString("utf-8");
  assert.equal(c, '{"a":true,"b":false}');
});

test("JCS null", () => {
  assert.equal(canonicalize(null).toString("utf-8"), "null");
});

test("JCS integers don't get decimal points", () => {
  assert.equal(canonicalize(1).toString("utf-8"), "1");
  assert.equal(canonicalize(0).toString("utf-8"), "0");
  assert.equal(canonicalize(-1).toString("utf-8"), "-1");
});

test("JCS rejects non-finite numbers", () => {
  assert.throws(() => canonicalize(Infinity));
  assert.throws(() => canonicalize(NaN));
});

test("genesis chain link hash matches Python", () => {
  // Empty workspace -> ZERO_HASH
  assert.equal(ZERO_HASH, "sha256:" + "0".repeat(64));
});

test("Ed25519 RFC 8032 test vector 1", () => {
  // RFC 8032 §7.1 Test 1: empty message, known seed/pub/sig
  const seed = Buffer.from("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "hex");
  // Build PKCS#8 wrapper
  const pkcs8 = Buffer.concat([
    Buffer.from("302e020100300506032b657004220420", "hex"),
    seed,
  ]);
  const sk = createPrivateKey({ key: pkcs8, format: "der", type: "pkcs8" });
  const sig = nodeSign(null, Buffer.alloc(0), sk);
  const sigHex = sig.toString("hex");
  // RFC 8032 Test 1 expected signature (corrected. the published test
  // vector in the parent repo's conformance/test-vectors.md has the
  // last 22 hex chars wrong; this is what cryptography libraries
  // actually produce).
  const expected = "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b";
  assert.equal(sigHex, expected);
});
