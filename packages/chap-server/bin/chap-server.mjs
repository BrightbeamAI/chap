#!/usr/bin/env node
// chap-server: run a CHAP project's server, write its agent's key, or start
// a development OIDC issuer. `chap-server --help` lists the commands.

import { readFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { serve as serveProject } from "../lib/server.mjs";
import { generateKeyFile, sshKeyFiles } from "../lib/keys.mjs";
import { startDevIssuer } from "../lib/dev-issuer.mjs";

const USAGE = `Usage:
  chap-server [--config <file>]               the desk, POST /chap, the read API and /mcp for a project
  chap-server keys [--uri <uri>] [--config <file>]
                                              the agent's signing key, written to keys/ beside the config
  chap-server dev-issuer [<port>]             a development OIDC issuer, for tests and local runs only

The project is the directory that holds its chap.config.json: --config names
the file, CHAP_CONFIG does the same, and by default it is chap.config.json in
the working directory.`;

/** The value after a flag, refused when it is missing. */
function valueAfter(argv, i, flag) {
  const v = argv[i + 1];
  if (v === undefined || v.startsWith("-")) throw new Error(`${flag} needs a value`);
  return v;
}

function parse(argv, flags) {
  const out = { positional: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--help" || a === "-h") out.help = true;
    else if (flags.includes(a)) { out[a.slice(2)] = valueAfter(argv, i, a); i++; }
    else if (a.startsWith("-")) throw new Error(`Unknown option ${a}. chap-server --help lists the options.`);
    else out.positional.push(a);
  }
  return out;
}

async function serve(argv) {
  const args = parse(argv, ["--config"]);
  if (args.help) return console.log(USAGE);
  if (args.positional.length) throw new Error(`Unexpected argument ${args.positional[0]}. chap-server --help lists the commands.`);
  await serveProject({ configPath: args.config });
}

async function keys(argv) {
  const args = parse(argv, ["--uri", "--config"]);
  if (args.help) return console.log(USAGE);
  const configPath = resolve(args.config ?? process.env.CHAP_CONFIG ?? "chap.config.json");
  let uri = args.uri ?? process.env.CHAP_AGENT_URI;
  if (!uri) {
    const config = JSON.parse(await readFile(configPath, "utf8"));
    uri = config.agent?.uri;
  }
  if (!uri) {
    console.error("No agent URI: set agent.uri in chap.config.json or pass --uri");
    process.exitCode = 2;
    return;
  }
  const { path, publicJwk, created } = await generateKeyFile(uri, join(dirname(configPath), "keys"));
  console.log(created ? `Key for ${uri} written to ${path} (mode 0600)` : `Key for ${uri} already at ${path}; left as it is`);
  console.log(`Public JWK, registered at participant.join:`);
  console.log(JSON.stringify(publicJwk, null, 2));
  const ssh = await sshKeyFiles(uri, path);
  console.log(`The same key for signing git commits: ${ssh.privatePath} and ${ssh.publicPath}; ${ssh.allowedSigners} names it.`);
  console.log(ssh.publicLine);
}

async function devIssuer(argv) {
  const args = parse(argv, []);
  if (args.help) return console.log(USAGE);
  const port = Number(args.positional[0] ?? 8791);
  const dev = await startDevIssuer(port);
  console.log("Development OIDC issuer. Tests and local runs only: it signs any claims it is asked to.");
  console.log(`OIDC_ISSUER=${dev.issuer}`);
  console.log(`OIDC_JWKS_URL=${dev.jwks_url}`);
  console.log(`OIDC_AUDIENCE=${dev.audience}`);
}

const [command, ...rest] = process.argv.slice(2);
const run = command === "keys" ? () => keys(rest)
  : command === "dev-issuer" ? () => devIssuer(rest)
  : () => serve(command === undefined ? [] : [command, ...rest]);
run().catch((e) => { console.error(e instanceof Error ? e.message : String(e)); process.exit(1); });
