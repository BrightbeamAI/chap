/**
 * Model providers. One HTTP call each, no vendor SDK.
 *
 * The provider is chosen by CHAP_MODEL_PROVIDER, or by the keys present:
 *
 *   anthropic   ANTHROPIC_API_KEY, Messages API, model ANTHROPIC_MODEL
 *   openai      OPENAI_API_KEY, Responses API, model OPENAI_MODEL
 *   ollama      OLLAMA_URL (or OLLAMA_MODEL), local model
 *   scripted    none of the above: a deterministic drafter with no network
 *
 * The scripted provider keeps every template and the playground runnable
 * with nothing installed and nothing configured. Each provider returns the
 * model's text and the time the call took; the caller parses the text.
 */

export type ProviderName = "anthropic" | "openai" | "ollama" | "scripted";

export interface Completion {
  text:       string;
  latency_ms: number;
  model_id:   string;
}

export interface Provider {
  name:     ProviderName;
  model_id: string;
  /** Short description for a console line or a health endpoint. */
  detail:   string;
  complete(prompt: string): Promise<Completion>;
  /** Whether the provider can be reached; the scripted provider always can. */
  probe():  Promise<{ ok: boolean; detail: string }>;
}

const env = (name: string): string | undefined => {
  const v = process.env[name];
  return v && v.trim() ? v.trim() : undefined;
};

export function providerName(): ProviderName {
  const chosen = env("CHAP_MODEL_PROVIDER")?.toLowerCase();
  if (chosen === "anthropic" || chosen === "openai" || chosen === "ollama" || chosen === "scripted") {
    return chosen;
  }
  if (chosen) throw new Error(`CHAP_MODEL_PROVIDER must be anthropic, openai, ollama or scripted, got ${chosen}`);
  if (env("ANTHROPIC_API_KEY")) return "anthropic";
  if (env("OPENAI_API_KEY")) return "openai";
  if (env("OLLAMA_URL") || env("OLLAMA_MODEL")) return "ollama";
  return "scripted";
}

/**
 * The scripted drafter. It is given the whole prompt and answers from the
 * ticket text inside it, so the routing policy sees varied signals. The
 * caller supplies the function because it knows the shape it wants back.
 */
export type ScriptedDrafter = (prompt: string) => string;

export function makeProvider(scripted: ScriptedDrafter): Provider {
  const name = providerName();
  switch (name) {
    case "anthropic": return anthropic();
    case "openai":    return openai();
    case "ollama":    return ollama();
    default:          return scriptedProvider(scripted);
  }
}

function timed<T>(fn: () => Promise<T>): Promise<{ value: T; latency_ms: number }> {
  const t0 = Date.now();
  return fn().then((value) => ({ value, latency_ms: Date.now() - t0 }));
}

function anthropic(): Provider {
  const key = env("ANTHROPIC_API_KEY");
  if (!key) throw new Error("CHAP_MODEL_PROVIDER=anthropic needs ANTHROPIC_API_KEY");
  const model_id = env("ANTHROPIC_MODEL") ?? "claude-haiku-5-5";
  const url = (env("ANTHROPIC_BASE_URL") ?? "https://api.anthropic.com") + "/v1/messages";
  return {
    name: "anthropic", model_id, detail: `Anthropic ${model_id}`,
    async complete(prompt) {
      const { value, latency_ms } = await timed(async () => {
        const res = await fetch(url, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
          },
          body: JSON.stringify({ model: model_id, max_tokens: 400, messages: [{ role: "user", content: prompt }] }),
        });
        if (!res.ok) throw new Error(`Anthropic returned ${res.status}: ${await res.text()}`);
        const data = await res.json() as { content?: { type: string; text?: string }[] };
        return (data.content ?? []).filter((c) => c.type === "text").map((c) => c.text ?? "").join("");
      });
      return { text: value, latency_ms, model_id };
    },
    async probe() {
      return { ok: true, detail: `Anthropic ${model_id}, key set` };
    },
  };
}

function openai(): Provider {
  const key = env("OPENAI_API_KEY");
  if (!key) throw new Error("CHAP_MODEL_PROVIDER=openai needs OPENAI_API_KEY");
  const model_id = env("OPENAI_MODEL") ?? "gpt-5.5";
  const url = (env("OPENAI_BASE_URL") ?? "https://api.openai.com") + "/v1/responses";
  return {
    name: "openai", model_id, detail: `OpenAI ${model_id}`,
    async complete(prompt) {
      const { value, latency_ms } = await timed(async () => {
        const res = await fetch(url, {
          method: "POST",
          headers: { "content-type": "application/json", authorization: `Bearer ${key}` },
          body: JSON.stringify({ model: model_id, max_output_tokens: 400, input: prompt }),
        });
        if (!res.ok) throw new Error(`OpenAI returned ${res.status}: ${await res.text()}`);
        const data = await res.json() as {
          output?: { type: string; content?: { type: string; text?: string }[] }[];
        };
        return (data.output ?? [])
          .filter((item) => item.type === "message")
          .flatMap((item) => item.content ?? [])
          .filter((c) => c.type === "output_text")
          .map((c) => c.text ?? "")
          .join("");
      });
      return { text: value, latency_ms, model_id };
    },
    async probe() {
      return { ok: true, detail: `OpenAI ${model_id}, key set` };
    },
  };
}

function ollama(): Provider {
  const base = env("OLLAMA_URL") ?? "http://localhost:11434";
  const model_id = env("OLLAMA_MODEL") ?? "gemma3:4b";
  return {
    name: "ollama", model_id, detail: `Ollama ${model_id} at ${base}`,
    async complete(prompt) {
      const { value, latency_ms } = await timed(async () => {
        const res = await fetch(`${base}/api/generate`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({ model: model_id, prompt, stream: false, options: { temperature: 0.6, num_predict: 200 } }),
        });
        if (!res.ok) throw new Error(`Ollama returned ${res.status}: ${await res.text()}`);
        const data = await res.json() as { response: string };
        return data.response;
      });
      return { text: value, latency_ms, model_id };
    },
    async probe() {
      try {
        const res = await fetch(`${base}/api/tags`);
        if (!res.ok) return { ok: false, detail: `Ollama at ${base} returned ${res.status}` };
        const data = await res.json() as { models?: { name: string }[] };
        const has = data.models?.some((m) => m.name.startsWith(model_id.split(":")[0]));
        if (!has) return { ok: false, detail: `Ollama is reachable and ${model_id} is not pulled. Run: ollama pull ${model_id}` };
        return { ok: true, detail: `Ollama ${model_id} at ${base}` };
      } catch (e) {
        return { ok: false, detail: `Cannot reach Ollama at ${base}: ${e instanceof Error ? e.message : String(e)}` };
      }
    },
  };
}

function scriptedProvider(draft: ScriptedDrafter): Provider {
  const model_id = "scripted";
  return {
    name: "scripted", model_id,
    detail: "scripted agent, no model. Set ANTHROPIC_API_KEY, OPENAI_API_KEY or OLLAMA_URL to draft with a model",
    async complete(prompt) {
      const text = draft(prompt);
      // A stable, small latency derived from the input so routing sees a signal.
      return { text, latency_ms: 120 + (prompt.length % 40), model_id };
    },
    async probe() {
      return { ok: true, detail: "scripted agent" };
    },
  };
}
