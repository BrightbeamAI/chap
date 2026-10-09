import { readFile } from "node:fs/promises";
import { main } from "./lib/profiles-diff.mjs";
import { generateSigner } from "./desk/chap-client.mjs";
const config = JSON.parse(await readFile(new URL("./chap.config.json", import.meta.url), "utf8"));
await main({ template: "mcp-gate", defaults: config.profiles, generateSigner });
