# Sandboxed OpenCode on freellmpool (bubblewrap jail)

`opencode-freellmpool-jailed.sh` runs [OpenCode](https://opencode.ai) on the **freellmpool**
free-model pool inside a [bubblewrap](https://github.com/containers/bubblewrap) sandbox, so you
can let a model do **long agentic coding hands-off** without it touching anything outside a
throwaway project — while it *believes* it's on a normal full Linux box.

```sh
freellmpool proxy                         # starts the proxy on http://127.0.0.1:8080
integrations/opencode-jail/opencode-freellmpool-jailed.sh            # interactive TUI in ~/project
integrations/opencode-jail/opencode-freellmpool-jailed.sh --run "…"  # headless single turn
integrations/opencode-jail/opencode-freellmpool-jailed.sh --serve     # opencode serve (basic-auth)
integrations/opencode-jail/opencode-freellmpool-jailed.sh --selftest  # prove the containment, exit
```

## The illusion (what the model sees)
A complete, normal Linux box it owns: real read-only `/usr /lib /bin /etc /proc /dev`, a home
it can write anywhere in, a working `/tmp`, and a real git project at `~/project`. OpenCode
permissions are **allow-all** (no approval prompts), and pip/npm installs succeed (into writable
tmpfs prefixes). Nothing is labelled "sandbox"/"jail" — no obvious tell.

## The reality (what's actually true)
- **Only the project persists.** The project, OpenCode's state, and its config all live inside a
  single fixed-size **ext4 loopback image** (default **12 GB**); a runaway agent fills at most that
  image, never the host disk.
- **All host secrets are gone.** `$HOME` is a fresh tmpfs, so `~/.ssh`, API keys under `~/.config`,
  cloud creds, the real OpenCode auth, and the freellmpool source tree are all `ENOENT`.
  `--clearenv` keeps the host environment (and any exported keys) out.
- **Resources are capped** via a cgroup-v2 scope: **1 CPU core**, **6 GB RAM** (hard cap, no swap),
  **12 GB disk**. Tunable via `OPENCODE_FP_CPUS` / `OPENCODE_FP_MEM` / `OPENCODE_FP_DISK`.
- `--unshare-pid/uts/ipc`, fail-closed storage, an exclusive flock against concurrent jails, and
  symlink/TOCTOU-hardened host-side setup (reviewed across 10 adversarial Codex passes).

## Routing
Defaults to `freellmpool/agent`: the strongest healthy benchmark-capability tier, with
quota spreading and latency preference inside that tier. This keeps long tool loops away
from weak models without exhausting one provider. Override with
`OPENCODE_FP_MODEL=freellmpool/spread` (whole-pool breadth), `freellmpool/quality`
(prompt-matched capability), or another routing alias.

The generated OpenCode provider also raises header, total-request, and stream-chunk
timeouts to 10 minutes, 10 minutes, and 2 minutes respectively so a slow free-model
stream is not mistaken for a dead session.

## Known residuals (by design)
- **Network egress is open.** Free models + webfetch need the internet, and `--unshare-net` would
  also cut off the host proxy on `127.0.0.1`. Containment here is filesystem + secret-hiding, not
  network (there are no on-disk secrets to exfiltrate).
- **`/etc` is bound read-only** for a convincing, functional box.
- **No writable `/usr`** (this bubblewrap build lacks overlay), so a *system* `apt install` fails;
  project-local pip/npm works.

## Requirements
bubblewrap, OpenCode, a running freellmpool proxy, cgroup-v2 with a systemd `--user` session
(falls back to `taskset` CPU-pinning otherwise), `sudo` for the one-time loopback mount, and
`dev.tty.legacy_tiocsti=0` (the Linux ≥6.2 default) for the interactive TUI.

## Env knobs
`OPENCODE_FP_MODEL`, `OPENCODE_FP_PROJECT` (in-jail project basename), `OPENCODE_FP_PROXY_URL`,
`OPENCODE_FP_CPUS` / `OPENCODE_FP_MEM` / `OPENCODE_FP_DISK`, `OPENCODE_FP_PORT` (`--serve`),
`OPENCODE_FP_INTEG` (plugin source), `OPENCODE_FP_ALLOW_UNCAPPED=1` (run without the disk image —
discouraged).
