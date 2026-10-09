import { readFile } from "node:fs/promises";
import { main } from "./lib/profiles-diff.mjs";
import { generateSigner } from "./desk/chap-client.mjs";
const config = JSON.parse(await readFile(new URL("./chap.config.json", import.meta.url), "utf8"));
// The comparison workload presents no OIDC token, and a workspace cannot
// advertise identity-oidc/1.0 without a verifier, so that profile is left
// out of the comparison. The tests cover what it changes.
const defaults = config.profiles.filter((p) => !p.startsWith("identity-oidc/"));
if (defaults.length !== config.profiles.length) console.error("identity-oidc/1.0 is left out of the comparison: the workload presents no token. See tests/production.test.mjs for what it changes.\n");
await main({ template: "production", defaults, generateSigner });
