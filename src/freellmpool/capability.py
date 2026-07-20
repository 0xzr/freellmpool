"""Model-capability scoring for quality-tiered ("degradation-aware") routing.

Quality routing (``FREELLMPOOL_ROUTING=quality``) matches prompt *difficulty* to
model *capability*: hard prompts go to the strongest available model, easy ones to
lightweight models — which rations scarce strong-model quota so the pool stays
sharp as the day's daily caps fill.

Capability is grounded in real benchmarks, not guessed from names. The bundled
``capability_scores.json`` is built from public leaderboard data (LMArena Elo,
MIT-mirrored) mapped onto catalog model names. ``freellmpool capability sync``
refreshes it and can layer in the Artificial Analysis Intelligence Index under the
user's own access. Models the benchmark doesn't cover fall back to a coarse name
heuristic, then a neutral default.

All scores are normalized to 0.0–1.0 (higher = more capable). The runtime path is
pure and dependency-free; the optional sync uses stdlib ``urllib``.
"""

from __future__ import annotations

import bisect
import json
import math
import os
import re
import urllib.error
import urllib.request
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType

from .context import estimate_input_tokens

_BUNDLED_SCORES = Path(__file__).with_name("capability_scores.json")
_NEUTRAL = 0.5  # used when neither benchmark nor heuristic can say anything

# ---- name normalization ------------------------------------------------------

# A single leading "provider/" namespace, e.g. "openai/gpt-oss-120b".
_PROVIDER_PREFIX_RE = re.compile(r"^[^/]+/")
# A redundant leading vendor-ORG token kept before the family name, e.g.
# "meta-llama-3.3-70b" → "llama-3.3-70b", "cohere-command-r" → "command-r",
# "zai-org-glm-4.6" → "glm-4.6". Only true org tokens — never a family name
# (stripping "qwen"/"mistral"/"gemma" would destroy the model identity).
_VENDOR_PREFIX_RE = re.compile(
    r"^(?:meta|mistralai|nvidia|google|microsoft|nousresearch|cognitivecomputations|"
    r"deepseek-ai|cohere|c4ai|ai21|aisingapore|ibm-granite|ibm|zai-org|zai|"
    r"moonshotai|cf-moonshotai|minimaxai|xiaomimimo|opengvlab|sarvamai|01-ai|"
    r"openai|z-ai)[-_]",
    re.IGNORECASE,
)
# A doubled family token, e.g. "qwen-qwen3-30b" → "qwen3-30b",
# "llama-llama-2-7b" → "llama-2-7b" (some catalogs prefix the family twice).
_DOUBLED_FAMILY_RE = re.compile(r"^([a-z]{3,})[-_](?=\1)", re.IGNORECASE)
# Trailing date/version stamps: -2025-01-01, -20240620, -2507 (YYMM), -08, -03.
# Short 2-4 digit tags are versions/dates that benchmarks drop; sizes carry a "b".
_DATE_SUFFIX_RE = re.compile(r"[-_](?:\d{4}-\d{2}-\d{2}|\d{6,8}|\d{2,4})$")
# Trailing provider/packaging variant tags that don't change the base model's
# capability — stripping them lets a catalog name match a benchmark name. (Kept
# conservative: capability-distinguishing qualifiers like flash/mini/air are NOT
# stripped, so we never conflate a light variant with its stronger sibling.)
_VARIANT_SUFFIX_RE = re.compile(
    r"[-_](?:versatile|instant|latest|instruct|chat|it|hf|fp8|bf16|lora|"
    r"preview|turbo|tuned|free|online|beta)$",
    re.IGNORECASE,
)


@lru_cache(maxsize=8192)
def normalize_model_name(name: str) -> str:
    """A stable lookup key for a model name, robust to provider/vendor prefixes and
    packaging/date suffixes. The same function keys both the catalog names and the
    benchmark names, so equivalent variants collapse to one key (e.g.
    ``llama-3.3-70b-versatile`` and ``llama-3.3-70b-instruct`` → ``llama-3.3-70b``).
    """
    s = (name or "").strip().lower()
    s = _PROVIDER_PREFIX_RE.sub("", s)
    # Normalize separators early (":", "_", spaces → "-"), BEFORE peeling suffixes,
    # so OpenRouter's "model:free" and the like strip correctly.
    s = re.sub(r"[^a-z0-9.]+", "-", s).strip("-")
    s = _VENDOR_PREFIX_RE.sub("", s)
    s = _DOUBLED_FAMILY_RE.sub("", s)
    # Peel repeated trailing variant/date tags (a name may carry several).
    for _ in range(6):
        peeled = _VARIANT_SUFFIX_RE.sub("", s)
        peeled = _DATE_SUFFIX_RE.sub("", peeled)
        if peeled == s:
            break
        s = peeled
    return s.strip("-")


def _core(name: str) -> str:
    """Aggressive alphanumeric-only core, for a relaxed fallback match."""
    return re.sub(r"[^a-z0-9]+", "", normalize_model_name(name))


# ---- name heuristic (fallback only) ------------------------------------------

# Parameter count like "70b" / "8b" / "3.2b"; the look-behind avoids matching the
# tail of a version/date (so "...-3b" matches but "v0.3b" / "...123b-of-a-date"
# do not get mis-parsed mid-number).
_PARAM_RE = re.compile(r"(?<![.\d])(\d+(?:\.\d+)?)\s*b\b", re.IGNORECASE)
_DOWNWEIGHT_RE = re.compile(r"\b(?:flash|lite|mini|nano|tiny|instant|small|edge)\b", re.IGNORECASE)
_UPWEIGHT_RE = re.compile(r"\b(?:opus|reasoning|thinking|r1|o[1-9])\b", re.IGNORECASE)


def _heuristic_score(name: str) -> float:
    """Coarse 0–1 capability from the model name alone — the last-resort fallback
    when the benchmark table has no entry. Driven by parameter count, nudged by
    well-known size keywords."""
    s = (name or "").lower()
    params = [float(x) for x in _PARAM_RE.findall(s)]
    if params:
        b = max(params)
        if b >= 200:
            score = 0.85
        elif b >= 100:
            score = 0.78
        elif b >= 60:
            score = 0.68
        elif b >= 30:
            score = 0.58
        elif b >= 12:
            score = 0.46
        elif b >= 7:
            score = 0.38
        else:
            score = 0.28
    else:
        score = _NEUTRAL
    if _DOWNWEIGHT_RE.search(s):
        score = min(score, 0.40)
    if _UPWEIGHT_RE.search(s):
        score = max(score, 0.62)
    return round(score, 4)


# ---- bundled / user score table ----------------------------------------------


def user_capability_path() -> Path:
    """Where ``capability sync`` writes the refreshed table (overrides the bundle).

    Honors ``FREELLMPOOL_CAPABILITY_FILE`` for overrides and test injection.
    """
    override = os.environ.get("FREELLMPOOL_CAPABILITY_FILE")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".config" / "freellmpool" / "capability_scores.json"


def _read_scores(path: Path) -> dict[str, float]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    raw = data.get("scores", {}) if isinstance(data, dict) else {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for key, val in raw.items():
        score = val.get("score") if isinstance(val, dict) else val
        if score is None:
            continue
        try:
            value = float(score)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(value):  # reject NaN/inf rather than clamp them to 1.0
            continue
        # Clamp to [0,1] so a corrupt or malicious cache can't inject an
        # out-of-range capability that would distort routing.
        out[str(key)] = max(0.0, min(1.0, value))
    return out


@lru_cache(maxsize=8)
def _table_cached(user_str: str, _user_mtime: int) -> Mapping[str, float]:
    # Bundle first, user cache overlays it. ``_user_mtime`` is part of the cache
    # key only (not used in the body) so a `capability sync` is picked up without a
    # restart. Returned read-only (MappingProxyType) since it is shared across callers.
    table = _read_scores(_BUNDLED_SCORES)
    table.update(_read_scores(Path(user_str)))
    return MappingProxyType(table)


def capability_table() -> Mapping[str, float]:
    """Resolved ``{normalized_name: score}`` (bundled overlaid by the user cache).

    Cached and mtime-aware, so it is cheap to call once per routing decision.
    """
    user = user_capability_path()
    try:
        mtime = user.stat().st_mtime_ns
    except OSError:
        mtime = 0
    return _table_cached(str(user), mtime)


def model_capability(name: str, table: Mapping[str, float] | None = None) -> float:
    """Capability of a model on 0–1: benchmark score if known, else name heuristic.

    Pass ``table`` (from :func:`capability_table`) to avoid reloading it per call
    when scoring many candidates in one routing decision.
    """
    if table is None:
        table = capability_table()
    key = normalize_model_name(name)
    if key in table:
        return table[key]
    core = _core(name)
    if core and core in table:
        return table[core]
    return _heuristic_score(name)


# ---- prompt difficulty -------------------------------------------------------

_CODE_RE = re.compile(
    r"```|\bdef \b|\bclass \b|=>|;\s*$|^\s*(?:diff --git|@@ |[+-]{3} )", re.MULTILINE
)
_HARD_RE = re.compile(
    r"\b(?:debug|refactor|prove|analy[sz]e|algorithm|optimi[sz]e|architect|"
    r"design|reason|derive|complex|trade-?offs?|step by step|why)\b",
    re.IGNORECASE,
)


def _messages_text(messages) -> tuple[str, int]:
    """Concatenated text of all messages, plus the user/assistant turn count."""
    parts: list[str] = []
    turns = 0
    for m in messages or []:
        if not isinstance(m, dict):
            continue
        if m.get("role") in ("user", "assistant"):
            turns += 1
        content = m.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    parts.append(part["text"])
    return "\n".join(parts), turns


def prompt_difficulty(messages, max_tokens: int | None = None, tools=None) -> float:
    """Estimate how much capability a request needs, on 0–1.

    Cheap, dependency-free heuristics: input size (reusing the chars/4 token
    estimate), presence of code/diffs, reasoning cues, tool use, conversation
    depth, and a large requested output. A short plain prompt scores low (→ light
    models); long code/reasoning prompts score high (→ strong models).
    """
    text, turns = _messages_text(messages)
    tokens = estimate_input_tokens(messages, tools)
    score = 0.35  # baseline for a short, plain prompt
    if tokens > 8000:
        score += 0.30
    elif tokens > 2000:
        score += 0.20
    elif tokens > 500:
        score += 0.10
    if _CODE_RE.search(text):
        score += 0.20
    if _HARD_RE.search(text):
        score += 0.20
    if tools:
        score += 0.15
    if turns >= 6:
        score += 0.10
    if max_tokens and max_tokens >= 2048:
        score += 0.10
    return round(max(0.0, min(1.0, score)), 4)


# ---- the routing fit penalty -------------------------------------------------

_UNDERPOWER_PENALTY = 100.0  # never under-serve a hard prompt
_OVERPOWER_PENALTY = 1.0  # gently ration strong models on easy prompts


def fit_penalty(capability: float, need: float) -> float:
    """Routing penalty for using a model of ``capability`` on a request needing
    ``need`` (both 0–1). Lower is better.

    Asymmetric on purpose: a model weaker than the request is penalized heavily
    (so hard prompts get a strong model), while a model stronger than needed is
    penalized only lightly (so easy prompts prefer right-sized models, reserving
    scarce strong-model quota for when it's actually needed).
    """
    gap = capability - need
    if gap < 0:
        return (-gap) * _UNDERPOWER_PENALTY
    return gap * _OVERPOWER_PENALTY


# ---- building / refreshing the table -----------------------------------------


def normalize_scores(raw: dict[str, float]) -> dict[str, float]:
    """Convert a benchmark's raw scores (Elo, Index, pass-rate, …) into a 0–1
    *percentile rank*, keyed by normalized model name (equivalent variants collapse
    to their best raw score).

    Percentile rank — "the fraction of this benchmark's models it beats" — is used
    instead of min-max because it is robust to each benchmark's distribution shape
    and outliers, so scores from *different* benchmarks become roughly comparable
    when the table mixes sources (a top-decile model reads ~0.9 whether it came from
    Arena Elo or the AA Index, even though their raw scales differ wildly). It also
    gives quality routing a clean reading: difficulty ``d`` wants a model above the
    ``d`` percentile.
    """
    if not raw:
        return {}
    by_key: dict[str, float] = {}
    for name, value in raw.items():
        key = normalize_model_name(name)
        if not key:
            continue
        by_key[key] = max(by_key.get(key, float("-inf")), float(value))
    values = sorted(by_key.values())
    n = len(values)
    out: dict[str, float] = {}
    for key, value in by_key.items():
        lo = bisect.bisect_left(values, value)
        hi = bisect.bisect_right(values, value)
        out[key] = round((lo + hi) / 2.0 / n, 4)  # midrank percentile
    return out


def build_capability_table(
    *,
    aa_scores: dict[str, float] | None = None,
    arena_scores: dict[str, float] | None = None,
    aider_scores: dict[str, float] | None = None,
    catalog_names: list[str] | None = None,
) -> dict[str, dict]:
    """Resolve per-model capability with precedence Artificial Analysis → Arena →
    Aider → (runtime) heuristic, restricted to the catalog's model names.

    Returns ``{normalized_name: {"score": float, "source": ...}}`` for every catalog
    model a benchmark covers. Each model is resolved in priority order: a *direct*
    match (exact name, or alphanumeric-core to bridge separator differences) from the
    highest-precedence source wins first; failing that, a *same-family, same-size*
    approximation is borrowed (tagged ``…~``). Models nothing covers are omitted —
    they get the name heuristic at runtime. The ``*_scores`` args are raw benchmark
    dicts (model name → raw number); each is percentile-rank normalized independently
    (see :func:`normalize_scores`), which makes scores from different benchmarks
    roughly comparable. Precedence follows measured correlation with AA (Arena ≈ 0.73
    > Aider ≈ 0.60).
    """
    from .config import load_catalog

    if catalog_names is None:
        catalog_names = [m.name for p in load_catalog() for m in p.models]

    aa = normalize_scores(aa_scores or {})
    arena = normalize_scores(arena_scores or {})
    aider = normalize_scores(aider_scores or {})
    resolvers = [
        _index_source("aa", aa),
        _index_source("arena", arena),
        _index_source("aider", aider),
    ]
    families = set().union(*(r["families"] for r in resolvers))

    # Stems shared by ≥2 distinct *sized* catalog names (e.g. llama-3.1-8b and
    # llama-3.1-405b both strip to "llama3.1"). The sized→unsized match must not be
    # used for these, or both sizes would get one (wrong) score.
    stem_keys: dict[str, set[str]] = {}
    for name in catalog_names:
        key = normalize_model_name(name)
        if key and _largest_params(key) is not None:
            stem_keys.setdefault(_alnum(_strip_params(key)), set()).add(key)
    ambiguous_stems = {stem for stem, keys in stem_keys.items() if len(keys) > 1}

    table: dict[str, dict] = {}
    for name in catalog_names:
        key = normalize_model_name(name)
        if not key or key in table:
            continue
        entry = _resolve_capability(key, resolvers, families, ambiguous_stems)
        if entry is not None:
            table[key] = entry
    return table


def _alnum(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", s)


def _strip_params(key: str) -> str:
    """Drop ``Nb`` size tokens, e.g. ``mistral-large-3-675b`` → ``mistral-large-3``."""
    return re.sub(r"(?<![.\d])\d+(?:\.\d+)?b\b", "", key)


def _largest_params(key: str) -> float | None:
    params = [float(x) for x in re.findall(r"(?<![.\d])(\d+(?:\.\d+)?)b\b", key)]
    return max(params) if params else None


def _leading_family(key: str) -> str:
    """The leading alpha run of a normalized key (the model family), e.g.
    ``llama-3.1-8b`` → ``llama``. Empty if shorter than 4 chars."""
    m = re.match(r"[a-z]+", key)
    fam = m.group(0) if m else ""
    return fam if len(fam) >= 4 else ""


def _index_source(label: str, norm_scores: dict[str, float]) -> dict:
    """Precompute the lookup indexes for one benchmark source:
    exact-by-name, alphanumeric-core, paramless-core (only entries without a size
    token, for matching a sized catalog name to an unsized benchmark name), a
    (family, params) index for same-family/same-size approximation, and the set of
    known leading-family tokens (so approximation can't latch onto a stray word)."""
    core: dict[str, float] = {}
    noparam_core: dict[str, float] = {}
    fp: dict[tuple[str, float], float] = {}
    families: set[str] = set()
    for key, value in norm_scores.items():
        ck = _alnum(key)
        if ck:
            core[ck] = max(core.get(ck, 0.0), value)
        fam = _leading_family(key)
        if fam:
            families.add(fam)
        params = _largest_params(key)
        if params is None:
            if ck:
                noparam_core[ck] = max(noparam_core.get(ck, 0.0), value)
        elif fam:
            # Index approximation only under the benchmark entry's own family token.
            fp[(fam, params)] = max(fp.get((fam, params), 0.0), value)
    return {
        "label": label,
        "exact": norm_scores,
        "core": core,
        "noparam": noparam_core,
        "fp": fp,
        "families": families,
    }


def _resolve_capability(
    key: str, resolvers: list[dict], families: set[str], ambiguous_stems: set[str]
) -> dict | None:
    """Resolve one normalized model key against ordered resolvers (AA → Arena → Aider).

    Tries, in order: exact name, alphanumeric core, then a sized→unsized core match
    (so ``mistral-large-3-675b`` finds ``mistral-large-3`` — skipped when the stem is
    shared by multiple catalog sizes, which would conflate them). A *direct* match
    from any source beats a *same-family/same-size approximation* (tagged ``…~``),
    and approximation only fires on a token that is a known benchmark family — never
    a stray word — so an unrelated name can't borrow a family's score."""
    core = _alnum(key)
    np_core = _alnum(_strip_params(key))
    has_params = np_core != core
    for r in resolvers:
        if key in r["exact"]:
            return {"score": r["exact"][key], "source": r["label"]}
        if core in r["core"]:
            return {"score": r["core"][core], "source": r["label"]}
        if has_params and np_core and np_core not in ambiguous_stems and np_core in r["noparam"]:
            return {"score": r["noparam"][np_core], "source": r["label"]}
    params = _largest_params(key)
    if params is not None:
        tokens = [t for t in re.findall(r"[a-z]+", key) if len(t) >= 4 and t in families]
        for r in resolvers:
            for tok in tokens:
                if (tok, params) in r["fp"]:
                    return {"score": r["fp"][(tok, params)], "source": f"{r['label']}~"}
    return None


# ---- benchmark fetching (used by `capability sync` and bundle generation) -----

# LMArena Elo via a friendly, daily-updated mirror of the public leaderboard.
ARENA_DATASET = "mathewhe/chatbot-arena-elo"
_HF_ROWS_URL = "https://datasets-server.huggingface.co/rows"
# Artificial Analysis Intelligence Index — fetched only when the user provides
# their own API key (keeps the bundled snapshot free of AA-licensed data; AA's
# terms require attribution and don't grant public redistribution, so it is never
# bundled — only cached locally under the user's own key, as AA requests).
AA_DEFAULT_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"
# Aider code-editing leaderboard — Apache-2.0, redistributable. A real benchmark
# that fills a few open/code models Arena lacks (codestral, command-r, …).
AIDER_URLS = (
    "https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/polyglot_leaderboard.yml",
    "https://raw.githubusercontent.com/Aider-AI/aider/main/aider/website/_data/edit_leaderboard.yml",
)
_MAX_FETCH_BYTES = 8 * 1024 * 1024
# The AA key may only ever be sent to AA's own host (a misconfigured URL must not
# leak the secret to a third party).
_AA_ALLOWED_HOSTS = frozenset({"artificialanalysis.ai", "www.artificialanalysis.ai"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never auto-follow redirects, so a request carrying an API key can't forward
    it to a redirect target (credential leak / SSRF)."""

    def redirect_request(self, *args, **kwargs):  # noqa: D102
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect())


def _get_text(url: str, *, timeout: float, headers: dict[str, str] | None = None) -> str:
    if not url.lower().startswith("https://"):
        raise ValueError(f"refusing non-https benchmark URL: {url!r}")
    request = urllib.request.Request(url, headers=headers or {})
    with _NO_REDIRECT_OPENER.open(request, timeout=timeout) as response:
        raw = response.read(_MAX_FETCH_BYTES + 1)
    if len(raw) > _MAX_FETCH_BYTES:
        raise ValueError(f"benchmark response exceeds {_MAX_FETCH_BYTES} bytes; refusing to load")
    return raw.decode("utf-8")


def _get_json(url: str, *, timeout: float, headers: dict[str, str] | None = None):
    return json.loads(_get_text(url, timeout=timeout, headers=headers))


def fetch_aider_scores(*, timeout: float = 20.0) -> dict[str, float]:
    """Pull the Aider code-editing leaderboards (model name → best pass rate).

    Public, Apache-2.0 data in the Aider repo (no key). The YAML is parsed line by
    line (``model:`` / ``pass_rate_2:``) to avoid a yaml dependency.
    """
    scores: dict[str, float] = {}
    for url in AIDER_URLS:
        try:
            text = _get_text(url, timeout=timeout)
        except (OSError, ValueError, urllib.error.HTTPError):
            continue
        model: str | None = None
        for line in text.splitlines():
            name_match = re.match(r"\s*model:\s*(.+?)\s*$", line)
            if name_match:
                model = name_match.group(1)
            rate_match = re.match(r"\s*pass_rate_2:\s*([\d.]+)", line)
            if rate_match and model:
                scores[model] = max(scores.get(model, 0.0), float(rate_match.group(1)))
                model = None
    return scores


def fetch_arena_scores(*, timeout: float = 20.0) -> dict[str, float]:
    """Pull the full LMArena Elo leaderboard (model name → Arena Elo).

    Reads the public, MIT-mirrored dataset via the HuggingFace datasets-server
    (no key required). Paginates until exhausted.
    """
    scores: dict[str, float] = {}
    offset = 0
    page = 100
    while True:
        url = (
            f"{_HF_ROWS_URL}?dataset={ARENA_DATASET.replace('/', '%2F')}"
            f"&config=default&split=train&offset={offset}&length={page}"
        )
        data = _get_json(url, timeout=timeout)
        rows = data.get("rows", []) if isinstance(data, dict) else []
        if not rows:
            break
        for entry in rows:
            row = entry.get("row", {}) if isinstance(entry, dict) else {}
            name = row.get("Model")
            elo = row.get("Arena Score")
            if isinstance(name, str) and isinstance(elo, (int, float)):
                scores[name] = float(elo)
        total = data.get("num_rows_total") if isinstance(data, dict) else None
        offset += page
        if not isinstance(total, int) or offset >= total:
            break
    return scores


def fetch_aa_scores(
    *, api_key: str, url: str = AA_DEFAULT_URL, timeout: float = 20.0
) -> dict[str, float]:
    """Pull the Artificial Analysis Intelligence Index (model name → index).

    Requires the caller's own AA API key (so AA-licensed data is only ever fetched
    under the user's access, never bundled). Best-effort: tolerates schema drift by
    scanning common field names; returns {} on any failure. The key is only ever
    sent to an AA host (a misconfigured ``url`` cannot leak it elsewhere).
    """
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in _AA_ALLOWED_HOSTS:
        raise ValueError(f"refusing to send the AA key to a non-AA/non-https URL: {url!r}")
    try:
        data = _get_json(url, timeout=timeout, headers={"x-api-key": api_key})
    except (OSError, ValueError, urllib.error.HTTPError):
        return {}
    rows = data.get("data", data) if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return {}
    scores: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = row.get("slug") or row.get("name")
        evals = row.get("evaluations")
        value = (
            evals.get("artificial_analysis_intelligence_index") if isinstance(evals, dict) else None
        )
        if isinstance(name, str) and isinstance(value, (int, float)):
            scores[name] = float(value)
    return scores


def sync_capability_table(
    *,
    timeout: float = 20.0,
    aa_api_key: str | None = None,
    aa_url: str = AA_DEFAULT_URL,
    path: Path | None = None,
) -> tuple[Path, dict]:
    """Fetch benchmarks, rebuild the score table, and write the user cache.

    Returns ``(path, stats)`` where ``stats`` reports how many models were mapped
    and from which source. Artificial Analysis is included only when ``aa_api_key``
    is provided. Clears the in-process table cache so the refresh takes effect.
    """
    path = path or user_capability_path()
    arena = fetch_arena_scores(timeout=timeout)
    aider = fetch_aider_scores(timeout=timeout)
    aa = fetch_aa_scores(api_key=aa_api_key, url=aa_url, timeout=timeout) if aa_api_key else {}
    if aa:
        # AA data must never be written into the packaged bundle (its terms forbid
        # redistribution) — only into a user cache outside the package tree.
        package_dir = Path(__file__).resolve().parent
        if package_dir == path.resolve().parent or package_dir in path.resolve().parents:
            raise ValueError(
                f"refusing to write Artificial Analysis data into the package dir: {path}. "
                "AA scores may only be cached outside the installed package."
            )
    table = build_capability_table(aa_scores=aa, arena_scores=arena, aider_scores=aider)
    by_source: dict[str, int] = {}
    for entry in table.values():
        by_source[entry["source"]] = by_source.get(entry["source"], 0) + 1
    payload = {
        "meta": {
            "arena_models": len(arena),
            "aider_models": len(aider),
            "aa_models": len(aa),
            "mapped": len(table),
        },
        "scores": table,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    _table_cached.cache_clear()
    return path, {
        "arena": len(arena),
        "aider": len(aider),
        "aa": len(aa),
        "mapped": len(table),
        "by_source": by_source,
    }
