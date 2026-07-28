#!/usr/bin/env bash
# opencode-freellmpool-jailed.sh — sandboxed OpenCode on the freellmpool free pool,
# for long agentic coding you can let run hands-off.
#
# THE ILLUSION (what the model believes): a normal, complete Linux box it owns — real
# read-only /usr /lib /bin /etc /proc /dev, a home it can write anywhere in, a working
# /tmp, and a real git project at ~/project. OpenCode permissions are allow-all (no
# approval prompts), pip/npm installs succeed (into writable tmpfs prefixes). Nothing is
# named "sandbox"/"jail" inside; the agent has no obvious tell.
#
# THE REALITY: the ONLY persistent, writable storage is a single fixed-size ext4 image
# (default 12G). The project, OpenCode's state, and its config all live INSIDE that image,
# so a runaway agent can fill at most that image — never the host disk. $HOME is a fresh
# tmpfs, so every host secret is gone (ENOENT): ~/.ssh, ~/.polybot (POLY_PRIVATE_KEY),
# ~/.config/* keys, ~/.codex, ~/.aws, the real opencode auth, and the llmbuffet source.
# --clearenv keeps the host environment (and any exported keys) out. CPU/RAM are capped
# via a cgroup-v2 scope. PID/UTS/IPC namespaces are unshared.
#
# NETWORK (known residual): egress is OPEN — free models + webfetch need the internet, and
# --unshare-net would also cut off the host proxy on 127.0.0.1. So the jail CAN reach other
# host localhost services and the internet. Containment here is filesystem + secret-hiding,
# NOT network. (A net-isolated variant with a proxy-only forwarder is a future option.)
#
# USAGE:  opencode-freellmpool-jailed.sh            # interactive TUI in ~/project
#         opencode-freellmpool-jailed.sh --run "…"  # headless single turn (cron-friendly)
#         opencode-freellmpool-jailed.sh --serve     # opencode serve (basic-auth)
#         opencode-freellmpool-jailed.sh --selftest  # prove containment + stealth, exit
#
# ENV KNOBS: OPENCODE_FP_MODEL (default freellmpool/agent — strongest healthy capability tier
#   with quota spreading for long tool loops;
#   models), OPENCODE_FP_PROJECT (in-jail project basename, default "project"),
#   OPENCODE_FP_PROXY_URL, OPENCODE_FP_CPUS/MEM/DISK (default 1 / 6G / 12G),
#   OPENCODE_FP_PORT (--serve), OPENCODE_FP_ALLOW_UNCAPPED=1 (run without the disk image —
#   discouraged), OPENCODE_FP_INTEG (source of the freellmpool plugins on the host).

set -euo pipefail

MODE="tui"
RUN_ARGS=()
case "${1:-}" in
  --serve) MODE="serve" ;;
  --selftest) MODE="selftest" ;;
  --run) MODE="run"; shift; RUN_ARGS=("$@") ;;
  "" ) ;;
  *) echo "usage: $(basename "$0") [--serve|--selftest|--run <prompt...>]" >&2; exit 2 ;;
esac

# ── tunables ────────────────────────────────────────────────────────────────────────
PROXY_URL="${OPENCODE_FP_PROXY_URL:-http://127.0.0.1:8080}"
MODEL="${OPENCODE_FP_MODEL:-freellmpool/agent}"  # agent = strongest healthy capability tier,
                                                 # spreading quota inside the tier to avoid stalls
PROJECT="${OPENCODE_FP_PROJECT:-project}"         # in-jail cwd basename — NOT "sandbox"
PORT="${OPENCODE_FP_PORT:-4099}"
HOSTBIND="127.0.0.1"
CPUS="${OPENCODE_FP_CPUS:-1}"
MEM="${OPENCODE_FP_MEM:-6G}"
DISK="${OPENCODE_FP_DISK:-12G}"
# Source of the freellmpool plugins (host, read-only). If this script lives in the repo's
# integrations/ tree (…/integrations/opencode-jail/), derive it from the script location;
# otherwise fall back. OPENCODE_FP_INTEG always overrides.
_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [ -n "${_self_dir:-}" ] && [ -d "$_self_dir/../opencode" ] && [ -d "$_self_dir/../opencode-tui" ]; then
  _integ_default="$(cd "$_self_dir/.." && pwd)"
else
  _integ_default="/home/ubuntu/llmbuffet/integrations"
fi
INTEG="${OPENCODE_FP_INTEG:-$_integ_default}"  # plugin source (host, read-only)

# PROJECT must be a bare directory name (no traversal / absolute path).
case "$PROJECT" in */*|..|.|"") echo "ERROR: OPENCODE_FP_PROJECT must be a bare name" >&2; exit 2 ;; esac
# MODEL and PROXY_URL get written verbatim into the JSON config — reject anything that could
# break/inject JSON (quotes, newlines, etc.). These are operator-set, so fail rather than escape.
case "$MODEL" in ""|*[!A-Za-z0-9._/-]*) echo "ERROR: OPENCODE_FP_MODEL: only [A-Za-z0-9._/-] allowed" >&2; exit 2 ;; esac
case "$PROXY_URL" in http://*|https://*) ;; *) echo "ERROR: OPENCODE_FP_PROXY_URL must start with http:// or https://" >&2; exit 2 ;; esac
case "$PROXY_URL" in *[!A-Za-z0-9._:/-]*) echo "ERROR: OPENCODE_FP_PROXY_URL has invalid characters" >&2; exit 2 ;; esac

# ── host-side state (hidden from the jail; everything persistent lives in ONE image) ──
HOSTBASE="$HOME/.local/state/oc-fp-jail"
DISK_IMG="$HOSTBASE/disk.img"
MNT="$HOSTBASE/mnt"                 # the 12G image is mounted here on the host
IMG_PROJECT="$MNT/project"          # → ~/$PROJECT  (the project; cwd)
IMG_STATE="$MNT/state"             # → ~/.local/share/opencode  (opencode DB)
IMG_CFG="$MNT/config"             # → ~/.config/opencode       (config + plugins)

# in-jail paths the model sees:
J_PROJECT="$HOME/$PROJECT"
J_CFG="$HOME/.config/opencode"
J_STATE="$HOME/.local/share/opencode"
NODE_BIN="$(dirname "$(command -v node 2>/dev/null || echo /usr/bin/node)")"

command -v bwrap >/dev/null 2>&1 || { echo "ERROR: bwrap not found (sudo apt-get install -y bubblewrap)" >&2; exit 1; }
command -v opencode >/dev/null 2>&1 || { echo "ERROR: opencode not found on PATH" >&2; exit 1; }

# ── capped storage: one fixed-size ext4 image holds project + state + config ──────────
# Fail CLOSED: if we can't provide the cap, refuse — unless explicitly overridden.
ensure_storage() {
  mkdir -p "$HOSTBASE" "$MNT"
  if ! mountpoint -q "$MNT"; then
    if ! command -v mkfs.ext4 >/dev/null 2>&1; then
      uncapped_or_die "mkfs.ext4 missing"; return
    fi
    if [ ! -f "$DISK_IMG" ]; then
      echo "[jail] creating ${DISK} sandbox disk image ..." >&2
      fallocate -l "$DISK" "$DISK_IMG" 2>/dev/null || dd if=/dev/zero of="$DISK_IMG" bs=1M count="$(( ${DISK%G} * 1024 ))" status=none
      mkfs.ext4 -q -F "$DISK_IMG"
    fi
    if ! sudo -n mount -o loop "$DISK_IMG" "$MNT" 2>/dev/null; then
      uncapped_or_die "could not loop-mount $DISK_IMG (needs sudo)"; return
    fi
    sudo -n chown "$(id -un):$(id -gn)" "$MNT" 2>/dev/null || true
    echo "[jail] mounted ${DISK}-capped storage" >&2
  fi
  mkdir -p "$IMG_PROJECT" "$IMG_STATE" "$IMG_CFG"
}
# Fallback when the disk cap can't be applied: only if the operator opted in.
uncapped_or_die() {
  if [ "${OPENCODE_FP_ALLOW_UNCAPPED:-0}" = "1" ]; then
    echo "[jail] WARNING: $1 — running WITHOUT the ${DISK} disk cap (OPENCODE_FP_ALLOW_UNCAPPED=1)" >&2
    MNT="$HOSTBASE/uncapped"; IMG_PROJECT="$MNT/project"; IMG_STATE="$MNT/state"; IMG_CFG="$MNT/config"
    mkdir -p "$IMG_PROJECT" "$IMG_STATE" "$IMG_CFG"
  else
    echo "ERROR: $1. Refusing to run without the disk cap. Set OPENCODE_FP_ALLOW_UNCAPPED=1 to override." >&2
    exit 4
  fi
}

mkdir -p "$HOSTBASE"
# Serialize ALL access to the shared image (image create/mkfs/mount AND the symlink-safe
# regen): a concurrent jail could otherwise race image setup or plant a symlink between our
# cleanup and our writes (TOCTOU). Take the lock BEFORE ensure_storage and hold it through
# the jail's whole life — the fd survives `exec` into bwrap, released only when the jail
# exits. A second concurrent jail on the same image is refused.
exec {LOCKFD}>"$HOSTBASE/.lock"
# Wait briefly (not -n/instant-fail): when a previous session is still tearing down, its
# lock lingers for a moment — instant-fail made the jail "refuse to restart" right after
# closing it. flock auto-releases on process death, so a real holder means a live jail.
flock -w 15 "$LOCKFD" || { echo "ERROR: another jail is still using this sandbox image — close it first, or set OPENCODE_FP_* to run a separate one." >&2; exit 6; }

if [ "$MODE" != "selftest" ]; then ensure_storage; else mkdir -p "$IMG_PROJECT" "$IMG_STATE" "$IMG_CFG" 2>/dev/null || { MNT="$HOSTBASE/uncapped"; IMG_PROJECT="$MNT/project"; IMG_STATE="$MNT/state"; IMG_CFG="$MNT/config"; mkdir -p "$IMG_PROJECT" "$IMG_STATE" "$IMG_CFG"; }; fi

# The project + config + state dirs are model-writable (across runs), so the model could
# plant SYMLINKS at the names the host writes on the NEXT run, making `cat >`/`cp`/`git`
# follow them to clobber host files or read host secrets. Defuse by removing any existing
# file/symlink/dir at each target first (under the lock above, so no concurrent jail races
# us), then recreating fresh regular files/dirs.
# .git may be a real dir (keep the project's repo), or a model-planted symlink/gitfile that
# would redirect git into a host path. `[ -d ]` FOLLOWS a symlink, so test `-L` too: treat
# any symlink OR non-directory as hostile — remove it (rm -rf on a symlink removes the link,
# not its target) and re-init clean.
if [ -L "$IMG_PROJECT/.git" ] || [ ! -d "$IMG_PROJECT/.git" ]; then
  rm -rf "$IMG_PROJECT/.git" 2>/dev/null || true
  git -C "$IMG_PROJECT" init -q 2>/dev/null || true
fi

# ── refresh the freellmpool plugins INTO the config (so llmbuffet is never bind-mounted) ──
# rm -rf (no trailing slash) clears a planted file, symlink, OR directory at each name — and
# only AFTER this do we recreate plugin/tui-plugin, so no stale symlink is followed by mkdir.
rm -rf "$IMG_CFG/opencode.jsonc" "$IMG_CFG/tui.json" "$IMG_CFG/plugin" "$IMG_CFG/tui-plugin"
mkdir -p "$IMG_CFG/plugin" "$IMG_CFG/tui-plugin"
if [ -f "$INTEG/opencode/freellmpool.js" ]; then
  cp -f "$INTEG/opencode/freellmpool.js" "$IMG_CFG/plugin/freellmpool.js"
fi
if [ -d "$INTEG/opencode-tui" ]; then
  cp -rf "$INTEG/opencode-tui/." "$IMG_CFG/tui-plugin/"
fi

# ── /goal long-horizon plugin (auto-continues toward an objective on session.idle) ─────
# Installed as an npm spec opencode fetches into the capped image state (persists). It must
# be in BOTH the server (opencode.jsonc) and tui (tui.json) plugin lists. Set OPENCODE_FP_GOAL
# to a different package, or empty, to change/disable.
GOAL="${OPENCODE_FP_GOAL-@prevalentware/opencode-goal-plugin}"
case "$GOAL" in *[!A-Za-z0-9@/._-]*) echo "ERROR: OPENCODE_FP_GOAL has invalid chars" >&2; exit 2 ;; esac
if [ -n "$GOAL" ]; then
  PLUGIN_LIST="\"$J_CFG/plugin/freellmpool.js\", [\"$GOAL\", { \"auto_continue\": true, \"min_continue_interval_seconds\": 3 }]"
  TUI_PLUGIN_LIST="\"file:$J_CFG/tui-plugin\", \"$GOAL\""
else
  PLUGIN_LIST="\"$J_CFG/plugin/freellmpool.js\""
  TUI_PLUGIN_LIST="\"file:$J_CFG/tui-plugin\""
fi

# ── jail-only OpenCode config (regenerated each run; lives in the capped image) ────────
cat > "$IMG_CFG/opencode.jsonc" <<JSON
{
  "\$schema": "https://opencode.ai/config.json",
  "model": "$MODEL",
  "small_model": "$MODEL",
  // never self-update inside the jail (binary lives on the read-only host mount; the host
  // owns the version). Belt-and-suspenders with OPENCODE_DISABLE_AUTOUPDATE in the env.
  "autoupdate": false,
  // allow-all: NO approval prompts of any kind (incl. external_directory). The sandbox is
  // the real containment, so blanket-allow inside is safe.
  "permission": {
    "read": "allow", "list": "allow", "glob": "allow", "grep": "allow", "lsp": "allow",
    "edit": "allow", "bash": "allow", "task": "allow", "skill": "allow",
    "external_directory": "allow",
    "webfetch": "allow", "websearch": "allow"
  },
  "plugin": [ $PLUGIN_LIST ],
  "provider": {
    "freellmpool": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "freellmpool (free pool)",
      "options": {
        "baseURL": "$PROXY_URL/v1", "apiKey": "unused",
        "headerTimeout": 60000, "timeout": 600000, "chunkTimeout": 120000
      },
      "models": {
        "agent":   { "name": "agent (strongest tier + spread)", "tool_call": true, "structured_output": true, "limit": { "context": 128000, "output": 8192 } },
        "spread":  { "name": "spread (all providers + fast)", "tool_call": true, "structured_output": true, "limit": { "context": 128000, "output": 8192 } },
        "auto":    { "name": "auto",    "tool_call": true, "structured_output": true, "limit": { "context": 128000, "output": 8192 } },
        "fast":    { "name": "fast",    "tool_call": true, "structured_output": true, "limit": { "context": 128000, "output": 8192 } },
        "quality": { "name": "quality", "tool_call": true, "structured_output": true, "limit": { "context": 128000, "output": 8192 } },
        "fair":    { "name": "fair",    "tool_call": true, "structured_output": true, "limit": { "context": 128000, "output": 8192 } }
      }
    }
  }
}
JSON
printf '{\n  "plugin": [ %s ]\n}\n' "$TUI_PLUGIN_LIST" > "$IMG_CFG/tui.json"

# ── preflight: host proxy reachable (shared net) ──────────────────────────────────────
if [ "$MODE" != "selftest" ] && ! curl -fsS --max-time 3 "$PROXY_URL/healthz" >/dev/null 2>&1; then
  echo "ERROR: freellmpool proxy not reachable at $PROXY_URL — start it on the host:" >&2
  echo "         freellmpool proxy --port 8080   (or set OPENCODE_FP_PROXY_URL)" >&2
  exit 3
fi

# ── the sandbox ───────────────────────────────────────────────────────────────────────
BWRAP=(
  bwrap
  --clearenv
  --ro-bind /usr /usr
  --ro-bind-try /lib /lib
  --ro-bind-try /lib64 /lib64
  --ro-bind /bin /bin
  --ro-bind /sbin /sbin
  --ro-bind /etc /etc
  --proc /proc
  --dev /dev
  --size 2147483648 --tmpfs /tmp
  --size 67108864 --tmpfs /run
  --ro-bind-try /run/systemd/resolve/stub-resolv.conf /run/systemd/resolve/stub-resolv.conf
  --size 4294967296 --tmpfs "$HOME"
  --ro-bind-try "$HOME/.npm-global" "$HOME/.npm-global"
  --ro-bind-try "$HOME/.nvm" "$HOME/.nvm"
  --bind "$IMG_CFG" "$J_CFG"
  --bind "$IMG_STATE" "$J_STATE"
  --bind "$IMG_PROJECT" "$J_PROJECT"
  --setenv HOME "$HOME"
  --setenv USER "$(id -un)"
  --setenv TERM "${TERM:-xterm-256color}"
  --setenv PATH "$NODE_BIN:$HOME/.npm-global/bin:$HOME/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
  --setenv FREELLMPOOL_PROXY_URL "$PROXY_URL"
  --setenv OPENCODE_DISABLE_AUTOUPDATE "1"
  --setenv npm_config_prefix "$HOME/.npm-local"
  --setenv PIP_USER "1"
  --setenv PYTHONUSERBASE "$HOME/.local"
  --setenv GIT_AUTHOR_NAME "dev"
  --setenv GIT_AUTHOR_EMAIL "dev@localhost"
  --setenv GIT_COMMITTER_NAME "dev"
  --setenv GIT_COMMITTER_EMAIL "dev@localhost"
  --unshare-pid
  --unshare-uts
  --unshare-ipc
  --die-with-parent
  --chdir "$J_PROJECT"
)

# ── CPU + RAM caps via a transient cgroup-v2 scope ────────────────────────────────────
LIMIT=()
if [ "$MODE" != "selftest" ]; then
  if systemctl --user show-environment >/dev/null 2>&1; then
    LIMIT=( systemd-run --user --scope --quiet --collect
            -p CPUQuota="$(( CPUS * 100 ))%" -p MemoryMax="$MEM" -p MemorySwapMax=0 )
    echo "[jail] caps: CPU=${CPUS} core(s) · RAM=${MEM} · disk=${DISK} · model=${MODEL}" >&2
  elif command -v taskset >/dev/null 2>&1; then
    echo "[jail] systemd --user unavailable; CPU-pinning via taskset (no hard RAM cap)" >&2
    LIMIT=( taskset -c "0-$(( CPUS - 1 ))" )
  fi
fi

case "$MODE" in
  tui)
    # Interactive TUI needs the controlling terminal, so --new-session isn't usable here.
    # The TIOCSTI keystroke-injection escape (queue input into the host shell) is instead
    # blocked kernel-wide by dev.tty.legacy_tiocsti=0 (the Linux >=6.2 default). Fail closed
    # if that protection isn't in effect — use --run/--serve (which detach the tty) instead.
    # Fail CLOSED: require the sysctl to be readable AND 0. An unreadable/missing knob
    # (older kernel) is NOT assumed safe — refuse the interactive TUI and point at --run/--serve.
    ti="$(cat /proc/sys/dev/tty/legacy_tiocsti 2>/dev/null || true)"
    if [ "$ti" != "0" ]; then
      echo "ERROR: TIOCSTI injection protection not confirmed (dev.tty.legacy_tiocsti='$ti')." >&2
      echo "       Use --run/--serve (tty detached), or: sudo sysctl -w dev.tty.legacy_tiocsti=0" >&2
      exit 5
    fi
    echo "[jail] OpenCode on freellmpool — project: ~/$PROJECT" >&2
    exec ${LIMIT[@]+"${LIMIT[@]}"} "${BWRAP[@]}" -- opencode
    ;;
  run)
    # Headless: --new-session detaches the controlling tty so nothing can inject keystrokes.
    exec ${LIMIT[@]+"${LIMIT[@]}"} "${BWRAP[@]}" --new-session -- opencode run "${RUN_ARGS[@]}"
    ;;
  serve)
    # Keep the server password in the HOST-only base — never in model-writable state, so a
    # planted symlink can't read-through it.
    PW_FILE="$HOSTBASE/server.password"
    [ -L "$PW_FILE" ] && rm -f "$PW_FILE"
    [ -f "$PW_FILE" ] || (umask 077; head -c 24 /dev/urandom | base64 | tr -d '/+= ' > "$PW_FILE")
    echo "[jail] serve on $HOSTBIND:$PORT (basic-auth password at host: $PW_FILE)" >&2
    # Pass the secret via a bound file the entrypoint reads — NOT via --setenv/argv (which
    # `ps`/`/proc/<pid>/cmdline` would expose to other host processes).
    exec ${LIMIT[@]+"${LIMIT[@]}"} "${BWRAP[@]}" \
      --ro-bind "$PW_FILE" /run/oc_pw \
      --new-session \
      -- /bin/bash -c 'export OPENCODE_SERVER_PASSWORD="$(cat /run/oc_pw)"; exec opencode serve --port "$1" --hostname "$2" --print-logs --log-level INFO' _ "$PORT" "$HOSTBIND"
    ;;
  selftest)
    echo "[jail] containment + stealth self-test:" >&2
    exec "${BWRAP[@]}" -- bash -c '
      fail=0
      absent() { if [ -e "$1" ]; then echo "  FAIL leaked: $1"; fail=1; else echo "  ok   hidden: $1"; fi; }
      absent "$HOME/.ssh"; absent "$HOME/.polybot"; absent "$HOME/.config/minimax"
      absent "$HOME/.codex"; absent "$HOME/.aws"; absent "$HOME/llmbuffet"
      [ -e /home/ubuntu/llmbuffet ] && { echo "  FAIL leaked: /home/ubuntu/llmbuffet"; fail=1; } || echo "  ok   hidden: llmbuffet source"
      case "$PWD" in *sandbox*|*jail*) echo "  FAIL cwd not stealthy: $PWD"; fail=1;; *) echo "  ok   stealthy cwd: $PWD";; esac
      echo hi > "$HOME/__probe" 2>/dev/null && echo "  ok   home writable (ephemeral)" || { echo "  FAIL home not writable"; fail=1; }
      echo p > "$PWD/.__probe" 2>/dev/null && { echo "  ok   project writable (persists)"; rm -f "$PWD/.__probe"; } || { echo "  FAIL project not writable"; fail=1; }
      touch /usr/__x 2>/dev/null && { echo "  FAIL /usr writable"; rm -f /usr/__x; fail=1; } || echo "  ok   /usr read-only"
      [ -e /etc/passwd ] && echo "  ok   system looks real" || { echo "  FAIL no /etc/passwd"; fail=1; }
      echo
      [ "$fail" = 0 ] && echo "SELFTEST: PASS — secrets+llmbuffet hidden, stealthy cwd, only the project persists" || echo "SELFTEST: FAIL"
      exit "$fail"
    '
    ;;
esac
