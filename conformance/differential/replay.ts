import { Coordinator } from "../../packages/coordinator/src/index.ts";

async function main(): Promise<void> {
  const chunks: Buffer[] = [];
  for await (const c of process.stdin) chunks.push(c as Buffer);
  const req = JSON.parse(Buffer.concat(chunks).toString("utf-8")) as {
    options: { defaultProfiles: string[] };
    workspace: string;
    envelopes: unknown[];
  };
  const coord = new Coordinator({
    deterministicIds: true,
    deterministicClock: true,
    enableChain: true,
    defaultProfiles: req.options.defaultProfiles,
  });
  const responses = req.envelopes.map((e) => coord.dispatch(e as never));
  const chain_head = coord.workspaces.get(req.workspace)?.chain_head ?? null;
  process.stdout.write(JSON.stringify({ responses, chain_head }));
}

main().catch((e) => {
  process.stderr.write(String(e?.stack ?? e) + "\n");
  process.exit(1);
});
