// freellmpool — OpenCode plugin
//
// Surfaces the freellmpool proxy *inside* OpenCode:
//   • freellmpool_status  — usage, $ saved, per-provider quota / cooldown / latency,
//                           and the most-recently-served provider+model.
//   • freellmpool_models  — the model ids the proxy currently exposes.
//   • a best-effort toast/log of which provider+model actually served each reply
//     (OpenCode only knows the alias you asked for — e.g. "freellmpool/auto" — so
//     the real target is read back from the proxy's /status `recent` buffer).
//
// Quality/routing control is done through OpenCode's own model picker: switch the
// model to `freellmpool/agent`, `/fast`, `/quality`, or `/fair` (the proxy maps the
// name to a routing mode). `freellmpool/auto` uses the proxy's default routing.
//
// Zero build step, zero extra deps: drop this file in ~/.config/opencode/plugin/ or
// reference it from opencode.json `plugin`. Config:
//   FREELLMPOOL_PROXY_URL   base URL (default http://localhost:8080)
//   FREELLMPOOL_PROXY_KEY   sent as `Authorization: Bearer <key>` if the proxy needs one
//   FREELLMPOOL_TOAST       set to 0/false/off to silence the served-model toast

import { tool } from "@opencode-ai/plugin";

const BASE_URL = (
  process.env.FREELLMPOOL_PROXY_URL || "http://localhost:8080"
).replace(/\/+$/, "");
const PROXY_KEY = process.env.FREELLMPOOL_PROXY_KEY || "";
const TOAST = !/^(0|false|off|no)$/i.test(process.env.FREELLMPOOL_TOAST || "");
const TIMEOUT_MS = 5000;

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
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      return { ok: false, error: `non-JSON response (HTTP ${res.status})` };
    }
    if (!res.ok) {
      const msg = data?.error?.message || data?.error || `HTTP ${res.status}`;
      return { ok: false, error: String(msg) };
    }
    return { ok: true, data };
  } catch (err) {
    const why = err?.name === "AbortError" ? `timed out after ${TIMEOUT_MS}ms` : err?.message || String(err);
    return { ok: false, error: `cannot reach freellmpool proxy at ${BASE_URL} (${why})` };
  } finally {
    clearTimeout(timer);
  }
}

// POST <path> with a JSON body. Like getJSON, never throws. Takes its own timeout
// because tokenmax fans out to many models and can run far longer than a status poll.
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
    try {
      data = text ? JSON.parse(text) : {};
    } catch {
      return { ok: false, error: `non-JSON response (HTTP ${res.status})` };
    }
    if (!res.ok) {
      const msg = data?.error?.message || data?.error || `HTTP ${res.status}`;
      return { ok: false, error: String(msg) };
    }
    return { ok: true, data };
  } catch (err) {
    const why = err?.name === "AbortError" ? `timed out after ${timeoutMs}ms` : err?.message || String(err);
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

// Build the human-readable status report from a /status payload.
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
    lines.push(`last served: ${r.provider}/${r.model}${num(r.attempts) > 1 ? ` (after ${r.attempts} attempts)` : ""}`);
  }

  const providers = Array.isArray(s.providers) ? s.providers : [];
  // Configured providers first, each with a one-line health summary.
  const active = providers.filter((p) => p.configured);
  lines.push("");
  lines.push(`providers (${active.length}/${providers.length} configured):`);
  for (const p of providers) {
    if (!p.configured && !verbose) continue;
    const models = Array.isArray(p.models) ? p.models : [];
    // Aggregate quota across this provider's models (rpd-limited ones only).
    let used = 0;
    let cap = 0;
    let capped = false;
    let bestMs = null;
    for (const m of models) {
      used += num(m.used_today);
      if (typeof m.rpd === "number" && m.rpd > 0) {
        cap += m.rpd;
        capped = true;
      }
      if (typeof m.ewma_ms === "number" && m.ewma_ms > 0) {
        bestMs = bestMs === null ? m.ewma_ms : Math.min(bestMs, m.ewma_ms);
      }
    }
    const flag = p.configured ? "✓" : "·";
    const quota = capped ? `${used}/${cap} req` : `${used} req (no daily cap)`;
    const cool = num(p.cooldown_remaining_s) > 0 ? ` · cooldown ${Math.ceil(p.cooldown_remaining_s)}s` : "";
    const lat = bestMs !== null ? ` · ${Math.round(bestMs)}ms` : "";
    lines.push(`  ${flag} ${pad(p.id, 12)} ${models.length} models · ${quota}${lat}${cool}`);

    if (verbose) {
      for (const m of models) {
        const remain =
          typeof m.remaining === "number" ? `${m.remaining} left` : "no cap";
        const sr = typeof m.success_rate === "number" ? ` · ${Math.round(m.success_rate * 100)}% ok` : "";
        const ms = typeof m.ewma_ms === "number" && m.ewma_ms > 0 ? ` · ${Math.round(m.ewma_ms)}ms` : "";
        const err = m.last_error ? ` · last_err: ${String(m.last_error).slice(0, 60)}` : "";
        lines.push(`      ${pad(m.name, 28)} ${num(m.used_today)} used · ${remain}${ms}${sr}${err}`);
      }
    }
  }
  if (!verbose) lines.push("(pass verbose:true for per-model quota/latency/errors)");
  return lines.join("\n");
}

export const FreellmpoolPlugin = async ({ client }) => {
  let lastSeenMsg = null; // dedupe message.updated (it fires repeatedly per message)

  return {
    tool: {
      freellmpool_status: tool({
        description:
          "Show freellmpool proxy status: usage, estimated $ saved, per-provider " +
          "quota/rate-limits/cooldown/latency, current routing mode, and the most " +
          "recently served provider+model. Use when asked which model/provider is " +
          "serving, how much quota is left, or how much spend has been saved.",
        args: {
          verbose: tool.schema
            .boolean()
            .optional()
            .describe("Include per-model quota, latency, success rate, and last error."),
        },
        async execute(args) {
          const { ok, data, error } = await getJSON("/status");
          if (!ok) return `freellmpool status unavailable: ${error}`;
          return renderStatus(data, { verbose: !!args.verbose });
        },
      }),

      freellmpool_models: tool({
        description:
          "List the model ids the freellmpool proxy currently exposes (including the " +
          "routing aliases agent/auto/spread/fast/quality/fair). Use to discover what to set as the model.",
        args: {},
        async execute() {
          const { ok, data, error } = await getJSON("/v1/models");
          if (!ok) return `freellmpool models unavailable: ${error}`;
          const ids = (data?.data || []).map((m) => m.id).filter(Boolean);
          if (!ids.length) return "freellmpool: no models reported.";
          return `freellmpool exposes ${ids.length} models:\n` + ids.map((id) => `  ${id}`).join("\n");
        },
      }),

      freellmpool_tokenmax: tool({
        description:
          "🌈 TOKENMAX: blast the SAME prompt to EVERY free model the proxy can reach at " +
          "once, then YOU synthesize the single best answer from all of them. While it runs, " +
          "the freellmpool panel throbs a live rainbow TOKENMAXXING animation with N/total " +
          "progress. Tongue-in-cheek but genuinely useful for the hardest questions where you " +
          "want every model's take.",
        args: {
          prompt: tool.schema.string().describe("The prompt to blast to every model."),
          max_models: tool.schema
            .number()
            .optional()
            .describe("Cap how many models to hit (default: ALL of them)."),
        },
        async execute(args) {
          if (!args?.prompt || !String(args.prompt).trim()) {
            return "freellmpool_tokenmax: 'prompt' is required";
          }
          const body = { prompt: String(args.prompt) };
          if (typeof args.max_models === "number") body.max_models = args.max_models;
          // Long timeout: the swarm can take a couple of minutes to drain every provider.
          const { ok, data, error } = await postJSON("/tokenmax", body, 180000);
          if (!ok) return `freellmpool tokenmax unavailable: ${error}`;
          const answers = Array.isArray(data.answers) ? data.answers : [];
          const lines = [
            `🌈 TOKENMAX — ${answers.length}/${num(data.total) || answers.length} models answered ` +
              `across ${num(data.n_providers) || "?"} providers (rainbow throbbing live in the freellmpool panel).`,
            "Synthesize the single best, correct answer from every response below " +
              "(weigh agreement, discard outliers):",
            "",
          ];
          for (const a of answers) lines.push(`### ${a.model}\n${a.text}\n`);
          const failed = Array.isArray(data.failed) ? data.failed : [];
          if (failed.length) {
            const shown = failed.slice(0, 30).join(", ") + (failed.length > 30 ? "…" : "");
            lines.push(`_${failed.length} unavailable: ${shown}_`);
          }
          return lines.join("\n");
        },
      }),
    },

    // Best-effort: when an assistant reply completes, read which provider+model the
    // proxy actually used (OpenCode only knows the alias we asked for) and surface it.
    event: async ({ event }) => {
      if (!TOAST) return;
      if (event?.type !== "message.updated") return;
      const info = event.properties?.info;
      if (!info || info.role !== "assistant" || !info.finish) return;
      if (info.id === lastSeenMsg) return;
      lastSeenMsg = info.id;

      const { ok, data } = await getJSON("/status");
      if (!ok) return;
      const r = (data.recent || [])[0];
      if (!r) return;
      const served = `${r.provider}/${r.model}`;

      const message = `served by ${served}${num(r.attempts) > 1 ? ` (${r.attempts} attempts)` : ""}`;
      try {
        await client.tui.showToast({
          body: { title: "freellmpool", message, variant: "info", duration: 2500 },
        });
      } catch {
        // TUI not attached (e.g. `opencode run`) — fall back to the log.
        console.log(`[freellmpool] ${message}`);
      }
    },
  };
};

export default FreellmpoolPlugin;
