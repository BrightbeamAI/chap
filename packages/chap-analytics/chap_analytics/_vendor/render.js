// Render Vega-Lite specifications to SVG with the vendored bundles.
//
//   node render.js [outDir] < specs.json
//
// stdin is a JSON object of name -> specification. With outDir, each SVG is
// written to outDir/<name>.svg; the result summary goes to stdout either way.
"use strict";
const vm = require("node:vm");
const fs = require("node:fs");
const path = require("node:path");

const here = __dirname;
const ctx = vm.createContext({
  console, setTimeout, clearTimeout, setImmediate, clearImmediate,
  TextEncoder, TextDecoder, URL, performance, structuredClone,
});
ctx.self = ctx;
ctx.window = ctx;
vm.runInContext(fs.readFileSync(path.join(here, "vega.min.js"), "utf8"), ctx, { filename: "vega.min.js" });
vm.runInContext(fs.readFileSync(path.join(here, "vega-lite.min.js"), "utf8"), ctx, { filename: "vega-lite.min.js" });
const vega = ctx.vega;
const vl = ctx.vegaLite;

async function main() {
  const outDir = process.argv[2];
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  const specs = JSON.parse(Buffer.concat(chunks).toString("utf8"));
  const results = {};
  for (const [name, spec] of Object.entries(specs)) {
    try {
      const compiled = vl.compile(spec).spec;
      const view = new vega.View(vega.parse(compiled), { renderer: "none" });
      const svg = await view.toSVG();
      if (outDir) fs.writeFileSync(path.join(outDir, `${name}.svg`), svg);
      results[name] = { ok: true, bytes: svg.length, svg: outDir ? undefined : svg };
    } catch (e) {
      results[name] = { ok: false, error: String((e && e.message) || e).slice(0, 400) };
    }
  }
  process.stdout.write(JSON.stringify(results));
}

main().catch((e) => { process.stderr.write(String(e && e.stack || e) + "\n"); process.exit(1); });
