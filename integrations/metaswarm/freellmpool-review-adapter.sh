#!/usr/bin/env bash
# freellmpool review adapter for metaswarm external-tools.
#
# Commands:
#   health      report local freellmpool availability and configured strong providers
#   review      review a worktree diff against a rubric/spec and emit a JSON envelope
#   implement   unsupported; freellmpool is a router, not a worktree-editing agent

set -euo pipefail

SCHEMA_VERSION="1"
TOOL_NAME="${FREELLMPOOL_ADAPTER_NAME:-freellmpool}"
TOOL_CMD="${FREELLMPOOL_CMD:-freellmpool}"
DEFAULT_MODEL="${FREELLMPOOL_MODEL:-strong-long-context}"
DEFAULT_ROUTING="${FREELLMPOOL_ROUTING:-quality}"
REVIEW_MODE="${FREELLMPOOL_REVIEW_MODE:-strong}"
MAX_MODELS="${FREELLMPOOL_MAX_MODELS:-7}"
MAX_TOKENS="${FREELLMPOOL_MAX_TOKENS:-65536}"
PROVIDER_TIMEOUT="${FREELLMPOOL_PROVIDER_TIMEOUT_SECONDS:-600}"
SYNTHESIS_TIMEOUT="${FREELLMPOOL_SYNTHESIS_TIMEOUT_SECONDS:-600}"
STRONG_PROVIDERS="${FREELLMPOOL_STRONG_PROVIDERS:-mistral,nvidia,openrouter}"
STRONG_MODELS="${FREELLMPOOL_STRONG_MODELS:-nvidia/moonshotai/kimi-k2.6,nvidia/z-ai/glm-5.1,nvidia/mistralai/mistral-large-3-675b-instruct-2512,mistral/mistral-large-latest,nvidia/nvidia/nemotron-3-ultra-550b-a55b,openrouter/nvidia/nemotron-3-ultra-550b-a55b:free,openrouter/openai/gpt-oss-120b:free}"
LOG_DIR="${METASWARM_LOG_DIR:-${TMPDIR:-/tmp}/metaswarm-freellmpool}"
SAFE_PATH="${FREELLMPOOL_ADAPTER_PATH:-/usr/local/bin:/usr/bin:/bin}"
HARD_MAX_MODELS="${FREELLMPOOL_MAX_MODELS_HARD_CAP:-16}"

case "$MAX_MODELS" in
  ''|*[!0-9]*) MAX_MODELS="7" ;;
esac
case "$HARD_MAX_MODELS" in
  ''|*[!0-9]*) HARD_MAX_MODELS="16" ;;
esac
if (( MAX_MODELS > HARD_MAX_MODELS )); then
  MAX_MODELS="$HARD_MAX_MODELS"
fi

XT_WORKTREE=""
XT_RUBRIC_FILE=""
XT_SPEC_FILE=""
XT_ATTEMPT="1"
XT_TIMEOUT="0"

# Interpreter for the provider-count helper, the only embedded helper that
# imports freellmpool (Python 3.11+ with the package importable). The other
# helpers use only the standard library and run on PATH python3. Resolution
# order: FREELLMPOOL_PYTHON -> the interpreter named in the freellmpool
# script's shebang (pip, pipx and uv console scripts name their own Python)
# -> the python3 beside the symlink-resolved freellmpool executable ->
# $VIRTUAL_ENV/bin/python3 -> PATH python3. The interpreter belonging to the
# freellmpool that will run the review comes before an active virtualenv,
# which may not have the package.
PYTHON_BIN=""
resolve_helper_python() {
  local tool_path real_path shebang candidate
  if [[ -n "${FREELLMPOOL_PYTHON:-}" ]]; then
    PYTHON_BIN="$FREELLMPOOL_PYTHON"
    return 0
  fi
  tool_path="$(command -v "$TOOL_CMD" 2>/dev/null || true)"
  if [[ -n "$tool_path" && -f "$tool_path" ]]; then
    shebang=""
    IFS= read -r -n 512 shebang 2>/dev/null <"$tool_path" || true
    if [[ "$shebang" == '#!/'* ]]; then
      candidate="${shebang#\#!}"
      candidate="${candidate%%[[:space:]]*}"
      if [[ "${candidate##*/}" == python* && -x "$candidate" ]]; then
        PYTHON_BIN="$candidate"
        return 0
      fi
    fi
    real_path="$(readlink -f "$tool_path" 2>/dev/null || printf '%s' "$tool_path")"
    candidate="$(dirname "$real_path")/python3"
    if [[ -x "$candidate" ]]; then
      PYTHON_BIN="$candidate"
      return 0
    fi
  fi
  if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python3" ]]; then
    PYTHON_BIN="$VIRTUAL_ENV/bin/python3"
    return 0
  fi
  PYTHON_BIN="python3"
  return 0
}
resolve_helper_python

# Shared fix-it guidance for every "helper interpreter unusable" diagnostic
# (helper stderr, health warning, review raw_log).
HELPER_FIX_HINT="Set FREELLMPOOL_PYTHON to a Python 3.11+ interpreter with freellmpool installed (for example the virtualenv used to install freellmpool), or activate that virtualenv before dispatch."

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --worktree)
        XT_WORKTREE="${2:-}"
        shift 2
        ;;
      --rubric-file)
        XT_RUBRIC_FILE="${2:-}"
        shift 2
        ;;
      --spec-file)
        XT_SPEC_FILE="${2:-}"
        shift 2
        ;;
      --attempt)
        XT_ATTEMPT="${2:-1}"
        shift 2
        ;;
      --timeout)
        XT_TIMEOUT="${2:-0}"
        shift 2
        ;;
      *)
        shift
        ;;
    esac
  done

  case "$XT_ATTEMPT" in
    ''|*[!0-9]*) XT_ATTEMPT="1" ;;
  esac
  case "$XT_TIMEOUT" in
    0|false|False|FALSE|none|None|NONE|off|Off|OFF) ;;
    ''|*[!0-9]*) XT_TIMEOUT="$PROVIDER_TIMEOUT" ;;
  esac
}

create_secure_tmp() {
  local tmp_dir
  tmp_dir="$(mktemp -d -t "metaswarm-freellmpool-XXXXXX")"
  chmod 700 "$tmp_dir"
  printf '%s' "$tmp_dir"
}

safe_invoke() {
  local timeout_secs="${1:?timeout required}"
  local stdout_file="${2:?stdout file required}"
  local stderr_file="${3:?stderr file required}"
  shift 3

  local exit_code=0
  case "$timeout_secs" in
    0|false|False|FALSE|none|None|NONE|off|Off|OFF)
      "$@" >"$stdout_file" 2>"$stderr_file" || exit_code=$?
      return "$exit_code"
      ;;
  esac

  if command -v timeout >/dev/null 2>&1; then
    timeout "$timeout_secs" "$@" >"$stdout_file" 2>"$stderr_file" || exit_code=$?
  else
    "$@" >"$stdout_file" 2>"$stderr_file" &
    local pid=$!
    local elapsed=0
    while kill -0 "$pid" 2>/dev/null; do
      if [[ "$elapsed" -ge "$timeout_secs" ]]; then
        kill -TERM "$pid" 2>/dev/null || true
        sleep 1
        kill -KILL "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        return 124
      fi
      sleep 1
      elapsed=$((elapsed + 1))
    done
    wait "$pid" 2>/dev/null || exit_code=$?
  fi

  return "$exit_code"
}

classify_error() {
  local exit_code="${1:-1}"
  local stderr_file="${2:-}"

  if [[ "$exit_code" -eq 124 ]]; then
    printf 'timeout'
    return 0
  fi
  if [[ "$exit_code" -eq 127 ]]; then
    printf 'tool_not_installed'
    return 0
  fi
  if [[ -n "$stderr_file" && -s "$stderr_file" ]]; then
    if grep -qi 'rate.limit\|rate_limit\|too many requests\|429' "$stderr_file"; then
      printf 'rate_limited'
      return 0
    fi
    if grep -qi 'auth\|unauthorized\|401\|403\|forbidden\|token.*expired\|invalid.*key\|api.*key' "$stderr_file"; then
      printf 'auth_expired'
      return 0
    fi
    if grep -qi 'context.*too.*large\|token.*limit\|context.*length\|max.*tokens\|too.*long\|exceeds.*limit' "$stderr_file"; then
      printf 'context_too_large'
      return 0
    fi
  fi

  printf 'tool_crash'
}

redact_log_file() {
  local log_file="${1:-}"
  [[ -n "$log_file" && -f "$log_file" ]] || return 0

  python3 - "$log_file" <<'PY'
import pathlib
import re
import sys

path = pathlib.Path(sys.argv[1])
text = path.read_text(encoding="utf-8", errors="replace")
patterns = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret)\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:MISTRAL_API_KEY|NVIDIA_API_KEY|OPENROUTER_API_KEY)\s*=\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)([?&](?:api[_-]?key|key|token)=)[^&\s]+"), r"\1[REDACTED]"),
]
for pattern, replacement in patterns:
    text = pattern.sub(replacement, text)
path.write_text(text, encoding="utf-8")
PY
}

strong_models_json() {
  python3 - "$STRONG_MODELS" <<'PY'
import json
import sys

models = [item.strip() for item in sys.argv[1].split(",") if item.strip()]
print(json.dumps(models))
PY
}

emit_json() {
  local command="${1:-}"
  local model="${2:-$DEFAULT_MODEL}"
  local attempt="${3:-1}"
  local exit_code="${4:-0}"
  local branch="${5:-}"
  local git_sha="${6:-}"
  local duration_seconds="${7:-0}"
  local raw_log_file="${8:-}"
  local error_type="${9:-}"

  python3 - "$SCHEMA_VERSION" "$TOOL_NAME" "$command" "$model" "$attempt" "$exit_code" \
    "$branch" "$git_sha" "$duration_seconds" "$raw_log_file" "$error_type" <<'PY'
import json
import pathlib
import re
import sys

(
    schema_version,
    tool,
    command,
    model,
    attempt,
    exit_code,
    branch,
    git_sha,
    duration_seconds,
    raw_log_file,
    error_type,
) = sys.argv[1:]

raw_log = ""
raw_log_path = raw_log_file
if raw_log_file:
    path = pathlib.Path(raw_log_file)
    if path.exists():
        raw_log = path.read_text(errors="replace")[-200000:]

patterns = [
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret)\s*[:=]\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)((?:MISTRAL_API_KEY|NVIDIA_API_KEY|OPENROUTER_API_KEY)\s*=\s*)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)([?&](?:api[_-]?key|key|token)=)[^&\s]+"), r"\1[REDACTED]"),
]
for pattern, replacement in patterns:
    raw_log = pattern.sub(replacement, raw_log)

print(json.dumps({
    "schema_version": schema_version,
    "tool": tool,
    "command": command,
    "model": model,
    "attempt": int(attempt or "1"),
    "exit_code": int(exit_code or "0"),
    "branch": branch,
    "git_sha": git_sha,
    "files_changed": [],
    "diff_stats": {"additions": 0, "deletions": 0},
    "duration_seconds": int(duration_seconds or "0"),
    "cost": {"input_tokens": 0, "output_tokens": 0},
    "raw_log": raw_log,
    "raw_log_path": raw_log_path,
    "error_type": error_type or None,
}))
PY
}

log_session() {
  local json_string="${1:?json string required}"
  mkdir -p "$LOG_DIR"
  printf '%s\n' "$json_string" >>"${LOG_DIR}/external-tools.jsonl" 2>/dev/null || true
}

emit_error() {
  local command="${1:-}"
  local model="${2:-$DEFAULT_MODEL}"
  local attempt="${3:-1}"
  local exit_code="${4:-1}"
  local stderr_file="${5:-}"
  local duration_seconds="${6:-0}"
  local raw_log_file="${7:-}"
  local error_type="${8:-}"

  if [[ -z "$error_type" ]]; then
    error_type="$(classify_error "$exit_code" "$stderr_file")"
  fi

  emit_json "$command" "$model" "$attempt" "$exit_code" "" "" "$duration_seconds" "$raw_log_file" "$error_type"
}

build_keyed_env() {
  KEYED_ENV=("FREELLMPOOL_ROUTING=$DEFAULT_ROUTING")
  add_provider_key_env "MISTRAL_API_KEY"
  add_provider_key_env "NVIDIA_API_KEY"
  add_provider_key_env "OPENROUTER_API_KEY"
  [[ -n "${FREELLMPOOL_CONFIG:-}" ]] && KEYED_ENV+=("FREELLMPOOL_CONFIG=$FREELLMPOOL_CONFIG")
  [[ -n "${FREELLMPOOL_CONFIG_FILE:-}" ]] && KEYED_ENV+=("FREELLMPOOL_CONFIG_FILE=$FREELLMPOOL_CONFIG_FILE")
  [[ -n "${FREELLMPOOL_KEYS_PATH:-}" ]] && KEYED_ENV+=("FREELLMPOOL_KEYS_PATH=$FREELLMPOOL_KEYS_PATH")
}

provider_key_env_errors() {
  local invalid=()
  local name value
  for name in MISTRAL_API_KEY NVIDIA_API_KEY OPENROUTER_API_KEY; do
    value="${!name:-}"
    [[ -z "$value" ]] && continue
    case "$value" in
      *$'\n'*|*$'\r'*|*$'\t'*|*" "*|*"="*) invalid+=("$name") ;;
    esac
  done
  if [[ "${#invalid[@]}" -gt 0 ]]; then
    local IFS=,
    printf '%s' "${invalid[*]}"
  fi
}

add_provider_key_env() {
  local name="${1:?name required}"
  local value="${!name:-}"
  [[ -z "$value" ]] && return 0
  case "$value" in
    *$'\n'*|*$'\r'*|*$'\t'*|*" "*|*"="*) return 0 ;;
  esac
  KEYED_ENV+=("$name=$value")
}

missing_strong_model_providers() {
  local configured_providers="${1:-}"
  python3 - "$STRONG_MODELS" "$configured_providers" <<'PY'
import sys

models = [item.strip() for item in sys.argv[1].split(",") if item.strip()]
configured = {item.strip() for item in sys.argv[2].split(",") if item.strip()}
missing = sorted({
    model.split("/", 1)[0]
    for model in models
    if "/" in model and model.split("/", 1)[0] not in configured
})
print(",".join(missing))
PY
}

count_configured_strong_providers() {
  # Output contract: line 1 = count, line 2 = comma-joined provider ids.
  # The special count -1 means the helper interpreter could not import
  # freellmpool (too old, or package not installed). Callers treat -1, a
  # failed helper, or empty output as helper_unavailable rather than guess at
  # auth state.
  "$PYTHON_BIN" - "$STRONG_PROVIDERS" "$HELPER_FIX_HINT" <<'PY'
import sys

try:
    from freellmpool.config import configured_providers
except Exception as exc:
    print(f"freellmpool review adapter helper could not import freellmpool via {sys.executable}: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(f"Fix: {sys.argv[2]}", file=sys.stderr)
    print(-1)
    print("")
    raise SystemExit(0)

wanted = {item.strip() for item in sys.argv[1].split(",") if item.strip()}
configured = sorted({provider.id for provider in configured_providers() if provider.id in wanted})
print(len(configured))
print(",".join(configured))
PY
}

cmd_health() {
  local status="unavailable"
  local auth_valid=false
  local version="not_installed"
  local strong_provider_count=0
  local configured_strong_providers=""

  if command -v "$TOOL_CMD" >/dev/null 2>&1; then
    version="$("$TOOL_CMD" --version 2>/dev/null | head -n 1 | tr -d '\r\n' || printf 'unknown')"
    local strong_provider_info
    strong_provider_info="$(count_configured_strong_providers 2>/dev/null || printf '%s\n' '-1')"
    strong_provider_count="$(printf '%s\n' "$strong_provider_info" | sed -n '1p')"
    strong_provider_count="${strong_provider_count:--1}"
    if [[ "$strong_provider_count" == "-1" ]]; then
      printf 'freellmpool review adapter helper interpreter %q could not run the provider check. %s\n' "$PYTHON_BIN" "$HELPER_FIX_HINT" >&2
    fi
    case "$strong_provider_count" in
      ''|*[!0-9]*) strong_provider_count="0" ;;
    esac
    configured_strong_providers="$(printf '%s\n' "$strong_provider_info" | sed -n '2p')"
    if [[ -z "$(provider_key_env_errors)" && "$strong_provider_count" -gt 0 ]]; then
      status="ready"
      auth_valid=true
    fi
  fi

  python3 - "$TOOL_NAME" "$status" "$version" "$auth_valid" "$DEFAULT_MODEL" "$DEFAULT_ROUTING" \
    "$REVIEW_MODE" "$MAX_MODELS" "$STRONG_PROVIDERS" "$configured_strong_providers" \
    "$strong_provider_count" "$(strong_models_json)" <<'PY'
import json
import sys

(
    tool,
    status,
    version,
    auth_valid,
    model,
    routing,
    review_mode,
    max_models,
    strong_providers,
    configured_strong_providers,
    strong_provider_count,
    strong_models_json,
) = sys.argv[1:]

print(json.dumps({
    "tool": tool,
    "status": status,
    "version": version,
    "auth_valid": auth_valid == "true",
    "model": model,
    "routing": routing,
    "review_mode": review_mode,
    "max_models": int(max_models),
    "strong_providers": strong_providers,
    "configured_strong_providers": configured_strong_providers,
    "strong_provider_count": int(strong_provider_count or "0"),
    "strong_models": json.loads(strong_models_json),
}))
PY
}

cmd_implement() {
  parse_args "$@"

  local tmp_dir raw_log_file result
  tmp_dir="$(create_secure_tmp)"
  raw_log_file="${tmp_dir}/unsupported.txt"
  printf 'freellmpool is review-only for metaswarm; use a coding agent for implementation.\n' >"$raw_log_file"
  result="$(emit_error "implement" "$DEFAULT_MODEL" "$XT_ATTEMPT" 2 "" 0 "$raw_log_file" "unsupported_role")"
  log_session "$result"
  printf '%s\n' "$result"
  rm -rf "$tmp_dir"
  return 2
}

cmd_review() {
  parse_args "$@"

  if [[ -z "$XT_WORKTREE" || ! -d "$XT_WORKTREE" ]]; then
    printf 'Error: --worktree is required for review and must exist\n' >&2
    return 1
  fi
  if [[ -z "$XT_RUBRIC_FILE" || ! -f "$XT_RUBRIC_FILE" ]]; then
    printf 'Error: --rubric-file is required for review and must exist\n' >&2
    return 1
  fi
  if [[ -z "$XT_SPEC_FILE" || ! -f "$XT_SPEC_FILE" ]]; then
    printf 'Error: --spec-file is required for review and must exist\n' >&2
    return 1
  fi

  local tmp_dir stdout_file stderr_file raw_log_file
  tmp_dir="$(create_secure_tmp)"
  stdout_file="${tmp_dir}/stdout.txt"
  stderr_file="${tmp_dir}/stderr.log"

  if ! command -v "$TOOL_CMD" >/dev/null 2>&1; then
    raw_log_file="${tmp_dir}/missing-tool.txt"
    printf 'freellmpool CLI not found on PATH. Install with: pip install freellmpool\n' >"$raw_log_file"
    local missing_json
    missing_json="$(emit_error "review" "$DEFAULT_MODEL" "$XT_ATTEMPT" 127 "" 0 "$raw_log_file" "tool_not_installed")"
    log_session "$missing_json"
    printf '%s\n' "$missing_json"
    rm -rf "$tmp_dir"
    return 1
  fi
  local tool_bin
  tool_bin="$(command -v "$TOOL_CMD")"

  local invalid_provider_keys
  invalid_provider_keys="$(provider_key_env_errors)"
  if [[ -n "$invalid_provider_keys" ]]; then
    raw_log_file="${tmp_dir}/invalid-provider-keys.txt"
    printf 'Invalid provider key environment variable value for: %s. Provider key env vars are not forwarded when they contain whitespace, equals signs, or control characters; use freellmpool keys add instead.\n' "$invalid_provider_keys" >"$raw_log_file"
    local invalid_key_json
    invalid_key_json="$(emit_error "review" "$DEFAULT_MODEL" "$XT_ATTEMPT" 2 "" 0 "$raw_log_file" "auth_missing")"
    log_session "$invalid_key_json"
    printf '%s\n' "$invalid_key_json"
    rm -rf "$tmp_dir"
    return 1
  fi

  local strong_provider_info strong_provider_count helper_stderr_file
  local configured_strong_providers=""
  helper_stderr_file="${tmp_dir}/provider-helper-stderr.txt"
  strong_provider_info="$(count_configured_strong_providers 2>"$helper_stderr_file" || printf '%s\n' '-1')"
  strong_provider_count="$(printf '%s\n' "$strong_provider_info" | sed -n '1p')"
  strong_provider_count="${strong_provider_count:--1}"
  configured_strong_providers="$(printf '%s\n' "$strong_provider_info" | sed -n '2p')"
  if [[ "$strong_provider_count" == "-1" ]]; then
    # Fail closed loudly: the helper interpreter cannot run this project, so no
    # provider-auth conclusion is possible. Never mislabel this auth_missing.
    raw_log_file="${tmp_dir}/helper-interpreter-unavailable.txt"
    {
      printf 'freellmpool review adapter helper interpreter %q could not run the provider check; the configured strong providers cannot be determined. %s\n' "$PYTHON_BIN" "$HELPER_FIX_HINT"
      if [[ -s "$helper_stderr_file" ]]; then
        printf 'Helper stderr:\n'
        tail -c 4000 "$helper_stderr_file"
      fi
    } >"$raw_log_file"
    local helper_json
    helper_json="$(emit_error "review" "$DEFAULT_MODEL" "$XT_ATTEMPT" 2 "" 0 "$raw_log_file" "helper_unavailable")"
    log_session "$helper_json"
    printf '%s\n' "$helper_json"
    rm -rf "$tmp_dir"
    return 1
  fi
  case "$strong_provider_count" in
    ''|*[!0-9]*) strong_provider_count="0" ;;
  esac
  if [[ "$strong_provider_count" -eq 0 ]]; then
    raw_log_file="${tmp_dir}/missing-strong-provider-keys.txt"
    printf 'No configured strong freellmpool providers. Configure at least one of: %s. For the default metaswarm review panel, set one or more of MISTRAL_API_KEY, NVIDIA_API_KEY, OPENROUTER_API_KEY, or use freellmpool keys add.\n' "$STRONG_PROVIDERS" >"$raw_log_file"
    local auth_json
    auth_json="$(emit_error "review" "$DEFAULT_MODEL" "$XT_ATTEMPT" 2 "" 0 "$raw_log_file" "auth_missing")"
    log_session "$auth_json"
    printf '%s\n' "$auth_json"
    rm -rf "$tmp_dir"
    return 1
  fi
  if [[ "$REVIEW_MODE" == "strong" || "$REVIEW_MODE" == "strong-long-context" ]]; then
    local missing_model_providers
    missing_model_providers="$(missing_strong_model_providers "$configured_strong_providers")"
    if [[ -n "$missing_model_providers" ]]; then
      raw_log_file="${tmp_dir}/missing-requested-provider-keys.txt"
      printf 'Strong review model list includes providers without configured keys: %s. Configure those provider keys or restrict FREELLMPOOL_STRONG_MODELS to configured providers: %s.\n' "$missing_model_providers" "$configured_strong_providers" >"$raw_log_file"
      local partial_auth_json
      partial_auth_json="$(emit_error "review" "$DEFAULT_MODEL" "$XT_ATTEMPT" 2 "" 0 "$raw_log_file" "auth_missing")"
      log_session "$partial_auth_json"
      printf '%s\n' "$partial_auth_json"
      rm -rf "$tmp_dir"
      return 1
    fi
  fi

  local diff_content rubric_content spec_content system_prompt review_prompt review_prompt_file
  diff_content="$(git -C "$XT_WORKTREE" diff HEAD 2>/dev/null || true)"
  if [[ -z "$diff_content" ]]; then
    diff_content="$(git -C "$XT_WORKTREE" diff HEAD~1 HEAD 2>/dev/null || true)"
  fi
  if [[ -z "$diff_content" ]]; then
    diff_content="$(git -C "$XT_WORKTREE" diff --cached 2>/dev/null || true)"
  fi
  if [[ -z "$diff_content" ]]; then
    raw_log_file="${tmp_dir}/empty-diff.txt"
    printf 'No git diff was captured from worktree %s; refusing to run an external review on an empty payload.\n' "$XT_WORKTREE" >"$raw_log_file"
    local empty_diff_json
    empty_diff_json="$(emit_error "review" "$DEFAULT_MODEL" "$XT_ATTEMPT" 2 "" 0 "$raw_log_file" "empty_diff")"
    log_session "$empty_diff_json"
    printf '%s\n' "$empty_diff_json"
    rm -rf "$tmp_dir"
    return 1
  fi
  rubric_content="$(cat "$XT_RUBRIC_FILE")"
  spec_content="$(cat "$XT_SPEC_FILE")"
  system_prompt="You are an adversarial code reviewer. Return one JSON object with keys verdict, findings, summary. Use PASS only when there are no blocking issues. Use FAIL when any blocking issue exists. Findings must include classification, citation, and explanation."
  review_prompt="$(cat <<PROMPT_EOF
Review the following change against the specification and rubric.

## Specification
${spec_content}

## Review Rubric
${rubric_content}

## Git Diff
\`\`\`diff
${diff_content}
\`\`\`
PROMPT_EOF
)"
  review_prompt_file="${tmp_dir}/review-prompt.md"
  printf '%s' "$review_prompt" >"$review_prompt_file"

  local start_time end_time duration exit_code
  start_time="$(date +%s)"
  exit_code=0

  build_keyed_env
  if [[ "$REVIEW_MODE" == "strong" || "$REVIEW_MODE" == "strong-long-context" ]]; then
    : >"$stdout_file"
    local answered=0
    local attempted=0
    local first_success_model=""
    local pids=()
    local pid_models=()
    local pid_stdout=()
    local pid_stderr=()
    local strong_models=()

    IFS=',' read -r -a strong_models <<<"$STRONG_MODELS"
    for model in "${strong_models[@]}"; do
      model="${model#"${model%%[![:space:]]*}"}"
      model="${model%"${model##*[![:space:]]}"}"
      [[ -z "$model" ]] && continue
      attempted=$((attempted + 1))
      if [[ "$attempted" -gt "$MAX_MODELS" ]]; then
        break
      fi

      local model_stdout="${tmp_dir}/model-${attempted}.out"
      local model_stderr="${tmp_dir}/model-${attempted}.err"
      safe_invoke "$PROVIDER_TIMEOUT" "$model_stdout" "$model_stderr" \
        env -i HOME="$HOME" PATH="$SAFE_PATH" "${KEYED_ENV[@]}" \
        "$tool_bin" ask \
          --model "$model" \
          --max-tokens "$MAX_TOKENS" \
          --temperature 0 \
          --timeout "$PROVIDER_TIMEOUT" \
          --json \
          --system "$system_prompt" \
          <"$review_prompt_file" &
      pids+=("$!")
      pid_models+=("$model")
      pid_stdout+=("$model_stdout")
      pid_stderr+=("$model_stderr")
    done

    local i
    for i in "${!pids[@]}"; do
      local model="${pid_models[$i]}"
      local model_stdout="${pid_stdout[$i]}"
      local model_stderr="${pid_stderr[$i]}"
      local model_exit=0
      if wait "${pids[$i]}"; then
        model_exit=0
      else
        model_exit=$?
      fi

      if [[ "$model_exit" -eq 0 && -s "$model_stdout" ]]; then
        answered=$((answered + 1))
        if [[ -z "$first_success_model" ]]; then
          first_success_model="$model"
        fi
        {
          printf '### %s\n' "$model"
          cat "$model_stdout"
          printf '\n\n'
        } >>"$stdout_file"
      else
        {
          printf '### %s unavailable\n' "$model"
          printf 'exit_code=%s error_type=%s\n' "$model_exit" "$(classify_error "$model_exit" "$model_stderr")"
          if [[ -s "$model_stderr" ]]; then
            sed -n '1,20p' "$model_stderr"
          fi
          printf '\n'
        } >>"$stderr_file"
      fi
    done

    if [[ "$answered" -eq 0 ]]; then
      exit_code=1
    else
      local synthesis_prompt="${tmp_dir}/synthesis-prompt.txt"
      {
        printf 'Synthesize these independent freellmpool reviews into one JSON object with keys verdict, findings, summary.\n'
        printf 'Return FAIL if any model found a credible BLOCKING issue. Preserve citations.\n\n'
        cat "$stdout_file"
      } >"$synthesis_prompt"

      local synthesis_stdout="${tmp_dir}/synthesis.out"
      local synthesis_stderr="${tmp_dir}/synthesis.err"
      local synthesis_exit=0
      safe_invoke "$SYNTHESIS_TIMEOUT" "$synthesis_stdout" "$synthesis_stderr" \
        env -i HOME="$HOME" PATH="$SAFE_PATH" "${KEYED_ENV[@]}" \
        "$tool_bin" ask \
          --model "$first_success_model" \
          --max-tokens "$MAX_TOKENS" \
          --temperature 0 \
          --timeout "$SYNTHESIS_TIMEOUT" \
          --json \
          --system "$system_prompt" \
          <"$synthesis_prompt" \
        || synthesis_exit=$?

      {
        printf '### SYNTHESIS via %s\n' "$first_success_model"
        if [[ "$synthesis_exit" -eq 0 && -s "$synthesis_stdout" ]]; then
          cat "$synthesis_stdout"
        else
          printf 'synthesis unavailable; use individual reviews above. exit_code=%s error_type=%s\n' "$synthesis_exit" "$(classify_error "$synthesis_exit" "$synthesis_stderr")"
          if [[ -s "$synthesis_stderr" ]]; then
            {
              printf '\n### SYNTHESIS STDERR\n'
              cat "$synthesis_stderr"
            } >>"$stderr_file"
          fi
          if [[ "$synthesis_exit" -eq 0 ]]; then
            exit_code=1
          else
            exit_code="$synthesis_exit"
          fi
        fi
        printf '\n'
      } >>"$stdout_file"
    fi
  elif [[ "$REVIEW_MODE" == "tokenmax" ]]; then
    safe_invoke "${XT_TIMEOUT:-$PROVIDER_TIMEOUT}" "$stdout_file" "$stderr_file" \
      env -i HOME="$HOME" PATH="$SAFE_PATH" "${KEYED_ENV[@]}" \
      "$tool_bin" tokenmax \
        --max-models "$MAX_MODELS" \
        --max-tokens "$MAX_TOKENS" \
        --timeout "$PROVIDER_TIMEOUT" \
        --system "$system_prompt" \
        "$review_prompt" \
      || exit_code=$?
  else
    safe_invoke "${XT_TIMEOUT:-$PROVIDER_TIMEOUT}" "$stdout_file" "$stderr_file" \
      env -i HOME="$HOME" PATH="$SAFE_PATH" "${KEYED_ENV[@]}" \
      "$tool_bin" ask \
        --max-tokens "$MAX_TOKENS" \
        --temperature 0 \
        --timeout "$PROVIDER_TIMEOUT" \
        --json \
        --system "$system_prompt" \
        "$review_prompt" \
      || exit_code=$?
  fi

  end_time="$(date +%s)"
  duration=$((end_time - start_time))

  mkdir -p "$LOG_DIR"
  raw_log_file="${LOG_DIR}/${TOOL_NAME}-review-$(date +%Y%m%dT%H%M%S)-$$.txt"
  {
    if [[ -s "$stdout_file" ]]; then
      cat "$stdout_file"
    fi
    if [[ -s "$stderr_file" ]]; then
      printf '\n### STDERR / MODEL FAILURES\n'
      cat "$stderr_file"
    fi
  } >"$raw_log_file" 2>/dev/null || true
  redact_log_file "$raw_log_file"

  local branch git_sha result
  branch="$(git -C "$XT_WORKTREE" rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  git_sha="$(git -C "$XT_WORKTREE" rev-parse HEAD 2>/dev/null || true)"

  if [[ "$exit_code" -ne 0 ]]; then
    result="$(emit_error "review" "$DEFAULT_MODEL" "$XT_ATTEMPT" "$exit_code" "$stderr_file" "$duration" "$raw_log_file")"
    log_session "$result"
    printf '%s\n' "$result"
    rm -rf "$tmp_dir"
    return 1
  fi

  result="$(emit_json "review" "$DEFAULT_MODEL" "$XT_ATTEMPT" 0 "$branch" "$git_sha" "$duration" "$raw_log_file" "")"
  log_session "$result"
  printf '%s\n' "$result"
  rm -rf "$tmp_dir"
}

command="${1:-}"
shift || true

case "$command" in
  health)
    cmd_health "$@"
    ;;
  implement)
    cmd_implement "$@"
    ;;
  review)
    cmd_review "$@"
    ;;
  second_opinion)
    cmd_review "$@"
    ;;
  *)
    cat >&2 <<USAGE
Usage: $(basename "$0") <command> [options]

Commands:
  health      Check freellmpool CLI and configured strong providers.
  review      Run freellmpool as a metaswarm adversarial reviewer.
  second_opinion  Alias for review when a dispatcher uses role names as commands.
  implement   Unsupported; this adapter is review-only.

Review options:
  --worktree <path>       Git worktree to review.
  --rubric-file <path>    Review rubric file.
  --spec-file <path>      Specification file.
  --attempt <N>           Attempt number for logs.
  --timeout <seconds>     Outer timeout for tokenmax/ask modes; 0 disables.

Environment:
  FREELLMPOOL_CMD                         freellmpool binary path.
  FREELLMPOOL_REVIEW_MODE                 strong, strong-long-context, tokenmax, or ask; default strong.
  FREELLMPOOL_STRONG_MODELS               comma-separated exact model ids for strong mode.
  FREELLMPOOL_STRONG_PROVIDERS            comma-separated provider ids required for ready health.
  FREELLMPOOL_MAX_MODELS                  max strong/tokenmax models, default 7.
  FREELLMPOOL_MAX_MODELS_HARD_CAP         safety cap for concurrent model calls, default 16.
  FREELLMPOOL_MAX_TOKENS                  max output tokens per model, default 65536.
  FREELLMPOOL_PROVIDER_TIMEOUT_SECONDS    per-model upstream timeout, default 600.
  FREELLMPOOL_SYNTHESIS_TIMEOUT_SECONDS   synthesis timeout, default 600.
  FREELLMPOOL_ADAPTER_PATH                PATH used for child freellmpool calls.
  METASWARM_LOG_DIR                       raw review log directory.
USAGE
    exit 2
    ;;
esac
