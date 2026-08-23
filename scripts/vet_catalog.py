#!/usr/bin/env python3
"""Maintainer tool: audit ``providers.toml`` against live provider state.

For every *configured* provider (its key — if any — is present in the
environment) this:

1. **Discovers** the provider's live model list and diffs it against the
   catalog, surfacing models the provider added (candidates to add) and models
   the catalog lists that the provider no longer offers (candidates to remove).
2. **Pings** every catalog model — both ``enabled`` and ``enabled = false`` — with
   a small completion through the *real* :func:`freellmpool.client.call`
   path, retrying transient failures, so the report reflects what auto-routing
   would actually experience.

It writes a JSON report and a short human summary. It never prints or stores
API keys. Nothing here is imported by the package; it is a standalone
maintainer utility.

Usage::

    python scripts/vet_catalog.py                      # full audit, all configured providers
    python scripts/vet_catalog.py -p groq,mistral      # only these providers
    python scripts/vet_catalog.py --enabled-only       # skip pinging disabled models
    python scripts/vet_catalog.py --no-discover        # skip live /models listing
    python scripts/vet_catalog.py --report out.json    # report path (default: /tmp/flp_vet_report.json)
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Make the package importable when run from a source checkout without install.
_SRC = Path(__file__).resolve().parent.parent / "src"
if _SRC.is_dir():
    sys.path.insert(0, str(_SRC))

import httpx  # noqa: E402

from freellmpool import client as flp_client  # noqa: E402
from freellmpool.catalog_validation import normalize_model_listing  # noqa: E402
from freellmpool.config import configured_providers, effective_env, load_catalog  # noqa: E402
from freellmpool.errors import ProviderHTTPError  # noqa: E402
from freellmpool.models import Provider  # noqa: E402

# Substrings that mark a model id as non-chat (image/audio/embed/safety/etc.).
# Used only to flag discovery candidates as unlikely-chat — never to hide them.
_NON_CHAT_HINTS = (
    "whisper",
    "tts",
    "text-to-speech",
    "speech",
    "transcribe",
    "audio",
    "voxtral",
    "orpheus",
    "bark",
    "parler",
    "embed",
    "rerank",
    "reranker",
    "moderation",
    "guard",
    "safety",
    "detector",
    "reward",
    "-pii",
    "deplot",
    "kosmos",
    "nemotron-parse",
    "riva-translate",
    "ising-calibration",
    "stable-diffusion",
    "diffusion",
    "sdxl",
    "sd-",
    "flux",
    "dall-e",
    "dalle",
    "imagen",
    "lyria",
    "sora",
    "veo",
    "image-",
    "-image",
    "bge-",
    "arctic-embed",
    "nemoretriever",
    "nv-embed",
    "ocr",
    "clip",
    "stable-video",
    "nvclip",
)

_PING_MESSAGES = [{"role": "user", "content": "Reply with the single word: pong"}]
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 520, 522, 524}


def _looks_chat(model_id: str) -> bool:
    low = model_id.lower()
    return low != "ppl" and not any(h in low for h in _NON_CHAT_HINTS)


# --------------------------------------------------------------------------- #
# Discovery: list a provider's live models.
# --------------------------------------------------------------------------- #
def _http_get(url: str, headers: dict, timeout: float = 20.0):
    resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    resp.raise_for_status()
    return resp.json()


def _http_get_obj(url: str, headers: dict, timeout: float = 20.0) -> dict:
    data = _http_get(url, headers, timeout)
    return data if isinstance(data, dict) else {}


def list_live_models(provider: Provider, env: dict) -> list[str]:
    """Return the provider's live model ids, or [] if listing is unsupported.

    Filters aggregator listings to free routes (the only tier the catalog
    tracks) and Cloudflare to text-generation models via the native model-search
    route.
    """
    key = provider.api_key(env)
    auth = {"Authorization": f"Bearer {key}"} if key else {}

    if provider.id == "pollinations":
        return _list_fallback(provider)

    if provider.adapter == "gemini":
        data = _http_get_obj(f"{provider.base_url}/models?key={key}", {})
        return [m["name"].split("/")[-1] for m in data.get("models", []) if "name" in m]

    if provider.adapter == "cloudflare":
        acct = env.get("CLOUDFLARE_ACCOUNT_ID", "")
        url = f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/models/search?per_page=1000"
        data = _http_get_obj(url, auth)
        out = []
        for m in data.get("result", []) or []:
            task = ((m.get("task") or {}).get("name") or "").lower()
            name = m.get("name")
            if name and ("text generation" in task or not task):
                out.append(name)
        return out

    # OpenAI-shape /models for everyone else.
    try:
        data = _http_get(f"{provider.base_url}/models", auth)
    except Exception:
        # Some keyless providers (e.g. Pollinations) expose a non-standard list.
        return _list_fallback(provider)

    rows = data.get("data") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        return []
    ids = []
    for m in rows:
        mid = m.get("id") if isinstance(m, dict) else (m if isinstance(m, str) else None)
        if mid:
            ids.append(mid)
    if provider.id in {"kilo", "openrouter"}:
        free_aliases = {"openrouter/free", "kilo-auto/free"}
        ids = [i for i in ids if i.endswith(":free") or i in free_aliases]
    elif provider.id == "opencode":
        ids = [i for i in ids if i.endswith("-free")]
    return ids


def _list_fallback(provider: Provider) -> list[str]:
    """Best-effort listing for providers with a non-OpenAI /models shape."""
    if provider.id == "pollinations":
        try:
            data = _http_get("https://text.pollinations.ai/models", {})
        except Exception:
            return []
        return list(normalize_model_listing(data))
    return []


# --------------------------------------------------------------------------- #
# Live ping: exercise the real completion path.
# --------------------------------------------------------------------------- #
def ping_model(provider: Provider, model_name: str, env: dict, timeout: float) -> dict:
    """Ping one model, retrying transient failures. Returns a result record."""
    key = provider.api_key(env)
    last_err = ""
    last_status: int | None = None
    attempts = 0
    for attempt in range(3):
        attempts = attempt + 1
        t0 = time.monotonic()
        try:
            reply = flp_client.call(
                provider,
                model_name,
                _PING_MESSAGES,
                api_key=key,
                env=env,
                max_tokens=256,
                temperature=0.0,
                timeout=timeout,
            )
            dt = round(time.monotonic() - t0, 2)
            text = reply.text.strip()
            if text:
                return {
                    "ok": True,
                    "status": 200,
                    "latency_s": dt,
                    "empty": False,
                    "snippet": text[:60],
                    "attempts": attempts,
                    "error": "",
                }
            last_status = 200
            last_err = "empty completion"
        except ProviderHTTPError as exc:
            last_status = exc.status
            last_err = str(exc)[:200]
            if exc.status not in _RETRYABLE_STATUS:
                break
        except (httpx.HTTPError, Exception) as exc:  # noqa: BLE001 — record + retry
            last_status = None
            last_err = f"{type(exc).__name__}: {exc}"[:200]
        time.sleep(1.5 * (attempt + 1))
    return {
        "ok": False,
        "status": last_status,
        "latency_s": None,
        "empty": last_status == 200 and last_err == "empty completion",
        "snippet": "",
        "attempts": attempts,
        "error": last_err,
        "classification": _classify(last_status, last_err),
    }


def _classify(status: int | None, err: str) -> str:
    """Bucket a failure so the caller can decide enable/disable safely."""
    low = err.lower()
    if status in (401, 403):
        return "auth"  # provider-level, not the model's fault
    if status == 429:
        return "rate_limited"  # transient — do NOT disable
    if (
        status in (404, 410)
        or "not found" in low
        or "does not exist" in low
        or "decommission" in low
        or "end of life" in low
        or "retired" in low
        or "not a valid model" in low
        or "no longer" in low
    ):
        return "dead"  # model gone — disable
    if status == 400 and (
        "model" in low
        and (
            "not" in low
            or "invalid" in low
            or "support" in low
            or "unavailable" in low
            or "unknown" in low
        )
    ):
        return "dead"
    if status == 200 and "empty completion" in low:
        return "empty"
    if status is None:
        return "unreachable"  # network/timeout — transient
    if status and 500 <= status < 600:
        return "server_error"  # transient
    return "other"


# --------------------------------------------------------------------------- #
# Orchestration.
# --------------------------------------------------------------------------- #
def audit(
    providers: list[Provider],
    env: dict,
    *,
    enabled_only: bool,
    discover: bool,
    workers: int,
    timeout: float,
    per_provider_cap: int,
) -> dict:
    sems: dict[str, threading.Semaphore] = {
        p.id: threading.Semaphore(per_provider_cap) for p in providers
    }
    report: dict = {"providers": {}}

    # Discovery first (cheap, one call per provider) so the summary has it even
    # if pings are interrupted.
    for p in providers:
        catalog_names = [m.name for m in p.models]
        entry = {
            "label": p.label,
            "adapter": p.adapter,
            "catalog_total": len(p.models),
            "catalog_enabled": sum(1 for m in p.models if m.enabled),
            "discover_ok": False,
            "live_count": 0,
            "new_models": [],  # live but not in catalog
            "removed_models": [],  # catalog but not live (provider dropped)
            "models": {},  # name -> ping result
        }
        if discover:
            try:
                live = set(list_live_models(p, env))
                entry["discover_ok"] = True
                entry["live_count"] = len(live)
                cat = set(catalog_names)
                new = sorted(live - cat)
                entry["new_models"] = [{"name": n, "likely_chat": _looks_chat(n)} for n in new]
                # Only flag removals when listing clearly worked (non-empty).
                if live:
                    entry["removed_models"] = sorted(cat - live)
            except Exception as exc:  # noqa: BLE001
                entry["discover_error"] = f"{type(exc).__name__}: {exc}"[:200]
        report["providers"][p.id] = entry

    # Build the ping work-list.
    tasks: list[tuple[Provider, str, bool]] = []
    for p in providers:
        for m in p.models:
            if enabled_only and not m.enabled:
                continue
            tasks.append((p, m.name, m.enabled))

    done = 0
    total = len(tasks)
    lock = threading.Lock()

    def _run(p: Provider, name: str, was_enabled: bool):
        with sems[p.id]:
            res = ping_model(p, name, env, timeout)
        res["was_enabled"] = was_enabled
        return p.id, name, res

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_run, p, name, en) for (p, name, en) in tasks]
        for fut in as_completed(futs):
            pid, name, res = fut.result()
            report["providers"][pid]["models"][name] = res
            with lock:
                done += 1
                if done % 20 == 0 or done == total:
                    print(f"  pinged {done}/{total} models...", file=sys.stderr)

    return report


def summarize(report: dict) -> str:
    lines = []
    flips_enable: list[str] = []  # disabled but now passing
    flips_disable: list[str] = []  # enabled but dead
    rate_limited: list[str] = []
    add_candidates: list[str] = []
    removed: list[str] = []
    for pid, e in sorted(report["providers"].items()):
        models = e["models"]
        enabled_pass = sum(1 for m in models.values() if m["was_enabled"] and m["ok"])
        enabled_fail = sum(1 for m in models.values() if m["was_enabled"] and not m["ok"])
        disabled_pass = sum(1 for m in models.values() if not m["was_enabled"] and m["ok"])
        lines.append(
            f"{pid:<12} enabled {enabled_pass} ok / {enabled_fail} fail · "
            f"disabled-now-ok {disabled_pass} · "
            f"live {e['live_count']} · new {len([n for n in e['new_models'] if n['likely_chat']])} chat"
        )
        for name, m in models.items():
            tag = f"{pid}/{name}"
            if m["was_enabled"] and not m["ok"]:
                cls = m.get("classification", "?")
                if cls in ("dead",):
                    flips_disable.append(f"{tag}  [{cls} {m['status']}] {m['error'][:80]}")
                elif cls == "rate_limited":
                    rate_limited.append(tag)
                else:
                    flips_disable.append(f"{tag}  [{cls} {m['status']}] {m['error'][:80]}")
            if not m["was_enabled"] and m["ok"]:
                flips_enable.append(tag)
        for n in e["new_models"]:
            if n["likely_chat"]:
                add_candidates.append(f"{pid}/{n['name']}")
        for r in e["removed_models"]:
            # "Not in /models" is only a real removal if the model also fails to
            # answer — some providers serve unlisted models (e.g. free -flash
            # variants). Annotate so the maintainer doesn't drop a working model.
            pr = models.get(r)
            if pr is None:
                note = "?"
            elif pr["ok"]:
                note = "still answers — keep"
            else:
                note = f"confirmed gone [{pr.get('classification', '?')}]"
            removed.append(f"{pid}/{r}  ({note})")

    out = ["", "=== per-provider ===", *lines]
    out += ["", f"=== ENABLED models FAILING ({len(flips_disable)}) ===", *flips_disable]
    out += ["", f"=== DISABLED models NOW PASSING ({len(flips_enable)}) ===", *flips_enable]
    out += [
        "",
        f"=== enabled-but-RATE-LIMITED (keep, transient) ({len(rate_limited)}) ===",
        *rate_limited,
    ]
    out += ["", f"=== NEW chat models in catalog-gap ({len(add_candidates)}) ===", *add_candidates]
    out += ["", f"=== catalog models the provider DROPPED ({len(removed)}) ===", *removed]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", default="/tmp/flp_vet_report.json")
    ap.add_argument("-p", "--providers", help="comma-separated provider ids to limit to")
    ap.add_argument("--enabled-only", action="store_true", help="skip pinging disabled models")
    ap.add_argument("--no-discover", action="store_true", help="skip live /models listing")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument(
        "--per-provider-cap",
        type=int,
        default=3,
        help="max concurrent pings to one provider (avoid 429s)",
    )
    ap.add_argument("--timeout", type=float, default=40.0)
    args = ap.parse_args(argv)

    env = effective_env()
    catalog = load_catalog()
    provs = configured_providers(catalog, env)
    if args.providers:
        want = set(args.providers.split(","))
        provs = [p for p in provs if p.id in want]
    if not provs:
        print(
            "vet_catalog: no configured providers match (set keys / check --providers)",
            file=sys.stderr,
        )
        return 3

    print(f"vetting {len(provs)} providers: {', '.join(p.id for p in provs)}", file=sys.stderr)
    report = audit(
        provs,
        env,
        enabled_only=args.enabled_only,
        discover=not args.no_discover,
        workers=args.workers,
        timeout=args.timeout,
        per_provider_cap=args.per_provider_cap,
    )
    Path(args.report).write_text(json.dumps(report, indent=2))
    print(summarize(report))
    print(f"\nfull report: {args.report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
