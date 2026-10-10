// Model providers. One HTTP call each, no vendor SDK.
//
// The provider is chosen by CHAP_MODEL_PROVIDER, or by the keys present:
//
//   anthropic   ANTHROPIC_API_KEY, Messages API, model ANTHROPIC_MODEL
//   openai      OPENAI_API_KEY, Responses API, model OPENAI_MODEL
//   ollama      OLLAMA_URL (or OLLAMA_MODEL), local model
//   scripted    none of the above: a deterministic drafter with no network
//
// The scripted provider keeps the project runnable with nothing installed
// and nothing configured. Each provider returns the model's text and the
// time the call took; the caller parses the text. This is the playground's
// providers.ts as plain JavaScript, with the same behaviour.

/**
 * @typedef {"anthropic" | "openai" | "ollama" | "scripted"} ProviderName
 * @typedef {{ text: string, latency_ms: number, model_id: string }} Completion
 * @typedef {{
 *   name: ProviderName,
 *   model_id: string,
 *   detail: string,
 *   complete: (prompt: string) => Promise<Completion>,
 *   probe: () => Promise<{ ok: boolean, detail: string }>,
 * }} Provider
 */

const env = (name) => {
  const v = process.env[name];
  return v && v.trim() ? v.trim() : undefined;
};

/**
 * A model's name as people say it, from the id a provider takes:
 * claude-opus-5-5 is "Claude Opus 5.5", claude-3-5-sonnet-20241022 is
 * "Claude 3.5 Sonnet", gpt-5.5 is "GPT-5.5". A cloud provider's form of a
 * Claude id (us.anthropic.claude-sonnet-4-5-20250929-v1:0,
 * claude-sonnet-4-5@20250929) and a context-window suffix such as [1m] read
 * the same. Any other id is kept as it is. The scripted drafter is named as
 * what it is.
 * @param {string} id
 */
export function modelName(id) {
  if (!id) return id;
  if (id === "scripted") return "scripted drafter (no model)";
  const bare = String(id).trim().replace(/\[[^\]]*\]$/, "").replace(/^(?:[a-z]{2,4}\.)?anthropic\./, "").replace(/-v\d+(?::\d+)?$/, "").replace(/@\d{8}$/, "");
  const cap = (w) => `${w[0].toUpperCase()}${w.slice(1)}`;
  const claude = /^claude-([a-z]+)-(\d+)(?:-(\d{1,2}))?(?:-\d{8})?$/.exec(bare);
  if (claude) return `Claude ${cap(claude[1])} ${claude[2]}${claude[3] ? `.${claude[3]}` : ""}`;
  const older = /^claude-(\d+)(?:-(\d))?-([a-z]+)(?:-\d{8})?$/.exec(bare);
  if (older) return `Claude ${older[1]}${older[2] ? `.${older[2]}` : ""} ${cap(older[3])}`;
  const gpt = /^gpt-(.+)$/i.exec(bare);
  if (gpt) return `GPT-${gpt[1]}`;
  return bare;
}

/** @returns {ProviderName} */
export function providerName() {
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
 * The scripted drafter is given the whole prompt and answers from the text
 * inside it. The caller supplies the function because it knows the shape it
 * wants back.
 * @param {(prompt: string) => string} scripted
 * @returns {Provider}
 */
export function makeProvider(scripted) {
  const name = providerName();
  switch (name) {
    case "anthropic": return anthropic();
    case "openai":    return openai();
    case "ollama":    return ollama();
    default:          return scriptedProvider(scripted);
  }
}

function timed(fn) {
  const t0 = Date.now();
  return fn().then((value) => ({ value, latency_ms: Date.now() - t0 }));
}

function anthropic() {
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
        const data = await res.json();
        return (data.content ?? []).filter((c) => c.type === "text").map((c) => c.text ?? "").join("");
      });
      return { text: value, latency_ms, model_id };
    },
    async probe() {
      return { ok: true, detail: `Anthropic ${model_id}, key set` };
    },
  };
}

function openai() {
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
        const data = await res.json();
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

function ollama() {
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
        const data = await res.json();
        return data.response;
      });
      return { text: value, latency_ms, model_id };
    },
    async probe() {
      try {
        const res = await fetch(`${base}/api/tags`);
        if (!res.ok) return { ok: false, detail: `Ollama at ${base} returned ${res.status}` };
        const data = await res.json();
        const has = data.models?.some((m) => m.name.startsWith(model_id.split(":")[0]));
        if (!has) return { ok: false, detail: `Ollama is reachable and ${model_id} is not pulled. Run: ollama pull ${model_id}` };
        return { ok: true, detail: `Ollama ${model_id} at ${base}` };
      } catch (e) {
        return { ok: false, detail: `Cannot reach Ollama at ${base}: ${e instanceof Error ? e.message : String(e)}` };
      }
    },
  };
}

function scriptedProvider(draft) {
  const model_id = "scripted";
  return {
    name: "scripted", model_id,
    detail: "scripted agent, no model. Set ANTHROPIC_API_KEY, OPENAI_API_KEY or OLLAMA_URL to draft with a model",
    async complete(prompt) {
      return { text: draft(prompt), latency_ms: 0, model_id };
    },
    async probe() {
      return { ok: true, detail: "scripted agent" };
    },
  };
}
