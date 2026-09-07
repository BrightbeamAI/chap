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
  // zod must stay external. It is bundled otherwise, which inflates every
  // entry point by around 550 KB and, worse, gives the process a second zod
  // instance: the request schemas built here are handed to the MCP SDK, which
  // checks them against its own copy, and two copies do not satisfy each
  // other's instanceof. It is declared as a dependency on the same range the
  // SDK asks for, so npm resolves one shared copy.
  external:  ["@brightbeamai/chap-coordinator", "@modelcontextprotocol/sdk", "zod"],
  // No shebang banner here: tsup preserves the one in src/cli.ts, and adding
  // a second puts it on line 2 of the bundle, where it is a syntax error
  // rather than a shebang.
});
