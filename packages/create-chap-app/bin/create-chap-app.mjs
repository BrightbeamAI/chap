#!/usr/bin/env node
// The npm bin entry. npm links this file under a different name, so the
// generator's own "run when executed directly" guard is not used here.
import { main } from "../index.mjs";

main().then((code) => process.exit(code)).catch((e) => { console.error(e.message); process.exit(1); });
