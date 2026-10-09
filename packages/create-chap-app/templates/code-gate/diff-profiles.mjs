// The gate's workload under two profile sets, side by side.
//
//   node diff-profiles.mjs --against core/1.0
//   node diff-profiles.mjs --json --against core/1.0,review/1.0
//
// The decisions here are scripted, because this is a comparison and no
// person is at the desk. In the project itself the decision is made in
// the desk.
import { readFile } from "node:fs/promises";
import { generateSigner } from "./desk/chap-client.mjs";
import { main } from "./lib/profiles-diff.mjs";

const config = JSON.parse(await readFile(new URL("./chap.config.json", import.meta.url), "utf8"));
// The comparison workload presents no OIDC token, and a workspace cannot
// advertise identity-oidc/1.0 without a verifier, so that profile is left
// out of the comparison when it is configured.
const defaults = config.profiles.filter((p) => !p.startsWith("identity-oidc/"));
await main({ template: "code-gate", defaults, generateSigner });
