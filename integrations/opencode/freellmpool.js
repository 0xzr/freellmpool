// freellmpool — OpenCode plugin (v2 API)
//
// Surfaces the freellmpool proxy *inside* OpenCode:
//   • freellmpool_status  — usage, $ saved, per-provider quota / cooldown / latency,
//                           and the most-recently-served provider+model.
//   • freellmpool_models  — the model ids the proxy currently exposes.
//   • freellmpool_tokenmax — fan the same prompt out across the pool (rainbow TOKENMAXXING).
//   • a best-effort toast/log of which provider+model actually served each reply.
//   • registers the `freellmpool` provider + routing-alias models in the model picker.
//
// Quality/routing control is done through OpenCode's own model picker: switch the
// model to `freellmpool/agent`, `/fast`, `/quality`, or `/fair` (the proxy maps the
// name to a routing mode). `freellmpool/auto` uses the proxy's default routing.
//
// Zero build step, zero extra deps: drop this file in ~/.config/opencode/plugins/ or
// reference it from opencode.json `plugins`. Config:
//   FREELLMPOOL_PROXY_URL         base URL (default http://localhost:8080)
//   FREELLMPOOL_PROXY_KEY         sent as `Authorization: Bearer <key>` if the proxy needs one
//   FREELLMPOOL_TOAST             set to 0/false/off to silence the served-model toast
//   FREELLMPOOL_MODELS_DISCOVERY  set to 1/true/on to enable modelsDiscovery in provider settings

import { Plugin, Provider, Model } from "@opencode/plugin";

const BASE_URL = (
  process.env.FREELLMPOOL_PROXY_URL || "http://localhost:8080"
).replace(/\/+$/, "");
const PROXY_KEY = process.env.FREELLMPOOL_PROXY_KEY || "";
const TOAST = !/^(0|false|off|no)$/i.test(process.env.FREELLMPOOL_TOAST || "");
const MODELS_DISCOVERY = /^(1|true|on|yes)$/i.test(process.env.FREELLMPOOL_MODELS_DISCOVERY || "");
const TIMEOUT_MS = 5000;

// Routing aliases exposed in the OpenCode model picker.
// models is a plain object keyed by model id (mirrors opencode.json provider format).
const PROVIDER_MODELS = {
  agent:   { name: "Agent — strongest healthy tier" },
  spread:  { name: "Spread — maximum pool breadth" },
  auto:    { name: "Auto — proxy default routing" },
  fast:    { name: "Fast — lowest latency" },
  quality: { name: "Quality — capability matched" },
  fair:    { name: "Fair — provider quota spread" },
};

function authHeaders() {
  return PROXY_KEY ? { Authorization: `Bearer ${PROXY_KEY}` } : {};
}

// GET <path> as JSON. Never throws — returns {ok, data?, error?} so a down proxy
// surfaces as a friendly message instead of crashing the tool/hook.
async function getJSON(path) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      headers: { Accept: "application/json", ...authHeaders() },
      signal: ac.signal,
    });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; }
    catch { return { ok: false, error: `non-JSON response (HTTP ${res.status})` }; }
    if (!res.ok) {
      const msg = data?.error?.message || data?.error || `HTTP ${res.status}`;
      return { ok: false, error: String(msg) };
    }
    return { ok: true, data };
  } catch (err) {
    const why = err?.name === "AbortError"
      ? `timed out after ${TIMEOUT_MS}ms`
      : err?.message || String(err);
    return { ok: false, error: `cannot reach freellmpool proxy at ${BASE_URL} (${why})` };
  } finally {
    clearTimeout(timer);
  }
}

// POST <path> with a JSON body. Like getJSON, never throws.
async function postJSON(path, body, timeoutMs = TIMEOUT_MS) {
  const ac = new AbortController();
  const timer = setTimeout(() => ac.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE_URL}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json", ...authHeaders() },
      body: JSON.stringify(body),
      signal: ac.signal,
    });
    const text = await res.text();
    let data;
    try { data = text ? JSON.parse(text) : {}; }
    catch { return { ok: false, error: `non-JSON response (HTTP ${res.status})` }; }
    if (!res.ok) {
      const msg = data?.error?.message || data?.error || `HTTP ${res.status}`;
      return { ok: false, error: String(msg) };
    }
    return { ok: true, data };
  } catch (err) {
    const why = err?.name === "AbortError"
      ? `timed out after ${timeoutMs}ms`
      : err?.message || String(err);
    return { ok: false, error: `cannot reach freellmpool proxy at ${BASE_URL} (${why})` };
  } finally {
    clearTimeout(timer);
  }
}

const num = (n) => (typeof n === "number" && isFinite(n) ? n : 0);
const pad = (s, w) => String(s).padEnd(w);

function fmtMoney(usd) {
  const v = num(usd);
  return v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`;
}

function renderStatus(s, { verbose }) {
  const lines = [];
  const pool = s.pool || {};
  lines.push(`freellmpool — routing: ${s.routing || "?"}`);
  lines.push(
    `usage: ${num(pool.requests)} requests · ` +
      `${num(pool.prompt_tokens).toLocaleString()} in / ${num(pool.completion_tokens).toLocaleString()} out tok · ` +
      `${num(pool.cache_hits)} cache hits · ~${fmtMoney(pool.usd_saved)} saved`,
  );

  const recent = Array.isArray(s.recent) ? s.recent : [];
  if (recent.length) {
    const r = recent[0];
    lines.push(
      `last served: ${r.provider}/${r.model}` +
        (num(r.attempts) > 1 ? ` (after ${r.attempts} attempts)` : ""),
    );
  }

  const providers = Array.isArray(s.providers) ? s.providers : [];
  const active = providers.filter((p) => p.configured);
  lines.push("");
  lines.push(`providers (${active.length}/${providers.length} configured):`);
  for (const p of providers) {
    if (!p.configured && !verbose) continue;
    const models = Array.isArray(p.models) ? p.models : [];
    let used = 0, cap = 0, capped = false, bestMs = null;
    for (const m of models) {
      used += num(m.used_today);
      if (typeof m.rpd === "number" && m.rpd > 0) { cap += m.rpd; capped = true; }
      if (typeof m.ewma_ms === "number" && m.ewma_ms > 0)
        bestMs = bestMs === null ? m.ewma_ms : Math.min(bestMs, m.ewma_ms);
    }
    const flag  = p.configured ? "✓" : "·";
    const quota = capped ? `${used}/${cap} req` : `${used} req (no daily cap)`;
    const cool  = num(p.cooldown_remaining_s) > 0 ? ` · cooldown ${Math.ceil(p.cooldown_remaining_s)}s` : "";
    const lat   = bestMs !== null ? ` · ${Math.round(bestMs)}ms` : "";
    lines.push(`  ${flag} ${pad(p.id, 12)} ${models.length} models · ${quota}${lat}${cool}`);
    if (verbose) {
      for (const m of models) {
        const remain = typeof m.remaining === "number" ? `${m.remaining} left` : "no cap";
        const sr  = typeof m.success_rate === "number" ? ` · ${Math.round(m.success_rate * 100)}% ok` : "";
        const ms  = typeof m.ewma_ms === "number" && m.ewma_ms > 0 ? ` · ${Math.round(m.ewma_ms)}ms` : "";
        const err = m.last_error ? ` · last_err: ${String(m.last_error).slice(0, 60)}` : "";
        lines.push(`      ${pad(m.name, 28)} ${num(m.used_today)} used · ${remain}${ms}${sr}${err}`);
      }
    }
  }
  if (!verbose) lines.push("(pass verbose:true for per-model quota/latency/errors)");
  return lines.join("\n");
}

export default Plugin.define({
  id: "freellmpool",

  async setup(ctx) {
    // ── Provider registration ──────────────────────────────────────────────────
    // Mirrors opencode-models-discovery (v2/catalog.ts): editor.add({ info, models })
    // with Provider.Info.empty(), an integrationID + activation, and Model.Info.default()
    // entries set via editor.models.set(). Same package string as models-discovery's README.
    const PROVIDER_ID = Provider.ID.make("freellmpool");
    const INTEGRATION_ID = "freellmpool";

    try {
      await ctx.integration.transform((draft) => {
        if (!draft.get(INTEGRATION_ID)) {
          draft.update(INTEGRATION_ID, (cur) => {
            cur.id = INTEGRATION_ID;
            cur.name = "freellmpool (free pool)";
          });
        }
        draft.method.update({
          integrationID: INTEGRATION_ID,
          method: { type: "key", label: "API key" },
        });
      });
    } catch (err) {
      console.error("[freellmpool] integration.transform failed:", err?.message || err);
    }

    await ctx.provider.transform((editor) => {
      const headers = PROXY_KEY ? { authorization: `Bearer ${PROXY_KEY}` } : undefined;
      const current = editor.get(PROVIDER_ID);

      if (!current) {
        editor.add({
          info: {
            ...Provider.Info.empty(PROVIDER_ID),
            name: "freellmpool (free pool)",
            package: "@opencode/ai/providers/openai-compatible",
            settings: { baseURL: `${BASE_URL}/v1`, apiKey: PROXY_KEY || "dummy-key", ...(MODELS_DISCOVERY && { modelsDiscovery: { enabled: true } }) },
            headers,
            integrationID: INTEGRATION_ID,
            activation: "enabled",
          },
          models: [],
        });
      } else if (!current.provider?.integrationID) {
        // Provider already defined in opencode.json: only attach the integration.
        editor.update(PROVIDER_ID, (draft) => { draft.integrationID = INTEGRATION_ID; });
      }

      const source = editor.get(PROVIDER_ID);
      const existing = source ? [...source.models.values()] : [];
      const have = new Set(existing.map((m) => String(m.id)));
      editor.models.set(PROVIDER_ID, [
        ...existing,
        ...Object.entries(PROVIDER_MODELS)
          .filter(([id]) => !have.has(id))
          .map(([id, def]) =>
            Object.assign(Model.Info.default(PROVIDER_ID, Model.ID.make(id)), { name: def.name }),
          ),
      ]);
    });

    // ── Tools ──────────────────────────────────────────────────────────────────
    await ctx.tool.transform((editor) => {
      editor.add({
        name: "freellmpool_status",
        description:
          "Show freellmpool proxy status: usage, estimated $ saved, per-provider " +
          "quota/rate-limits/cooldown/latency, current routing mode, and the most " +
          "recently served provider+model. Use when asked which model/provider is " +
          "serving, how much quota is left, or how much spend has been saved.",
        input: {
          type: "object",
          properties: {
            verbose: {
              type: "boolean",
              description: "Include per-model quota, latency, success rate, and last error.",
            },
          },
          additionalProperties: false,
        },
        execute: async (input) => {
          const { ok, data, error } = await getJSON("/status");
          if (!ok) return { content: `freellmpool status unavailable: ${error}` };
          return { content: renderStatus(data, { verbose: !!input.verbose }) };
        },
      });

      editor.add({
        name: "freellmpool_models",
        description:
          "List the model ids the freellmpool proxy currently exposes (including the " +
          "routing aliases agent/auto/spread/fast/quality/fair). Use to discover what to set as the model.",
        input: {
          type: "object",
          properties: {},
          additionalProperties: false,
        },
        execute: async () => {
          const { ok, data, error } = await getJSON("/v1/models");
          if (!ok) return { content: `freellmpool models unavailable: ${error}` };
          const ids = (data?.data || []).map((m) => m.id).filter(Boolean);
          if (!ids.length) return { content: "freellmpool: no models reported." };
          return {
            content:
              `freellmpool exposes ${ids.length} models:\n` +
              ids.map((id) => `  ${id}`).join("\n"),
          };
        },
      });

      editor.add({
        name: "freellmpool_tokenmax",
        description:
          "🌈 TOKENMAX: fan the SAME prompt out to automatically eligible ranked targets " +
          "across configured providers (hard cap 256; max/routing may narrow the swarm), " +
          "then YOU synthesize the single best answer from the returned responses. While it runs, " +
          "the freellmpool panel throbs a live rainbow TOKENMAXXING animation with N/total " +
          "progress. Tongue-in-cheek but genuinely useful for hard questions.",
        input: {
          type: "object",
          properties: {
            prompt: {
              type: "string",
              description: "The prompt to send to the selected eligible targets.",
            },
            max_models: {
              type: "number",
              description: "Narrow the eligible ranked target set (hard max 256).",
            },
          },
          required: ["prompt"],
          additionalProperties: false,
        },
        execute: async (input) => {
          if (!input?.prompt || !String(input.prompt).trim())
            return { content: "freellmpool_tokenmax: 'prompt' is required" };
          const body = { prompt: String(input.prompt) };
          if (typeof input.max_models === "number") body.max_models = input.max_models;
          const { ok, data, error } = await postJSON("/tokenmax", body, 180000);
          if (!ok) return { content: `freellmpool tokenmax unavailable: ${error}` };
          const answers = Array.isArray(data.answers) ? data.answers : [];
          const lines = [
            `🌈 TOKENMAX — ${answers.length}/${num(data.total) || answers.length} models answered ` +
              `across ${num(data.n_providers) || "?"} providers (rainbow throbbing live in the freellmpool panel).`,
            "Synthesize the single best, correct answer from all returned responses below " +
              "(weigh agreement, discard outliers):",
            "",
          ];
          for (const a of answers) lines.push(`### ${a.model}\n${a.text}\n`);
          const failed = Array.isArray(data.failed) ? data.failed : [];
          if (failed.length) {
            const shown = failed.slice(0, 30).join(", ") + (failed.length > 30 ? "…" : "");
            lines.push(`_${failed.length} unavailable: ${shown}_`);
          }
          return { content: lines.join("\n") };
        },
      });
    });

    // ── Event hook: served-model toast ─────────────────────────────────────────
    // ctx.event.subscribe() returns an async iterable (see models-discovery v2/index.ts),
    // NOT a callback API. Fully guarded: a failure here must never abort plugin setup.
    const abort = new AbortController();
    if (TOAST && ctx.event && typeof ctx.event.subscribe === "function") {
      void (async () => {
        let lastSeenMsg = null; // message.updated fires repeatedly per message
        try {
          for await (const event of ctx.event.subscribe({ signal: abort.signal })) {
            try {
              if (event?.type !== "message.updated") continue;
              const info = event.properties?.info ?? event.data?.info;
              if (!info || info.role !== "assistant" || !info.finish) continue;
              if (info.id === lastSeenMsg) continue;
              lastSeenMsg = info.id;

              const { ok, data } = await getJSON("/status");
              if (!ok) continue;
              const r = (data.recent || [])[0];
              if (!r) continue;
              const message =
                `served by ${r.provider}/${r.model}` +
                (num(r.attempts) > 1 ? ` (${r.attempts} attempts)` : "");
              try {
                if (!ctx.ui?.toast?.show) throw new Error("no toast api");
                await ctx.ui.toast.show({ title: "freellmpool", message, variant: "info", duration: 2500 });
              } catch {
                console.log(`[freellmpool] ${message}`);
              }
            } catch { /* ignore per-event errors */ }
          }
        } catch { /* stream closed / aborted */ }
      })();
    }

    return () => abort.abort();
  },
});
