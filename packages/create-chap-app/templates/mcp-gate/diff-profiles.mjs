import { readFile } from "node:fs/promises";
import { main } from "@brightbeamai/chap-server/profiles-diff";
import { generateSigner } from "@brightbeamai/chap-server/client";
const config = JSON.parse(await readFile(new URL("./chap.config.json", import.meta.url), "utf8"));
await main({ template: "mcp-gate", defaults: config.profiles, generateSigner });
