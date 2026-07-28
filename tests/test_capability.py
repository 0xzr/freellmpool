"""Capability scoring + prompt-difficulty for quality-tiered routing."""

from __future__ import annotations

import json

import pytest

from freellmpool import capability as c


def test_normalize_collapses_variants_and_strips_vendor():
    assert c.normalize_model_name("openai/gpt-oss-120b") == "gpt-oss-120b"
    # packaging/variant suffixes drop, so equivalent names collapse to one key
    assert c.normalize_model_name("llama-3.3-70b-versatile") == c.normalize_model_name(
        "llama-3.3-70b-instruct"
    )
    # redundant leading vendor org token is stripped
    assert c.normalize_model_name("Meta-Llama-3.3-70B-Instruct").startswith("llama-3.3-70b")


@pytest.mark.parametrize(
    ("catalog_name", "benchmark_name"),
    [
        ("cloudflare/@cf/moonshotai/kimi-k2.6", "kimi-k2.6"),
        ("huggingface/MiniMaxAI/MiniMax-M3", "minimax-m3"),
        ("nvidia/minimaxai/minimax-m3", "minimax-m3"),
        ("huggingface/XiaomiMiMo/MiMo-V2.5-Pro", "mimo-v2.5-pro"),
        ("nvidia/z-ai/glm-5.2", "glm-5.2"),
        ("morph/morph-glm52-744b", "glm-5.2"),
        ("morph/morph-minimax3-428b", "minimax-m3"),
        ("morph/morph-dsv4flash", "deepseek-v4-flash"),
        ("cloudflare/@cf/moonshotai/kimi-k2.7-code", "kimi-k2.6"),
        ("huggingface/Qwen/Qwen3.6-27B", "qwen3-30b-a3b"),
        ("huggingface/Qwen/Qwen3.6-35B-A3B", "qwen3-30b-a3b"),
    ],
)
def test_normalize_current_provider_vendor_aliases(catalog_name, benchmark_name):
    assert c.normalize_model_name(catalog_name) == c.normalize_model_name(benchmark_name)


def test_bundled_scores_include_current_artificial_analysis_frontier_models():
    scores = c._read_scores(c._BUNDLED_SCORES)
    assert scores["glm-5.2"] > scores["mimo-v2.5-pro"]
    assert scores["minimax-m3"] == scores["deepseek-v4-pro"]
    assert scores["kimi-k2.6"] == scores["minimax-m3"]


def test_current_agent_models_use_researched_capability_tiers():
    table = c.capability_table()
    assert c.model_capability("@cf/moonshotai/kimi-k2.7-code", table) >= 0.95
    assert c.model_capability("Qwen/Qwen3.6-35B-A3B", table) == c.model_capability(
        "Qwen/Qwen3-30B-A3B", table
    )


def test_model_capability_preserves_direct_synced_score_before_alias():
    table = {
        "qwen3.6-35b-a3b": 0.9041,
        "qwen3-30b-a3b": 0.3536,
    }

    assert c.model_capability("Qwen/Qwen3.6-35B-A3B", table) == 0.9041
    assert c.model_capability(
        "Qwen/Qwen3.6-27B", {"qwen3-30b-a3b": 0.3536}
    ) == 0.3536


def test_heuristic_orders_by_size_and_keywords():
    assert c._heuristic_score("llama-3.1-405b") > c._heuristic_score("llama-3.1-8b-instant")
    assert c._heuristic_score("foo-flash") <= 0.40  # downweight keyword
    assert c._heuristic_score("brand-new-unknown") == 0.5  # neutral when nothing parses


def test_prompt_difficulty_easy_below_hard():
    easy = [{"role": "user", "content": "hi"}]
    hard = [
        {
            "role": "user",
            "content": "Debug and refactor this algorithm:\n```python\ndef f():\n  pass\n```\n"
            "Explain step by step why it is slow.",
        }
    ]
    assert c.prompt_difficulty(easy) < c.prompt_difficulty(hard)
    # tool use bumps difficulty
    assert c.prompt_difficulty(easy, tools=[{"type": "function"}]) > c.prompt_difficulty(easy)


def test_fit_penalty_is_asymmetric():
    # a hard prompt: under-powered model penalized far more than an over-powered one
    assert c.fit_penalty(0.4, 0.9) > c.fit_penalty(0.95, 0.9)
    # an easy prompt: a light model beats an over-powered one (rationing)
    assert c.fit_penalty(0.2, 0.1) < c.fit_penalty(0.95, 0.1)
    # an exact match is free
    assert c.fit_penalty(0.5, 0.5) == 0.0


def test_normalize_scores_percentile_rank():
    scores = c.normalize_scores({"a-1b": 1000.0, "b-7b": 1200.0, "c-70b": 1400.0, "d-405b": 1500.0})
    n = c.normalize_model_name
    # percentile rank preserves order; top model near 1.0, bottom near 0.0
    assert scores[n("a-1b")] < scores[n("b-7b")] < scores[n("c-70b")] < scores[n("d-405b")]
    assert scores[n("d-405b")] > 0.8
    assert scores[n("a-1b")] < 0.2


def test_build_table_precedence_and_relaxed_match():
    catalog = ["qwen-3-235b-a22b-instruct-2507", "llama-3.3-70b-versatile"]
    arena = {"Qwen3-235B-A22B": 1400.0, "Llama-3.3-70B-Instruct": 1300.0, "tiny-1b": 1000.0}
    aa = {"Llama 3.3 70B": 60.0}  # AA must win for llama
    table = c.build_capability_table(aa_scores=aa, arena_scores=arena, catalog_names=catalog)
    # qwen matched despite "qwen3" vs "qwen-3" separator difference (core match)
    assert c.normalize_model_name("qwen-3-235b-a22b-instruct-2507") in table
    # AA takes precedence over Arena for the same model
    llama_key = c.normalize_model_name("llama-3.3-70b-versatile")
    assert table[llama_key]["source"] == "aa"


def test_build_table_precedence_aa_arena_aider():
    # AA > Arena > Aider; a model only Aider has still gets covered, tagged "aider".
    catalog = ["frontier-100b", "only-on-aider-70b"]
    aa = {"frontier-100b": 60.0, "other-a": 10.0}
    arena = {"frontier-100b": 1500.0, "other-b": 1000.0}
    aider = {"only-on-aider-70b": 80.0, "other-c": 5.0}
    table = c.build_capability_table(
        aa_scores=aa, arena_scores=arena, aider_scores=aider, catalog_names=catalog
    )
    assert table[c.normalize_model_name("frontier-100b")]["source"] == "aa"  # AA wins
    assert table[c.normalize_model_name("only-on-aider-70b")]["source"] == "aider"


def test_build_table_family_param_approximation():
    # An uncovered variant borrows the same-family, same-size score, tagged approx.
    catalog = ["llama-3.1-nemotron-nano-8b-v1"]
    arena = {"Llama-3.1-8B-Instruct": 1200.0, "GPT-5": 1500.0}
    table = c.build_capability_table(arena_scores=arena, catalog_names=catalog)
    key = c.normalize_model_name("llama-3.1-nemotron-nano-8b-v1")
    assert key in table
    assert table[key]["source"] == "arena~"  # same family (llama) + size (8b)


def test_direct_match_beats_family_param_approximation():
    # A model with an exact name should never be downgraded to an approximation.
    catalog = ["llama-3.1-8b-instruct"]
    arena = {"Llama-3.1-8B-Instruct": 1200.0}
    table = c.build_capability_table(arena_scores=arena, catalog_names=catalog)
    key = c.normalize_model_name("llama-3.1-8b-instruct")
    assert table[key]["source"] == "arena"  # direct, not "arena~"


def test_model_capability_prefers_table_then_heuristic(tmp_path, monkeypatch):
    cap_file = tmp_path / "cap.json"
    cap_file.write_text(
        json.dumps({"scores": {"my-model": {"score": 0.77, "source": "arena"}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("FREELLMPOOL_CAPABILITY_FILE", str(cap_file))
    c._table_cached.cache_clear()
    assert c.model_capability("my-model") == 0.77
    # a model in neither the table nor the bundle falls back to the heuristic neutral
    assert c.model_capability("totally-unknown-zzz") == 0.5


def test_read_scores_clamps_and_rejects_bad_values(tmp_path):
    f = tmp_path / "cap.json"
    f.write_text(
        json.dumps(
            {
                "scores": {
                    "hi": 999,  # clamps to 1.0
                    "lo": -5,  # clamps to 0.0
                    "ok": {"score": 0.4},
                    "nan": float("nan"),
                    "inf": float("inf"),
                    "str": "abc",
                    "none": {"score": None},
                }
            }
        ),
        encoding="utf-8",
    )
    out = c._read_scores(f)
    assert out["hi"] == 1.0 and out["lo"] == 0.0 and out["ok"] == 0.4
    assert all(0.0 <= v <= 1.0 for v in out.values())
    for bad in ("nan", "inf", "str", "none"):  # non-finite / unparseable dropped
        assert bad not in out


def test_read_scores_rejects_non_dict_payloads(tmp_path):
    for body in ("[1,2,3]", "42", "{not json", '{"scores": [1,2]}'):
        f = tmp_path / "x.json"
        f.write_text(body, encoding="utf-8")
        assert c._read_scores(f) == {}


def test_fetch_aa_scores_refuses_unsafe_url_without_calling_network(monkeypatch):
    import freellmpool.capability as cap

    called = {"n": 0}

    def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("network must not be called for an unsafe AA URL")

    monkeypatch.setattr(cap, "_get_json", boom)
    for bad in (
        "http://artificialanalysis.ai/x",
        "https://evil.example/x",
        "https://evil.artificialanalysis.ai.attacker.com/x",
    ):
        with pytest.raises(ValueError):
            cap.fetch_aa_scores(api_key="secret", url=bad, timeout=1)
    assert called["n"] == 0  # key never sent
