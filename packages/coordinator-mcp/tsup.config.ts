import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index:   "src/index.ts",
    schemas: "src/schemas.ts",
    tools:   "src/tools.ts",
  },
  format:    ["esm", "cjs"],
  dts:       true,
  splitting: false,
  sourcemap: true,
  clean:     true,
  target:    "es2022",
  external:  ["@brightbeamai/coordinator", "@modelcontextprotocol/sdk"],
});
