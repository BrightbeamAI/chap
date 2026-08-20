import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index:   "src/index.ts",
    schemas: "src/schemas.ts",
    tools:   "src/tools.ts",
    cli:     "src/cli.ts",
  },
  format:    ["esm", "cjs"],
  dts:       true,
  splitting: false,
  sourcemap: true,
  clean:     true,
  target:    "es2022",
  external:  ["@brightbeamai/chap-coordinator", "@modelcontextprotocol/sdk"],
  // No shebang banner here: tsup preserves the one in src/cli.ts, and adding
  // a second puts it on line 2 of the bundle, where it is a syntax error
  // rather than a shebang.
});
