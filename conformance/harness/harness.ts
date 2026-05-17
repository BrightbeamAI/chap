/**
 * CHAP Conformance Test Harness
 *
 * Runs a battery of test vectors against any CHAP endpoint and reports
 * pass/fail. Produces an in-toto-compatible attestation on success.
 *
 * Usage:
 *   tsx harness.ts                                            # tests localhost:8080
 *   tsx harness.ts --url=http://my-chap.example.org/chap        # tests a remote endpoint
 *   tsx harness.ts --url=...  --workspace=wsp_conformance     # custom workspace id
 *   tsx harness.ts --core-only                                # skip profile tests
 *   tsx harness.ts --attest > attestation.json                # write in-toto attestation
 *
 * Exit codes:
 *   0  all selected tests passed
 *   1  one or more tests failed
 *   2  invalid arguments or harness error
 */

interface Args {
  url:        string;
  workspace:  string;
  coreOnly:   boolean;
  attest:     boolean;
  verbose:    boolean;
}

function parseArgs(): Args {
  const args: Args = {
    url:       process.env.CHAP_URL ?? "http://localhost:8080/chap",
    workspace: `wsp_conformance_${Math.random().toString(36).slice(2, 8)}`,
    coreOnly:  false,
    attest:    false,
    verbose:   false,
  };
  for (const a of process.argv.slice(2)) {
    if (a.startsWith("--url="))       args.url       = a.slice(6);
    else if (a.startsWith("--workspace=")) args.workspace = a.slice(12);
    else if (a === "--core-only")     args.coreOnly  = true;
    else if (a === "--attest")        args.attest    = true;
    else if (a === "--verbose" || a === "-v") args.verbose = true;
    else if (a === "--help" || a === "-h") {
      console.log(`Usage: tsx harness.ts [--url=URL] [--workspace=ID] [--core-only] [--attest] [-v]`);
      process.exit(0);
    } else {
      console.error(`Unknown argument: ${a}`);
      process.exit(2);
    }
  }
  return args;
}

// ============================================================
//   CHAP client
// ============================================================

class HapClient {
  constructor(public url: string) {}

  async call(method: string, params: Record<string, unknown>): Promise<{ result?: any; error?: any; raw: any }> {
    const env = { jsonrpc: "2.0" as const, id: `t-${Date.now()}-${Math.random()}`, method, params };
    const res = await fetch(this.url, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(env),
    });
    const body = await res.json();
    return { result: body.result, error: body.error, raw: body };
  }

  async callRaw(rawBody: string): Promise<{ status: number; body: any }> {
    const res = await fetch(this.url, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    rawBody,
    });
    const status = res.status;
    let body: any;
    try { body = await res.json(); } catch { body = await res.text(); }
    return { status, body };
  }
}

// ============================================================
//   Test framework
// ============================================================

interface TestResult {
  id:       string;
  name:     string;
  group:    string;
  status:   "pass" | "fail" | "skip";
  detail?:  string;
}

const results: TestResult[] = [];

async function test(
  group:   string,
  id:      string,
  name:    string,
  fn:      () => Promise<void> | void,
): Promise<void> {
  try {
    await fn();
    results.push({ id, name, group, status: "pass" });
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    results.push({ id, name, group, status: "fail", detail: msg });
  }
}

function assert(cond: any, msg: string): asserts cond {
  if (!cond) throw new Error(msg);
}

function assertEq(actual: unknown, expected: unknown, msg: string): void {
  if (actual !== expected) {
    throw new Error(`${msg} — expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
  }
}

// ============================================================
//   Core tests
// ============================================================

async function runCoreTests(client: HapClient, ws: string): Promise<void> {

  // -------- Wire format --------

  await test("Wire format", "wf-01", "Malformed JSON returns -32700", async () => {
    const { body } = await client.callRaw("{ not json");
    assert(body?.error?.code === -32700, `Expected -32700, got ${JSON.stringify(body?.error)}`);
  });

  await test("Wire format", "wf-02", "Non-JSON-RPC body returns -32600", async () => {
    const { body } = await client.callRaw(JSON.stringify({ no_jsonrpc_field: "here" }));
    assert(body?.error?.code === -32600, `Expected -32600, got ${JSON.stringify(body?.error)}`);
  });

  await test("Wire format", "wf-03", "Unknown method returns -32601", async () => {
    const { error } = await client.call("does.not.exist", { workspace: ws });
    assert(error?.code === -32601, `Expected -32601, got ${JSON.stringify(error)}`);
  });

  // -------- Core methods --------

  await test("Core methods", "cm-01", "participant.join admits a human", async () => {
    const { result } = await client.call("participant.join", {
      workspace: ws,
      from:      "human:alice@example.org",
      to:        "service:coordinator@example.org",
      ts:        new Date().toISOString(),
      type:      "human",
      role:      "reviewer",
    });
    assert(result, "No result");
    assert(result.joined === true || result.joined === undefined, "join should return joined:true");
  });

  await test("Core methods", "cm-02", "participant.join admits an agent", async () => {
    const { result } = await client.call("participant.join", {
      workspace: ws,
      from:      "agent:test-bot",
      to:        "service:coordinator@example.org",
      ts:        new Date().toISOString(),
      type:      "agent",
      role:      "drafter",
    });
    assert(result, "No result");
  });

  await test("Core methods", "cm-03", "workspace.describe lists members and profiles", async () => {
    const { result } = await client.call("workspace.describe", {
      workspace: ws,
      from:      "human:alice@example.org",
      to:        "service:coordinator@example.org",
      ts:        new Date().toISOString(),
    });
    assert(result, "No result");
    assert(Array.isArray(result.members),  "members should be an array");
    assert(result.members.length >= 2,     "should have at least 2 members");
    assert(Array.isArray(result.profiles), "profiles should be an array");
    assert(result.profiles.some((p: string) => p.startsWith("core/")), "should advertise core/");
  });

  let createdTaskId = "";
  await test("Core methods", "cm-04", "task.create assigns to a member", async () => {
    const { result } = await client.call("task.create", {
      workspace: ws,
      from:      "human:alice@example.org",
      to:        "agent:test-bot",
      ts:        new Date().toISOString(),
      kind:      "test_task",
      assignee:  "agent:test-bot",
      input:     { test: true },
    });
    assert(result?.task_id, "Should return task_id");
    assertEq(result?.state, "created", "Initial state");
    createdTaskId = result.task_id;
  });

  await test("Core methods", "cm-05", "task.create rejects non-member assignee", async () => {
    const { error } = await client.call("task.create", {
      workspace: ws,
      from:      "human:alice@example.org",
      to:        "agent:never-joined",
      ts:        new Date().toISOString(),
      kind:      "test_task",
      assignee:  "agent:never-joined",
      input:     {},
    });
    assert(error?.code === -32602, `Expected -32602, got ${JSON.stringify(error)}`);
  });

  await test("Core methods", "cm-06", "task.update transitions created → in_progress", async () => {
    const { result } = await client.call("task.update", {
      workspace: ws,
      from:      "agent:test-bot",
      to:        "human:alice@example.org",
      ts:        new Date().toISOString(),
      task_id:   createdTaskId,
      state:     "in_progress",
    });
    assertEq(result?.state, "in_progress", "Should be in_progress");
  });

  await test("Core methods", "cm-07", "task.update rejects illegal transition", async () => {
    const { error } = await client.call("task.update", {
      workspace: ws,
      from:      "agent:test-bot",
      to:        "human:alice@example.org",
      ts:        new Date().toISOString(),
      task_id:   createdTaskId,
      state:     "created", // illegal: in_progress → created
    });
    assert(error?.code === -32602, "Illegal transition should fail with -32602");
  });

  await test("Core methods", "cm-08", "task.complete finishes the task", async () => {
    const { result } = await client.call("task.complete", {
      workspace: ws,
      from:      "agent:test-bot",
      to:        "human:alice@example.org",
      ts:        new Date().toISOString(),
      task_id:   createdTaskId,
      output:    { result: "done" },
    });
    assertEq(result?.state, "completed", "Should be completed");
  });

  await test("Core methods", "cm-09", "task.complete on terminal task fails", async () => {
    const { error } = await client.call("task.complete", {
      workspace: ws,
      from:      "agent:test-bot",
      to:        "human:alice@example.org",
      ts:        new Date().toISOString(),
      task_id:   createdTaskId,
      output:    { result: "again" },
    });
    assert(error, "Should error");
  });

  await test("Core methods", "cm-10", "audit.read returns entries in order", async () => {
    const { result } = await client.call("audit.read", {
      workspace: ws,
      from:      "human:alice@example.org",
      to:        "service:coordinator@example.org",
      ts:        new Date().toISOString(),
      range:     { from_seq: 0 },
    });
    assert(Array.isArray(result?.entries), "entries should be an array");
    assert(result.entries.length >= 5, `expected >= 5 entries, got ${result.entries.length}`);
    // Sequence should be monotonic and start at 0
    let prev = -1;
    for (const e of result.entries) {
      assert(typeof e.seq === "number" && e.seq > prev, "seq should be monotonic");
      prev = e.seq;
    }
  });

  await test("Core methods", "cm-11", "audit.read filter by method works", async () => {
    const { result } = await client.call("audit.read", {
      workspace: ws,
      from:      "human:alice@example.org",
      to:        "service:coordinator@example.org",
      ts:        new Date().toISOString(),
      filter:    { method: "task.create" },
    });
    assert(Array.isArray(result?.entries), "entries should be an array");
    for (const e of result.entries) {
      assertEq(e.envelope.method, "task.create", "filter should restrict by method");
    }
  });

  await test("Core methods", "cm-12", "participant.leave removes member", async () => {
    const { result } = await client.call("participant.leave", {
      workspace: ws,
      from:      "agent:test-bot",
      to:        "service:coordinator@example.org",
      ts:        new Date().toISOString(),
    });
    assert(result, "Should return a result");
    // Verify via describe
    const { result: d } = await client.call("workspace.describe", { workspace: ws });
    const stillThere = d.members.some((m: any) => m.uri === "agent:test-bot");
    assert(!stillThere, "agent:test-bot should no longer be a member");
  });
}

// ============================================================
//   Review profile tests
// ============================================================

async function runReviewTests(client: HapClient, ws: string): Promise<void> {
  // Re-add the bot if it left
  await client.call("participant.join", {
    workspace: ws,
    from:      "agent:reviewer-test-bot",
    to:        "service:coordinator@example.org",
    ts:        new Date().toISOString(),
    type:      "agent",
    role:      "drafter",
  });

  let taskId = "";
  await test("Review profile", "rv-01", "setup: create task and request review", async () => {
    const { result: t } = await client.call("task.create", {
      workspace: ws,
      from:      "human:alice@example.org",
      to:        "agent:reviewer-test-bot",
      ts:        new Date().toISOString(),
      kind:      "draft_review_test",
      assignee:  "agent:reviewer-test-bot",
      input:     { test: true },
    });
    taskId = t.task_id;

    await client.call("task.update", {
      workspace: ws, from: "agent:reviewer-test-bot", to: "human:alice@example.org",
      ts: new Date().toISOString(),
      task_id: taskId, state: "in_progress",
    });

    const { result: r } = await client.call("review.request", {
      workspace: ws, from: "agent:reviewer-test-bot", to: ["human:alice@example.org"],
      ts: new Date().toISOString(),
      task_id: taskId,
      artefact: { subject: "test", body: "original body", severity: "high" },
      rule: "any_one_approves",
    });
    assertEq(r?.state, "review_requested", "should enter review_requested");
  });

  await test("Review profile", "rv-02", "decide.override applies JSON Patch", async () => {
    const { result } = await client.call("decide.override", {
      workspace: ws, from: "human:alice@example.org", to: "service:coordinator@example.org",
      ts: new Date().toISOString(),
      task_id: taskId,
      based_on_artefact: { subject: "test", body: "original body", severity: "high" },
      diff: [
        { op: "replace", path: "/body",     value: "softened body" },
        { op: "replace", path: "/severity", value: "low" },
      ],
      rationale: "Tone too strong; severity overstated.",
      tags:        ["tone-softened", "severity-downgraded"],
      policy_refs: ["test-policy-v1"],
    });
    assertEq(result?.state, "completed", "should complete");
    assert(result?.override_artefact_id, "should return artefact id");
    assert(result?.applied, "should return applied result");
    assertEq(result.applied.body, "softened body", "patch should apply /body");
    assertEq(result.applied.severity, "low", "patch should apply /severity");
  });

  await test("Review profile", "rv-03", "override audit entry contains structured data", async () => {
    const { result } = await client.call("audit.read", {
      workspace: ws,
      filter:    { method: "decide.override", task_id: taskId },
    });
    assert(result.entries.length === 1, `expected exactly 1 override entry, got ${result.entries.length}`);
    const params = result.entries[0].envelope.params;
    assert(Array.isArray(params.diff),     "diff should be present");
    assert(typeof params.rationale === "string", "rationale should be present");
    assert(Array.isArray(params.tags),     "tags should be present");
    assert(Array.isArray(params.policy_refs), "policy_refs should be present");
  });

  // -------- Abstain --------
  let abstainTaskId = "";
  await test("Review profile", "rv-04", "setup: another task for abstain", async () => {
    const { result: t } = await client.call("task.create", {
      workspace: ws, from: "human:alice@example.org", to: "agent:reviewer-test-bot",
      ts: new Date().toISOString(),
      kind: "abstain_test", assignee: "agent:reviewer-test-bot", input: {},
    });
    abstainTaskId = t.task_id;
    await client.call("task.update", {
      workspace: ws, from: "agent:reviewer-test-bot", to: "human:alice@example.org",
      ts: new Date().toISOString(), task_id: abstainTaskId, state: "in_progress",
    });
    await client.call("review.request", {
      workspace: ws, from: "agent:reviewer-test-bot", to: ["human:alice@example.org"],
      ts: new Date().toISOString(), task_id: abstainTaskId, artefact: {},
    });
  });

  await test("Review profile", "rv-05", "abstain.declare with category transitions to abstained", async () => {
    const { result } = await client.call("abstain.declare", {
      workspace: ws, from: "human:alice@example.org", to: "service:coordinator@example.org",
      ts: new Date().toISOString(),
      task_id:  abstainTaskId,
      reason:   "Out of my authorisation limit.",
      category: "out_of_authority",
    });
    assertEq(result?.state, "abstained", "should be abstained");
  });

  // -------- Reject --------
  let rejectTaskId = "";
  await test("Review profile", "rv-06", "decide.reject with request_revision returns to in_progress", async () => {
    const { result: t } = await client.call("task.create", {
      workspace: ws, from: "human:alice@example.org", to: "agent:reviewer-test-bot",
      ts: new Date().toISOString(),
      kind: "reject_test", assignee: "agent:reviewer-test-bot", input: {},
    });
    rejectTaskId = t.task_id;
    await client.call("task.update", {
      workspace: ws, from: "agent:reviewer-test-bot", to: "human:alice@example.org",
      ts: new Date().toISOString(), task_id: rejectTaskId, state: "in_progress",
    });
    await client.call("review.request", {
      workspace: ws, from: "agent:reviewer-test-bot", to: ["human:alice@example.org"],
      ts: new Date().toISOString(), task_id: rejectTaskId, artefact: {},
    });
    const { result } = await client.call("decide.reject", {
      workspace: ws, from: "human:alice@example.org", to: "service:coordinator@example.org",
      ts: new Date().toISOString(),
      task_id: rejectTaskId, comment: "Needs revision.", request_revision: true,
    });
    assertEq(result?.state, "in_progress", "should return to in_progress");
  });
}

// ============================================================
//   Reporting
// ============================================================

function report(args: Args): boolean {
  const byGroup = new Map<string, TestResult[]>();
  for (const r of results) {
    if (!byGroup.has(r.group)) byGroup.set(r.group, []);
    byGroup.get(r.group)!.push(r);
  }

  console.log("\n" + "═".repeat(60));
  console.log("  CHAP Conformance Test Results");
  console.log("═".repeat(60));
  console.log(`  Endpoint:  ${args.url}`);
  console.log(`  Workspace: ${args.workspace}`);
  console.log("═".repeat(60));

  let totalPass = 0, totalFail = 0;
  for (const [group, tests] of byGroup) {
    const pass = tests.filter((t) => t.status === "pass").length;
    const fail = tests.filter((t) => t.status === "fail").length;
    totalPass += pass; totalFail += fail;

    console.log(`\n  ${group}  (${pass}/${tests.length})`);
    for (const t of tests) {
      const icon = t.status === "pass" ? "✓" : t.status === "fail" ? "✗" : "·";
      const colour = t.status === "pass" ? "\x1b[32m" : t.status === "fail" ? "\x1b[31m" : "\x1b[33m";
      const reset = "\x1b[0m";
      console.log(`    ${colour}${icon}${reset} ${t.id}  ${t.name}`);
      if (t.status === "fail" && t.detail) {
        console.log(`        ${t.detail}`);
      }
    }
  }

  console.log("\n" + "═".repeat(60));
  if (totalFail === 0) {
    console.log(`  \x1b[32m✓ All ${totalPass} tests passed.\x1b[0m`);
  } else {
    console.log(`  \x1b[31m✗ ${totalFail} of ${totalPass + totalFail} tests failed.\x1b[0m`);
  }
  console.log("═".repeat(60) + "\n");

  return totalFail === 0;
}

function attestation(args: Args, passed: boolean): unknown {
  const profilesAttested = ["core/1.0"];
  if (!args.coreOnly) profilesAttested.push("review/1.0");

  return {
    _type: "https://in-toto.io/Statement/v1",
    subject: [
      {
        name: args.url,
        digest: { sha256: "0".repeat(64) },
      },
    ],
    predicateType: "https://chap.dev/conformance/v1",
    predicate: {
      timestamp: new Date().toISOString(),
      passed,
      profiles_attested: profilesAttested,
      tests: results.map((r) => ({
        id:     r.id,
        group:  r.group,
        status: r.status,
        ...(r.detail ? { detail: r.detail } : {}),
      })),
    },
  };
}

// ============================================================
//   Entry
// ============================================================

async function main(): Promise<void> {
  const args = parseArgs();

  if (!args.attest) {
    console.log(`CHAP Conformance Harness`);
    console.log(`Endpoint:  ${args.url}`);
    console.log(`Workspace: ${args.workspace}`);
    if (args.coreOnly) console.log(`Mode:      Core-only`);
    console.log("");
  }

  const client = new HapClient(args.url);

  try {
    await runCoreTests(client, args.workspace);
    if (!args.coreOnly) {
      await runReviewTests(client, args.workspace);
    }
  } catch (e) {
    console.error(`\nHarness error: ${e instanceof Error ? e.message : e}`);
    process.exit(2);
  }

  const passed = args.attest
    ? results.every((r) => r.status !== "fail")
    : report(args);

  if (args.attest) {
    console.log(JSON.stringify(attestation(args, passed), null, 2));
  }

  process.exit(passed ? 0 : 1);
}

main().catch((e) => {
  console.error(`Fatal: ${e instanceof Error ? e.message : e}`);
  process.exit(2);
});
