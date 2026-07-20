/** @jsxImportSource @opentui/solid */
//
// freellmpool — embedded live dashboard for OpenCode.
//
// A themed panel that renders *inside* opencode (sidebar + home screen), polling the
// freellmpool proxy's /status endpoint and showing, live: the routing mode, estimated
// $ saved, tokens served free, a provider "race", current throughput (tok/s), a
// latency sparkline, and the most-recently-served provider/model.
//
// Install:  opencode plugin -g file:/path/to/integrations/opencode-tui
// Config:   FREELLMPOOL_PROXY_URL (default http://localhost:8080)
//
import { createEffect, createSignal, onCleanup, For, Show } from "solid-js"

const PROXY = (process.env.FREELLMPOOL_PROXY_URL || "http://localhost:8080").replace(/\/+$/, "")
const PROXY_KEY = process.env.FREELLMPOOL_PROXY_KEY || ""
const AUTH = PROXY_KEY ? { Authorization: `Bearer ${PROXY_KEY}` } : {}
const POLL_MS = 1500
const POLL_MS_TOKENMAX = 300 // poll fast while a swarm is in flight, so the bar moves
const RAINBOW = ["#ff0040", "#ff8800", "#ffdd00", "#22cc44", "#00ccff", "#3366ff", "#cc44ff"]

// ── formatting helpers ───────────────────────────────────────────────────────
const BAR = "█"
const BAR_BG = "░"
const SPARK = ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█"]
const MEDALS = ["🥇", "🥈", "🥉"]

function bar(frac: number, width = 10): string {
  const f = Math.max(0, Math.min(1, frac || 0))
  const fill = Math.round(f * width)
  return BAR.repeat(fill) + BAR_BG.repeat(width - fill)
}

function sparkline(values: number[], width = 12): string {
  if (!values.length) return ""
  const recent = values.slice(-width)
  const max = Math.max(...recent, 1)
  const min = Math.min(...recent, 0)
  const span = max - min || 1
  return recent.map((v) => SPARK[Math.min(SPARK.length - 1, Math.floor(((v - min) / span) * (SPARK.length - 1)))]).join("")
}

function money(usd: number): string {
  const v = Number(usd) || 0
  return v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(4)}`
}

function compact(n: number): string {
  const v = Number(n) || 0
  if (v >= 1e9) return `${(v / 1e9).toFixed(1)}B`
  if (v >= 1e6) return `${(v / 1e6).toFixed(1)}M`
  if (v >= 1e3) return `${(v / 1e3).toFixed(1)}K`
  return `${v}`
}

function truncate(s: string, max = 28): string {
  return s.length > max ? s.slice(0, max - 1) + "…" : s
}

// ── plugin ───────────────────────────────────────────────────────────────────
const plugin = async (api) => {
  const [status, setStatus] = createSignal<any>(null)
  const [down, setDown] = createSignal(false)
  const [tps, setTps] = createSignal(0)
  const [latHist, setLatHist] = createSignal<number[]>([])

  let prev: { tokens: number; t: number } | null = null

  const poll = async () => {
    try {
      const res = await fetch(`${PROXY}/status`, { headers: AUTH })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const s = await res.json()
      setStatus(s)
      setDown(false)

      // throughput: completion-token delta / elapsed since last sample. Use the LIFETIME
      // counter (persisted, shared across the proxy + CLI + MCP) so tok/s reflects ALL
      // freellmpool activity, not just this proxy session — and so it's non-zero for
      // streaming clients now that the stream path records tokens.
      const tokens = Number(s?.lifetime?.completion_tokens) || 0
      const now = Date.now()
      if (prev && now > prev.t) {
        const dt = (now - prev.t) / 1000
        const dTok = tokens - prev.tokens
        setTps(dTok > 0 ? Math.round(dTok / dt) : 0)
      }
      prev = { tokens, t: now }

      // latency history = best per-provider ewma each tick (for the sparkline)
      const mss: number[] = []
      for (const p of s?.providers || []) {
        for (const m of p?.models || []) {
          if (typeof m.ewma_ms === "number" && m.ewma_ms > 0) mss.push(m.ewma_ms)
        }
      }
      if (mss.length) {
        const best = Math.min(...mss)
        setLatHist((h) => [...h.slice(-23), best])
      }
    } catch {
      setDown(true)
    }
  }
  // Self-scheduling poll: tick fast while a tokenmax swarm is active so the rainbow
  // progress bar moves smoothly, otherwise fall back to the calm cadence. The `disposed`
  // flag is essential: onCleanup can fire mid-`await poll()`, after which we must NOT
  // reschedule (the timeout cleanup already ran) — otherwise the loop becomes a zombie
  // poller that keeps hitting the proxy and updating disposed signals forever.
  let pollTimer: ReturnType<typeof setTimeout>
  let disposed = false
  const loop = async () => {
    await poll()
    if (disposed) return
    const delay = status()?.tokenmax?.active ? POLL_MS_TOKENMAX : POLL_MS
    pollTimer = setTimeout(loop, delay)
  }
  loop()
  onCleanup(() => {
    disposed = true
    clearTimeout(pollTimer)
  })

  // Rainbow throb: a frame counter the TOKENMAXXING banner reads to cycle colors. The
  // banner is only mounted while a swarm is active, so this drives no renders when idle.
  const [frame, setFrame] = createSignal(0)
  const throb = setInterval(() => setFrame((f) => (f + 1) % 1000), 90)
  onCleanup(() => clearInterval(throb))
  const rainbow = () => RAINBOW[frame() % RAINBOW.length]

  // Brief "✅ TOKENMAXXED" flash when a swarm finishes (active true → false).
  const [flash, setFlash] = createSignal<string | null>(null)
  let wasActive = false
  let flashTimer: ReturnType<typeof setTimeout> | undefined
  createEffect(() => {
    const tm = status()?.tokenmax
    const active = !!tm?.active
    if (wasActive && !active) {
      const n = tm?.answered ?? tm?.total ?? 0
      setFlash(`✅ TOKENMAXXED — ${n} models answered`)
      clearTimeout(flashTimer)
      flashTimer = setTimeout(() => setFlash(null), 4000)
    }
    wasActive = active
  })
  onCleanup(() => clearTimeout(flashTimer))

  // top providers by requests-used-today (the "race")
  const topProviders = () => {
    const s = status()
    if (!s?.providers) return [] as any[]
    return s.providers
      .filter((p: any) => p.configured)
      .map((p: any) => {
        const models = p.models || []
        const used = models.reduce((a: number, m: any) => a + (Number(m.used_today) || 0), 0)
        const cap = models.reduce((a: number, m: any) => a + (m.rpd > 0 ? m.rpd : 0), 0)
        return { id: p.id, used, cap, cooldown: Number(p.cooldown_remaining_s) || 0 }
      })
      .sort((a: any, b: any) => b.used - a.used)
      .slice(0, 5)
  }
  const maxUsed = () => Math.max(1, ...topProviders().map((p: any) => p.used))

  // ── 🌈 the live TOKENMAXXING banner (color-cycles while a swarm runs) ────────
  const TokenmaxBanner = () => {
    const theme = api.theme.current
    const tm = () => status()?.tokenmax || {}
    const done = () => Number(tm().done) || 0
    const total = () => Number(tm().total) || 0
    return (
      <Show when={tm().active || flash()}>
        <Show
          when={tm().active}
          fallback={
            <box border borderColor={theme.success} paddingLeft={1} paddingRight={1}>
              <text fg={theme.success}>{flash()}</text>
            </box>
          }
        >
          <box border borderColor={rainbow()} paddingLeft={1} paddingRight={1} flexDirection="column">
            <text fg={rainbow()}>🌈 T O K E N M A X X I N G 🌈</text>
            <box flexDirection="row" gap={1}>
              <text fg={rainbow()}>{bar(total() ? done() / total() : 0, 16)}</text>
              <text fg={theme.textMuted}>
                {done()}/{total()} models · {Number(tm().n_providers) || 0} providers
              </text>
            </box>
          </box>
        </Show>
      </Show>
    )
  }

  // ── the panel component (used by both the sidebar and the home screen) ──────
  const Panel = () => {
    const theme = api.theme.current
    return (
      <Show
        when={!down()}
        fallback={
          <box border borderColor={theme.error} paddingLeft={1} paddingRight={1} flexDirection="column">
            <text fg={theme.error}>● freellmpool</text>
            <text fg={theme.textMuted}>proxy offline — start `freellmpool proxy`</text>
          </box>
        }
      >
        <box border borderColor={theme.success} title=" freellmpool " paddingLeft={1} paddingRight={1} flexDirection="column">
          <TokenmaxBanner />
          <box flexDirection="row" gap={1}>
            <text fg={theme.textMuted}>routing</text>
            <text fg={theme.accent}>{status()?.routing ?? "?"}</text>
          </box>

          <box flexDirection="row" gap={1}>
            <text fg={theme.success}>💸 {money(status()?.lifetime?.usd_saved ?? 0)}</text>
            <text fg={theme.textMuted}>saved</text>
            <text fg={theme.info}>⚡ {tps()} tok/s</text>
          </box>

          <text fg={theme.textMuted}>
            {compact(
              (status()?.lifetime?.prompt_tokens ?? 0) + (status()?.lifetime?.completion_tokens ?? 0),
            )}{" "}
            tokens served free · {status()?.lifetime?.requests ?? 0} req
          </text>

          <text fg={theme.textMuted}>── provider race ──</text>
          <For each={topProviders()}>
            {(p: any, i) => (
              <box flexDirection="row" gap={1}>
                <text fg={theme.text}>
                  {MEDALS[i()] ?? "  "} {p.id.padEnd(10)}
                </text>
                <text fg={p.cooldown > 0 ? theme.warning : theme.success}>{bar(p.used / maxUsed())}</text>
                <text fg={theme.textMuted}>
                  {p.used}
                  {p.cap > 0 ? `/${p.cap}` : ""}
                  {p.cooldown > 0 ? ` ⏳${Math.ceil(p.cooldown)}s` : ""}
                </text>
              </box>
            )}
          </For>

          <Show when={latHist().length > 1}>
            <box flexDirection="row" gap={1}>
              <text fg={theme.textMuted}>latency</text>
              <text fg={theme.info}>{sparkline(latHist())}</text>
              <text fg={theme.textMuted}>{Math.round(latHist()[latHist().length - 1] || 0)}ms</text>
            </box>
          </Show>

          <Show when={status()?.recent?.[0]}>
            <text fg={theme.textMuted}>last: {truncate(`${status().recent[0].provider}/${status().recent[0].model}`)}</text>
          </Show>
        </box>
      </Show>
    )
  }

  // sidebar panel (live, while you code) + a compact home-screen teaser
  api.slots.register({
    order: 50,
    slots: {
      sidebar_content() {
        return <Panel />
      },
      home_bottom() {
        const theme = api.theme.current
        return (
          <Show when={!down()} fallback={<text fg={theme.error}>● freellmpool proxy offline</text>}>
            <box marginTop={1} flexDirection="column">
              <TokenmaxBanner />
              <box border borderColor={theme.success} paddingLeft={1} paddingRight={1} flexDirection="row" gap={1}>
                <text fg={theme.success}>● freellmpool</text>
                <text fg={theme.text}>
                  {money(status()?.lifetime?.usd_saved ?? 0)} saved · {compact((status()?.lifetime?.prompt_tokens ?? 0) + (status()?.lifetime?.completion_tokens ?? 0))} free tokens · {tps()} tok/s · {status()?.routing ?? "?"}
                </text>
              </box>
            </box>
          </Show>
        )
      },
    },
  })

  // a command to surface a one-shot summary toast
  api.command.register(() => [
    {
      title: "freellmpool: status",
      value: "freellmpool.status",
      category: "freellmpool",
      onSelect() {
        const s = status()
        const msg = down() || !s ? "proxy offline" : `${money(s.pool?.usd_saved ?? 0)} saved · ${s.pool?.requests ?? 0} req · routing ${s.routing}`
        api.ui.toast({ title: "freellmpool", message: msg, variant: down() ? "warning" : "success" })
      },
    },
  ])
}

export default { id: "freellmpool-tui", tui: plugin }
