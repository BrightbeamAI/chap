import { defineConfig } from "tsup";

export default defineConfig({
  entry: {
    index:            "src/index.ts",
    jsonrpc:          "src/jsonrpc.ts",
    patch:            "src/patch.ts",
    types:            "src/types.ts",
    methods:          "src/methods.ts",
    api:              "src/api.ts",
    "storage/store":  "src/storage/store.ts",
    "storage/sqlite": "src/storage/sqlite.ts",
  },
  format:   ["esm", "cjs"],
  dts:      true,
  splitting: false,
  sourcemap: true,
  clean:     true,
  target:    "es2022",
  external:  ["better-sqlite3"],
});
