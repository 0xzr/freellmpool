/** @jsxImportSource @opentui/solid */
//
// freellmpool — embedded live dashboard for OpenCode.
//
// A themed panel that renders *inside* opencode (sidebar + home screen), polling the
// freellmpool proxy's /status endpoint and showing, live: the routing mode, estimated
// $ saved, tokens served free, a provider "race", current throughput (tok/s), a
// latency sparkline, and the most-recently-served provider/model.
//
// Install:  add the package/dir to the "plugins" array of cli.json (OpenCode v2)
// Config:   FREELLMPOOL_PROXY_URL (default http://localhost:8080)
//
import { Plugin } from "@opencode/plugin/tui"
import { createEffect, createRoot, createSignal, onCleanup, For, Show } from "solid-js"

const PROXY = (process.env.FREELLMPOOL_PROXY_URL || "http://localhost:8080").replace(/\/+$/, "")
const PROXY_KEY = process.env.FREELLMPOOL_PROXY_KEY || ""
const AUTH = PROXY_KEY ? { Authorization: `Bearer ${PROXY_KEY}` } : {}
const AUTH_GUIDANCE = `${PROXY_KEY ? "check" : "set"} FREELLMPOOL_PROXY_KEY`
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
  return recent
    .map((v) => SPARK[Math.min(SPARK.length - 1, Math.floor(((v - min) / span) * (SPARK.length - 1)))])
    .join("")
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

// ── proxy connectivity states ────────────────────────────────────────────────
const STATE = {
  ONLINE: "online",
  OFFLINE: "offline", // connection refused / network error
  AUTH_REQUIRED: "auth_required", // 401/403 – proxy is running but auth failed
  ERROR: "error", // other HTTP error from the proxy itself
} as const
type ProxyState = (typeof STATE)[keyof typeof STATE]

// ── theme ────────────────────────────────────────────────────────────────────
// Docs only guarantee `context.theme.text.base`; other semantic tokens are read defensively
// and fall back to fixed colors so a missing token never breaks rendering.
function makeTheme(context: any) {
  const t = () => context.theme ?? {}
  const pick = (get: () => unknown, fallback: string): string => {
    try {
      const v = get()
      return typeof v === "string" && v ? v : fallback
    } catch {
      return fallback
    }
  }
  return {
    get text() { return pick(() => t().text?.base, "#e0e0e0") },
    get textMuted() { return pick(() => t().text?.muted ?? t().text?.subtle, "#8a8a8a") },
    get info() { return pick(() => t().status?.info ?? t().info, "#00ccff") },
    get success() { return pick(() => t().status?.success ?? t().success, "#22cc44") },
    get warning() { return pick(() => t().status?.warning ?? t().warning, "#ffdd00") },
    get error() { return pick(() => t().status?.error ?? t().error, "#ff4444") },
    get accent() { return pick(() => t().accent ?? t().text?.accent ?? t().status?.info, "#00ccff") },
  }
}

// ── selected routing alias ───────────────────────────────────────────────────
// The proxy's /status `routing` is its *default* mode (FREELLMPOOL_ROUTING) — it does not
// change when you pick `freellmpool/agent` etc. in OpenCode; the alias is just the model name
// of each request. So read the active model from the host (`context.ui.model.current()`,
// reactive in Solid computations per the v2 docs) and only fall back to the proxy default.
function selectedAlias(context: any): string | undefined {
  try {
    const c = context?.ui?.model?.current?.()
    if (!c) return undefined
    const provider = c.providerID ?? c.provider?.id ?? c.provider
    const model = c.modelID ?? c.model?.id ?? c.id
    if (String(provider) !== "freellmpool" || !model) return undefined
    return String(model).replace(/^freellmpool\//, "")
  } catch {
    return undefined
  }
}

// ── plugin ───────────────────────────────────────────────────────────────────
function build(context: any) {
  const theme = makeTheme(context)
  const routingLabel = () => selectedAlias(context) ?? status()?.routing ?? "?"
  const [status, setStatus] = createSignal<any>(null)
  const [proxyState, setProxyState] = createSignal<ProxyState>(STATE.OFFLINE)
  const [proxyError, setProxyError] = createSignal<string>("")
  const [tps, setTps] = createSignal(0)
  const [latHist, setLatHist] = createSignal<number[]>([])

  let prev: { tokens: number; t: number } | null = null

  // Lightweight health check to confirm the proxy is reachable (even if /status
  // returns 401). Returns true when the proxy responds, false on network error.
  async function checkReachable(): Promise<boolean> {
    try {
      const res = await fetch(`${PROXY}/healthz`, {
        signal: AbortSignal.timeout(3000),
      })
      return res.ok
    } catch {
      return false
    }
  }

  const poll = async () => {
    try {
      const res = await fetch(`${PROXY}/status`, {
        headers: AUTH,
        signal: AbortSignal.timeout(5000),
      })
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) {
          // Proxy is running but auth failed — quick health check to confirm
          const reachable = await checkReachable()
          if (reachable) {
            setProxyState(STATE.AUTH_REQUIRED)
            setProxyError(`auth failed (HTTP ${res.status}) — ${AUTH_GUIDANCE}`)
          } else {
            setProxyState(STATE.OFFLINE)
            setProxyError("")
          }
        } else {
          // Proxy returned an HTTP error but is reachable
          setProxyState(STATE.ERROR)
          setProxyError(`HTTP ${res.status}`)
        }
        return
      }
      const s = await res.json()
      setStatus(s)
      setProxyState(STATE.ONLINE)
      setProxyError("")

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
    } catch (err: unknown) {
      // Network-level failure: unreachable, timeout, DNS, etc.
      const isTimeout = err instanceof Error && (err.name === "TimeoutError" || err.message.includes("timed out"))
      if (isTimeout) {
        setProxyState(STATE.OFFLINE)
        setProxyError("timed out")
      } else {
        // Try a quick fallback health check to rule out transient blips
        const reachable = await checkReachable()
        setProxyState(reachable ? STATE.ERROR : STATE.OFFLINE)
        setProxyError(reachable ? "status endpoint failed" : "")
      }
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
    const delay = status()?.tokenmax?.active && proxyState() === STATE.ONLINE ? POLL_MS_TOKENMAX : POLL_MS
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
        return {
          id: p.id,
          used,
          cap,
          cooldown: Number(p.cooldown_remaining_s) || 0,
        }
      })
      .sort((a: any, b: any) => b.used - a.used)
      .slice(0, 5)
  }
  const maxUsed = () => Math.max(1, ...topProviders().map((p: any) => p.used))

  // ── 🌈 the live TOKENMAXXING banner (color-cycles while a swarm runs) ────────
  const TokenmaxBanner = () => {
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
    const st = () => proxyState()
    const isOnline = () => st() === STATE.ONLINE
    return (
      <Show
        when={isOnline() || st() === STATE.ERROR}
        fallback={
          <Show
            when={st() === STATE.AUTH_REQUIRED}
            fallback={
              <box border borderColor={theme.error} paddingLeft={1} paddingRight={1} flexDirection="column">
                <text fg={theme.error}>● freellmpool</text>
                <text fg={theme.textMuted}>proxy offline — start `freellmpool proxy`</text>
                <Show when={proxyError()}>
                  <text fg={theme.textMuted}>({proxyError()})</text>
                </Show>
              </box>
            }
          >
            <box border borderColor={theme.warning} paddingLeft={1} paddingRight={1} flexDirection="column">
              <text fg={theme.warning}>● freellmpool</text>
              <text fg={theme.textMuted}>proxy needs auth — {AUTH_GUIDANCE}</text>
              <text fg={theme.textMuted}>({proxyError()})</text>
            </box>
          </Show>
        }
      >
        <Show
          when={st() === STATE.ERROR}
          fallback={
            <box
              border
              borderColor={theme.success}
              title=" freellmpool "
              paddingLeft={1}
              paddingRight={1}
              flexDirection="column"
            >
              <TokenmaxBanner />
              <box flexDirection="row" gap={1}>
                <text fg={theme.textMuted}>routing</text>
                <text fg={theme.accent}>{routingLabel()}</text>
              </box>

              <box flexDirection="row" gap={1}>
                <text fg={theme.success}>💸 {money(status()?.lifetime?.usd_saved ?? 0)}</text>
                <text fg={theme.textMuted}>saved</text>
                <text fg={theme.info}>⚡ {tps()} tok/s</text>
              </box>

              <text fg={theme.textMuted}>
                {compact((status()?.lifetime?.prompt_tokens ?? 0) + (status()?.lifetime?.completion_tokens ?? 0))}{" "}
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
                <text fg={theme.textMuted}>
                  last: {truncate(`${status().recent[0].provider}/${status().recent[0].model}`)}
                </text>
              </Show>
            </box>
          }
        >
          <box border borderColor={theme.warning} paddingLeft={1} paddingRight={1} flexDirection="column">
            <text fg={theme.warning}>● freellmpool</text>
            <text fg={theme.textMuted}>proxy error ({proxyError()})</text>
          </box>
        </Show>
      </Show>
    )
  }

  // ── slots (documented v2 placements) ────────────────────────────────────────
  const home = () => {
    const st = proxyState()
    if (st === STATE.ONLINE) {
      const life = status()?.lifetime ?? {}
      return (
        <text fg={theme.success}>
          ● freellmpool {money(life.usd_saved ?? 0)} saved · {tps()} tok/s · {routingLabel()}
        </text>
      )
    }
    if (st === STATE.AUTH_REQUIRED) return <text fg={theme.warning}>● freellmpool needs auth — {AUTH_GUIDANCE}</text>
    if (st === STATE.ERROR) return <text fg={theme.warning}>● freellmpool proxy error ({proxyError()})</text>
    return <text fg={theme.error}>● freellmpool offline</text>
  }

  const unsubs: Array<() => void> = []
  const unregisterAll = () => {
    for (const u of unsubs.splice(0)) { try { u() } catch {} }
  }
  const reg = (r: unknown) => { if (typeof r === "function") unsubs.push(r as () => void) }
  try {
    reg(context.ui.slot({ append: "sidebar.content", render: () => <Panel /> }))
    reg(context.ui.slot({ append: "home.footer.status", render: () => <>{home()}</> }))
  } catch (err) {
    // A later registration failed: don't leave earlier slots bound to signals that are
    // about to be disposed (and would be duplicated on retry).
    unregisterAll()
    throw err
  }
  return unregisterAll
}

// ── /freellmpool: dialog-based status (needs no slot API) ───────────────────
// Slash command + dialog (pattern also used by opencode-antigravity-auth), independent of slots.
async function statusReport(alias?: string): Promise<string> {
  try {
    const res = await fetch(`${PROXY}/status`, { headers: AUTH, signal: AbortSignal.timeout(5000) })
    if (res.status === 401 || res.status === 403) return `proxy needs auth (HTTP ${res.status}) — ${AUTH_GUIDANCE}`
    if (!res.ok) return `proxy error (HTTP ${res.status})`
    const s: any = await res.json()
    const pool = s.pool ?? {}
    const life = s.lifetime ?? {}
    const lines = [
      `routing: ${alias ?? s.routing ?? "?"}${alias && s.routing && alias !== s.routing ? ` (proxy default: ${s.routing})` : ""}`,
      `${money(life.usd_saved ?? pool.usd_saved ?? 0)} saved · ${compact((life.prompt_tokens ?? 0) + (life.completion_tokens ?? 0))} free tokens · ${pool.requests ?? 0} req this session`,
    ]
    const r = s.recent?.[0]
    if (r) lines.push(`last served: ${r.provider}/${r.model}`)
    lines.push("", "providers:")
    for (const p of (s.providers ?? []).filter((x: any) => x.configured)) {
      const models = p.models ?? []
      const used = models.reduce((a: number, m: any) => a + (Number(m.used_today) || 0), 0)
      const cap = models.reduce((a: number, m: any) => a + (m.rpd > 0 ? m.rpd : 0), 0)
      const cool = Number(p.cooldown_remaining_s) > 0 ? ` · cooldown ${Math.ceil(p.cooldown_remaining_s)}s` : ""
      lines.push(`  ${p.id}: ${used}${cap ? `/${cap}` : ""} req${cool}`)
    }
    return lines.join("\n")
  } catch {
    return `proxy offline at ${PROXY} — start \`freellmpool proxy\``
  }
}

export default Plugin.define({
  id: "freellmpool-tui",
  setup(context: any) {
    context.keymap.layer(() => ({
      mode: "global",
      commands: [
        {
          id: "freellmpool.dashboard",
          title: "freellmpool status",
          group: "freellmpool",
          palette: true,
          slash: { name: "freellmpool", aliases: ["flp"] },
          run: async () => {
            await context.ui.dialog.alert({ title: "freellmpool", message: await statusReport(selectedAlias(context)) })
          },
        },
      ],
    }))

    let dispose: (() => void) | undefined
    let cleanupSlots: (() => void) | undefined
    try {
      // createRoot owns the polling timers / effects (onCleanup) created in build().
      createRoot((d) => {
        dispose = d
        cleanupSlots = build(context)
      })
    } catch (err) {
      dispose?.() // stop the polling timers/effects started before build() failed
      dispose = undefined
      // Never let the live panel break plugin loading; the slash command above still works.
      console.warn("[freellmpool-tui] live panel unavailable:", err instanceof Error ? err.message : err)
    }
    return () => {
      cleanupSlots?.()
      dispose?.()
    }
  },
})
