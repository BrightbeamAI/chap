import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index:    "src/index.ts",
    card:     "src/card.ts",
    executor: "src/executor.ts",
  },
  format:    ["esm", "cjs"],
  dts:       true,
  splitting: false,
  sourcemap: true,
  clean:     true,
  target:    "es2022",
  external:  ["@brightbeamai/coordinator", "@brightbeamai/coordinator-mcp", "@a2a-js/sdk"],
});
