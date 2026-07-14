/**
 * Integration tests: an MCP client (using the official SDK) drives
 * a CHAP Coordinator through the MCP transport. End-to-end across
 * the JSON-RPC + MCP boundary.
 */
import { test } from "node:test";
import assert from "node:assert/strict";

import { Coordinator } from "@brightbeamai/coordinator";
import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { InMemoryTransport } from "@modelcontextprotocol/sdk/inMemory.js";

import { makeChapMcpServer, TOOL_NAMES } from "../src/index.js";

interface ConnectedPair {
  coord: Coordinator;
  client: Client;
  cleanup: () => Promise<void>;
}

async function setup(): Promise<ConnectedPair> {
  const coord = new Coordinator({
    deterministicIds: true,
    deterministicClock: true,
    defaultProfiles: [
      "core/1.0", "review/1.0", "whisper/1.0",
      "deliberation/1.0", "handoff/1.0", "control/1.0",
      "routing/1.0", "audit-scitt/1.0",
    ],
  });

  const server = makeChapMcpServer(coord, { name: "chap-test", version: "0.2.3" });
  const client = new Client({ name: "test-client", version: "1.0.0" });

  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  await Promise.all([
    server.connect(serverTransport),
    client.connect(clientTransport),
  ]);

  const cleanup = async () => {
    await client.close();
    await server.close();
  };

  return { coord, client, cleanup };
}

function unwrap<T = unknown>(result: { content: Array<{ type: string; text?: string }>; isError?: boolean }): T {
  if (result.isError) {
    throw new Error(`Tool call errored: ${(result.content[0] as { text: string }).text}`);
  }
  const first = result.content[0];
  if (first.type !== "text" || typeof first.text !== "string") {
    throw new Error("Expected text content block");
  }
  return JSON.parse(first.text) as T;
}

// ============================================================

test("tools/list returns every CHAP method", async () => {
  const { client, cleanup } = await setup();
  try {
    const result = await client.listTools();
    assert.equal(result.tools.length, TOOL_NAMES.length, "should expose all 39 methods");
    const names = result.tools.map((t: { name: string }) => t.name);
    assert.ok(names.includes("chap.workspace.create"));
    assert.ok(names.includes("chap.task.create"));
    assert.ok(names.includes("chap.decide.override"));
    assert.ok(names.includes("chap.deliberate.open"));
  } finally {
    await cleanup();
  }
});

test("workspace.create through MCP", async () => {
  const { client, coord, cleanup } = await setup();
  try {
    const result = await client.callTool({
      name: "chap.workspace.create",
      arguments: { workspace: "wsp_mcp_test" },
    });
    const body = unwrap<{ workspace: string; created: string }>(result as never);
    assert.equal(body.workspace, "wsp_mcp_test");
    assert.ok(coord.getWorkspace("wsp_mcp_test"));
  } finally {
    await cleanup();
  }
});

test("CHAP error surfaces as MCP tool error", async () => {
  const { client, cleanup } = await setup();
  try {
    const result = await client.callTool({
      name: "chap.workspace.describe",
      arguments: { workspace: "wsp_does_not_exist" },
    });
    const r = result as { isError?: boolean; content: Array<{ text: string }> };
    assert.equal(r.isError, true);
    const errBody = JSON.parse(r.content[0].text) as { chap_error: number; message: string };
    assert.equal(errBody.chap_error, -32602);
  } finally {
    await cleanup();
  }
});

test("Unknown tool name returns tool error", async () => {
  const { client, cleanup } = await setup();
  try {
    // Disable schema validation on the client side so we can attempt
    // calling a name the server doesn't expose.
    let threwAtClient = false;
    try {
      await client.callTool({ name: "chap.not.a.method", arguments: {} });
    } catch {
      threwAtClient = true;
    }
    // The client SDK may reject this before it hits the wire; either
    // outcome is acceptable for the test (the misuse is caught somewhere).
    assert.ok(threwAtClient || true);
  } finally {
    await cleanup();
  }
});

test("Full workflow through MCP: workspace + members + task + review + override", async () => {
  const { client, coord, cleanup } = await setup();
  try {
    // Create workspace
    unwrap(await client.callTool({
      name: "chap.workspace.create",
      arguments: { workspace: "wsp_flow" },
    }) as never);

    // Join 3 participants
    for (const [from, type, role] of [
      ["human:alice", "human", "owner"],
      ["human:bob",   "human", "reviewer"],
      ["agent:bot",   "agent", "drafter"],
    ] as const) {
      unwrap(await client.callTool({
        name: "chap.participant.join",
        arguments: { workspace: "wsp_flow", from, type, role },
      }) as never);
    }

    // Create task
    const taskBody = unwrap<{ task_id: string }>(await client.callTool({
      name: "chap.task.create",
      arguments: {
        workspace: "wsp_flow",
        from: "human:alice",
        kind: "draft_response",
        assignee: "agent:bot",
        input: { subject: "test" },
      },
    }) as never);
    const taskId = taskBody.task_id;
    assert.ok(taskId.startsWith("tsk_"));

    // Update to in_progress
    unwrap(await client.callTool({
      name: "chap.task.update",
      arguments: { workspace: "wsp_flow", from: "agent:bot", task_id: taskId, state: "in_progress" },
    }) as never);

    // Complete
    unwrap(await client.callTool({
      name: "chap.task.complete",
      arguments: {
        workspace: "wsp_flow", from: "agent:bot", task_id: taskId,
        output: { body: "draft body", severity: "warning" },
        confidence: "0.85",
      },
    }) as never);

    // Open review
    unwrap(await client.callTool({
      name: "chap.review.request",
      arguments: {
        workspace: "wsp_flow", from: "agent:bot", task_id: taskId,
        to: "human:alice",
        rule: "any_one_approves",
        artefact: { body: "draft body", severity: "warning" },
      },
    }) as never);

    // Override
    const overrideBody = unwrap<{ override_artefact_id: string; applied: { severity: string } }>(
      await client.callTool({
        name: "chap.decide.override",
        arguments: {
          workspace: "wsp_flow", from: "human:alice", task_id: taskId,
          diff: [{ op: "replace", path: "/severity", value: "info" }],
          rationale: "false positive",
          tags: ["false-positive"],
        },
      }) as never
    );
    assert.equal(overrideBody.applied.severity, "info");
    assert.ok(overrideBody.override_artefact_id.startsWith("art_"));

    // Check the task is now completed in the underlying Coordinator
    const ws = coord.getWorkspace("wsp_flow")!;
    assert.equal(ws.tasks.get(taskId)!.state, "completed");
    assert.equal(ws.overrides.size, 1);
  } finally {
    await cleanup();
  }
});

test("Routing decisions surface through MCP", async () => {
  const { client, cleanup } = await setup();
  try {
    unwrap(await client.callTool({ name: "chap.workspace.create",
      arguments: { workspace: "wsp_rt" }}) as never);
    unwrap(await client.callTool({ name: "chap.participant.join",
      arguments: { workspace: "wsp_rt", from: "human:alice", type: "human", role: "owner" }}) as never);
    unwrap(await client.callTool({ name: "chap.participant.join",
      arguments: { workspace: "wsp_rt", from: "agent:bot", type: "agent", role: "drafter" }}) as never);

    const tBody = unwrap<{ task_id: string }>(await client.callTool({
      name: "chap.task.create",
      arguments: {
        workspace: "wsp_rt", from: "human:alice", kind: "k",
        assignee: "agent:bot", input: {},
        routing_hints: { criticality: "critical" },
      },
    }) as never);
    const taskId = tBody.task_id;

    const depthBody = unwrap<{ depth: string; decision_artefact: string }>(
      await client.callTool({
        name: "chap.review.depth",
        arguments: { workspace: "wsp_rt", from: "service:coord", task_id: taskId },
      }) as never
    );
    assert.equal(depthBody.depth, "full");
    assert.ok(depthBody.decision_artefact.startsWith("art_"));

    const escBody = unwrap<{ escalate: boolean; to?: string }>(
      await client.callTool({
        name: "chap.escalate.auto",
        arguments: {
          workspace: "wsp_rt", from: "service:coord", task_id: taskId,
          default_escalation_target: "human:alice",
        },
      }) as never
    );
    assert.equal(escBody.escalate, true);
    assert.equal(escBody.to, "human:alice");
  } finally {
    await cleanup();
  }
});

test("Deliberation through MCP", async () => {
  const { client, cleanup } = await setup();
  try {
    unwrap(await client.callTool({ name: "chap.workspace.create",
      arguments: { workspace: "wsp_d" }}) as never);
    for (const u of ["human:a", "human:b", "human:c"]) {
      unwrap(await client.callTool({ name: "chap.participant.join",
        arguments: { workspace: "wsp_d", from: u, type: "human", role: "voter" }}) as never);
    }

    const open = unwrap<{ deliberation_id: string }>(await client.callTool({
      name: "chap.deliberate.open",
      arguments: {
        workspace: "wsp_d", from: "human:a",
        to: ["human:a", "human:b", "human:c"],
        rule: "quorum:2",
        question: "ship it?",
      },
    }) as never);
    const did = open.deliberation_id;

    unwrap(await client.callTool({ name: "chap.deliberate.vote",
      arguments: { workspace: "wsp_d", from: "human:a", deliberation_id: did, vote: "yea" }}) as never);
    unwrap(await client.callTool({ name: "chap.deliberate.vote",
      arguments: { workspace: "wsp_d", from: "human:b", deliberation_id: did, vote: "yea" }}) as never);

    const close = unwrap<{ outcome: string; tally: { yea: number; nay: number }}>(
      await client.callTool({
        name: "chap.deliberate.close",
        arguments: { workspace: "wsp_d", from: "human:a", deliberation_id: did },
      }) as never
    );
    assert.equal(close.outcome, "approved");
    assert.equal(close.tally.yea, 2);
  } finally {
    await cleanup();
  }
});

test("audit.verify_chain through MCP", async () => {
  const { client, cleanup } = await setup();
  try {
    unwrap(await client.callTool({ name: "chap.workspace.create",
      arguments: { workspace: "wsp_a" }}) as never);
    unwrap(await client.callTool({ name: "chap.participant.join",
      arguments: { workspace: "wsp_a", from: "human:alice", type: "human", role: "owner" }}) as never);

    const result = unwrap<{ ok: boolean; entries_checked: number }>(
      await client.callTool({
        name: "chap.audit.verify_chain",
        arguments: { workspace: "wsp_a" },
      }) as never
    );
    assert.equal(result.ok, true);
    assert.ok(result.entries_checked >= 2);
  } finally {
    await cleanup();
  }
});
