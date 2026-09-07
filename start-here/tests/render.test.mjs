// Run: node --test start-here/tests/
//
// These cover the one thing the CHAP chain cannot prove on its own: that the
// text the reviewer read is the text the reviewer approved.

import test from "node:test";
import assert from "node:assert/strict";

const { reveal, hasHidden, label, isHidden, pretty, canonical, sameJson } =
  await import("../web/render.mjs");

const CH = code => String.fromCodePoint(code);
const RLO = CH(0x202e), PDF = CH(0x202c), ZWSP = CH(0x200b), BOM = CH(0xfeff);

test("a right-to-left override is shown, not swallowed", () => {
  const spoofed = `100${RLO}00.1${PDF} USD`;
  // Without reveal(), a terminal or browser draws this as "1001.00 USD".
  assert.deepEqual(reveal(spoofed), [
    { hidden: false, text: "100" },
    { hidden: true, text: "RLO" },
    { hidden: false, text: "00.1" },
    { hidden: true, text: "PDF" },
    { hidden: false, text: " USD" },
  ]);
});

test("zero-width characters are shown", () => {
  assert.deepEqual(reveal(`a${ZWSP}b`), [
    { hidden: false, text: "a" },
    { hidden: true, text: "ZWSP" },
    { hidden: false, text: "b" },
  ]);
});

test("newline and tab are left alone", () => {
  assert.deepEqual(reveal("a\nb\tc"), [{ hidden: false, text: "a\nb\tc" }]);
});

test("ordinary text produces one run and no markers", () => {
  assert.deepEqual(reveal("Your order is on its way."),
                   [{ hidden: false, text: "Your order is on its way." }]);
  assert.equal(reveal("plain").some(run => run.hidden), false);
});

test("an unnamed control character falls back to its code point", () => {
  assert.equal(label(0x0007), "U+0007");
  assert.equal(label(0x202e), "RLO");
  assert.deepEqual(reveal(CH(0x0007)), [{ hidden: true, text: "U+0007" }]);
});

test("isHidden agrees with reveal on every character it claims", () => {
  for (const code of [0x00ad, 0x061c, 0x200b, 0x200e, 0x202a, 0x202e,
                      0x2060, 0x2066, 0x2069, 0xfeff, 0x0000, 0x001f, 0x007f]) {
    assert.equal(isHidden(CH(code)), true, `U+${code.toString(16)} should be hidden`);
  }
  for (const character of ["a", "0", " ", "\n", "\t", "é", "字", "🙂"]) {
    assert.equal(isHidden(character), false, `${JSON.stringify(character)} should not be hidden`);
  }
});

test("hasHidden walks the whole value, including keys", () => {
  assert.equal(hasHidden({ text: `ok${RLO}` }), true);
  assert.equal(hasHidden({ items: [{ note: `x${BOM}` }] }), true);
  assert.equal(hasHidden({ [`key${ZWSP}`]: "clean" }), true);
  assert.equal(hasHidden({ text: "clean", n: 12, flag: true, none: null }), false);
  assert.equal(hasHidden([]), false);
});

test("reveal never loses visible characters", () => {
  const source = `Total: 100${RLO}00.1${PDF} USD\nSigned${ZWSP} off`;
  const shown = reveal(source).filter(run => !run.hidden).map(run => run.text).join("");
  const expected = [...source].filter(character => !isHidden(character)).join("");
  assert.equal(shown, expected);
});

test("pretty is stable enough to compare against", () => {
  assert.equal(pretty({ b: 1, a: 2 }), '{\n  "b": 1,\n  "a": 2\n}');
});

test("key order is not a change, because the coordinator does not think so", () => {
  // The server compares canonical hashes. A UI comparing raw JSON.stringify
  // offers an override the server then refuses as "nothing changed".
  assert.equal(sameJson({ b: 1, a: 2 }, { a: 2, b: 1 }), true);
  assert.equal(sameJson({ x: { q: 1, p: 2 } }, { x: { p: 2, q: 1 } }), true);
  assert.equal(sameJson({ items: [1, 2] }, { items: [2, 1] }), false,
               "array order is content, not formatting");
});

test("canonical keeps booleans and numbers apart", () => {
  // Python says True == 1. JSON does not, and neither does the audit chain.
  assert.equal(sameJson({ flag: true }, { flag: 1 }), false);
  assert.equal(sameJson({ n: [1, true] }, { n: [1, 1] }), false);
  assert.equal(sameJson({ a: null }, { a: 0 }), false);
  assert.equal(canonical({ b: 1, a: [true, null] }), '{"a":[true,null],"b":1}');
});
