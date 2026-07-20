"""Catalog loading + configured-provider filtering."""

from __future__ import annotations

from pathlib import Path

from freellmpool.config import (
    configured_providers,
    known_aliases,
    load_catalog,
    load_embedders,
    resolve_alias,
)


def test_alias_default_maps_to_auto():
    assert resolve_alias("gpt-4o-mini", {}) == "auto"
    assert resolve_alias("claude-3-5-sonnet-latest", {}) == "auto"


def test_alias_unknown_passthrough():
    assert resolve_alias("groq/llama-3.1-8b-instant", {}) == "groq/llama-3.1-8b-instant"
    assert resolve_alias("auto", {}) == "auto"


def test_alias_env_override():
    env = {"FREELLMPOOL_ALIAS_GPT_4O_MINI": "groq/llama-3.3-70b-versatile"}
    assert resolve_alias("gpt-4o-mini", env) == "groq/llama-3.3-70b-versatile"


def test_known_aliases_include_env_alias():
    env = {"FREELLMPOOL_ALIAS_MY_MODEL": "groq/llama-3.3-70b-versatile"}
    assert "MY_MODEL" in known_aliases(env)


def test_packaged_catalog_loads():
    catalog = load_catalog()
    ids = {p.id for p in catalog}
    assert {"groq", "cerebras", "openrouter", "gemini"} <= ids
    for p in catalog:
        assert p.models  # every provider ships at least one model
        assert p.base_url.startswith("https://")


def test_packaged_catalog_reflects_july_live_model_audit():
    providers = {provider.id: provider for provider in load_catalog()}
    expected_enabled = {
        "cerebras": {"gemma-4-31b"},
        "cloudflare": {"@cf/moonshotai/kimi-k2.7-code"},
        "cohere": {"command-a-translate-08-2025"},
        "huggingface": {
            "CohereLabs/aya-expanse-32b",
            "CohereLabs/aya-vision-32b",
            "CohereLabs/c4ai-command-a-03-2025",
            "CohereLabs/c4ai-command-r-08-2024",
            "CohereLabs/command-a-reasoning-08-2025",
            "CohereLabs/command-a-vision-07-2025",
            "MiniMaxAI/MiniMax-M2.7",
            "MiniMaxAI/MiniMax-M3",
            "Qwen/Qwen3.6-27B",
            "Qwen/Qwen3.6-35B-A3B",
            "XiaomiMiMo/MiMo-V2.5-Pro",
            "google/gemma-4-31B-it",
            "moonshotai/Kimi-K2.7-Code",
            "zai-org/GLM-5.2",
        },
        "kilo": {
            "nvidia/nemotron-3-ultra-550b-a55b:free",
            "poolside/laguna-xs-2.1:free",
            "tencent/hy3:free",
        },
        "nvidia": {"z-ai/glm-5.2"},
        "openrouter": {"poolside/laguna-xs-2.1:free", "tencent/hy3:free"},
    }
    expected_disabled = {
        "cloudflare": {"@cf/meta-llama/llama-2-7b-chat-hf-lora"},
        "llm7": {
            "qwen3-235b",
            "mistral-small-3.2",
            "devstral-small-2:24b",
            "gemma3:27b",
        },
        "longcat": {"LongCat-2.0-Preview"},
        "nvidia": {
            "deepseek-ai/deepseek-v4-pro",
            "meta/llama-3.3-70b-instruct",
            "meta/llama-4-maverick-17b-128e-instruct",
            "microsoft/phi-4-multimodal-instruct",
            "moonshotai/kimi-k2.6",
            "openai/gpt-oss-120b",
        },
        "ollama": {"rnj-1:8b"},
        "openrouter": {
            "liquid/lfm-2.5-1.2b-instruct:free",
            "liquid/lfm-2.5-1.2b-thinking:free",
            "openai/gpt-oss-120b:free",
            "openrouter/owl-alpha",
        },
        "ovh": {"Llama-3.1-8B-Instruct"},
    }

    for provider_id, names in expected_enabled.items():
        models = {model.name: model for model in providers[provider_id].models}
        assert all(models[name].enabled for name in names)
    for provider_id, names in expected_disabled.items():
        models = {model.name: model for model in providers[provider_id].models}
        assert all(not models[name].enabled for name in names)

    for provider_id in ("kilo", "openrouter"):
        assert providers[provider_id].model("poolside/laguna-xs.2:free") is None
    assert providers["nvidia"].model("z-ai/glm-5.1") is None
    for name in ("hy3-free", "mimo-v2.5-free"):
        model = providers["opencode"].model(name)
        assert model is not None
        assert not model.enabled


def test_packaged_catalog_reflects_july_16_provider_refresh():
    providers = {provider.id: provider for provider in load_catalog()}

    expected_models = {
        "aion": {
            "aion-labs/aion-2.0",
            "aion-labs/aion-2.5",
            "aion-labs/aion-3.0",
            "aion-labs/aion-3.0-mini",
            "aion-labs/aion-rp-llama-3.1-8b",
        },
        "modelscope": {
            "deepseek-ai/DeepSeek-V4-Flash",
            "MiniMax/MiniMax-M3",
            "Qwen/Qwen3.5-27B",
            "Qwen/Qwen3.5-35B-A3B",
            "stepfun-ai/Step-3.7-Flash",
            "Tencent-Hunyuan/Hy3",
            "ZhipuAI/GLM-5.2",
        },
        "siliconflow": {"Qwen/Qwen3-8B"},
    }
    for provider_id, names in expected_models.items():
        assert provider_id in providers
        assert names <= {model.name for model in providers[provider_id].models if model.enabled}

    llm7 = providers["llm7"]
    assert llm7.model("gpt-oss:20b") is not None
    assert llm7.model("gpt-oss:20b").enabled
    assert llm7.model("gemma3:27b") is not None
    assert not llm7.model("gemma3:27b").enabled

    ovh = {provider.id: provider for provider in load_embedders()}["ovh"]
    assert ovh.model("Qwen3-Embedding-8B") is not None
    assert ovh.model("Qwen3-Embedding-8B").enabled


def test_packaged_catalog_reflects_july_17_exhaustive_live_audit():
    providers = {provider.id: provider for provider in load_catalog()}

    assert providers["llm7"].model("minimax-m2.7").enabled
    assert providers["kilo"].model("kwaipilot/kat-coder-pro-v2.5:free").enabled
    assert providers["github"].model("openai/gpt-4.1-mini").enabled

    revived_nvidia = {
        "bytedance/seed-oss-36b-instruct",
        "minimaxai/minimax-m2.7",
        "qwen/qwen3-next-80b-a3b-instruct",
        "qwen/qwen3.5-122b-a10b",
        "poolside/laguna-xs-2.1",
        "thinkingmachines/inkling",
    }
    assert all(providers["nvidia"].model(name).enabled for name in revived_nvidia)

    unavailable_nvidia = {
        "google/gemma-3n-e2b-it",
        "google/gemma-3n-e4b-it",
        "microsoft/phi-4-mini-instruct",
        "mistralai/ministral-14b-instruct-2512",
        "mistralai/mixtral-8x7b-instruct-v0.1",
        "qwen/qwen3.5-397b-a17b",
        "stockmark/stockmark-2-100b-instruct",
    }
    assert all(not providers["nvidia"].model(name).enabled for name in unavailable_nvidia)

    retired_ollama = {
        "qwen3-coder:480b",
        "ministral-3:3b",
        "gemma3:4b",
        "gemma3:27b",
        "qwen3-coder-next",
        "minimax-m2.1",
        "devstral-2:123b",
        "devstral-small-2:24b",
        "gemma3:12b",
        "glm-4.7",
        "ministral-3:8b",
        "ministral-3:14b",
    }
    assert all(not providers["ollama"].model(name).enabled for name in retired_ollama)

    removed_github = {
        "meta/llama-3.2-11b-vision-instruct",
        "meta/llama-3.2-90b-vision-instruct",
        "meta/meta-llama-3.1-405b-instruct",
        "meta/meta-llama-3.1-8b-instruct",
    }
    assert all(not providers["github"].model(name).enabled for name in removed_github)

    nvidia_embedders = {provider.id: provider for provider in load_embedders()}["nvidia"]
    removed_nvidia_embedders = {
        "nvidia/embed-qa-4",
        "nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1",
        "nvidia/llama-3.2-nv-embedqa-1b-v1",
        "nvidia/nv-embedqa-mistral-7b-v2",
        "snowflake/arctic-embed-l",
    }
    assert all(not nvidia_embedders.model(name).enabled for name in removed_nvidia_embedders)


def test_packaged_catalog_includes_frontier_free_providers():
    providers = {provider.id: provider for provider in load_catalog()}

    morph = providers["morph"]
    assert morph.base_url == "https://api.morphllm.com/v1"
    assert morph.key_env == "MORPH_API_KEY"
    assert {model.name for model in morph.models if model.enabled} == {
        "morph-glm52-744b",
        "morph-minimax3-428b",
        "morph-dsv4flash",
    }
    assert sum(model.rpd for model in morph.models if model.enabled) <= 6

    vercel = providers["vercel"]
    assert vercel.base_url == "https://ai-gateway.vercel.sh/v1"
    assert vercel.key_env == "AI_GATEWAY_API_KEY"
    assert {model.name for model in vercel.models if model.enabled} == {
        "zai/glm-5.2",
        "minimax/minimax-m3",
        "deepseek/deepseek-v4-pro",
        "moonshotai/kimi-k2.6",
        "xiaomi/mimo-v2.5-pro",
    }
    assert sum(model.rpd for model in vercel.models if model.enabled) <= 5

    modelscope = providers["modelscope"]
    assert all(model.rpd == 200 for model in modelscope.models if model.enabled)


def test_keyless_providers_always_configured():
    # OVH (auth=none) and LLM7 (key_optional) are usable with an empty env.
    catalog = load_catalog()
    ids = {p.id for p in configured_providers(catalog, {})}
    assert "ovh" in ids  # keyless
    assert "llm7" in ids  # key optional
    assert "pollinations" in ids  # keyless
    assert "groq" not in ids  # needs a key

def test_env_example_documents_keyless_providers():
    """Verify .env.example lists all default-enabled keyless/key-optional providers."""
    catalog = load_catalog()
    default_enabled_keyless_ids = {
        p.id for p in catalog if p.keyless and any(model.enabled for model in p.models)
    }
    disabled_keyless_ids = {
        p.id for p in catalog if p.keyless and not any(model.enabled for model in p.models)
    }

    env_content = (Path(__file__).parent.parent / ".env.example").read_text()
    start = env_content.find("# Zero-setup providers")
    end = env_content.find("# So freellmpool works")
    zero_setup_section = env_content[start:end]
    zero_setup_lower = zero_setup_section.lower()

    for provider_id in default_enabled_keyless_ids:
        assert provider_id.lower() in zero_setup_lower, (
            f"Keyless provider '{provider_id}' must be documented in .env.example zero-setup section"
        )
    for provider_id in disabled_keyless_ids:
        assert provider_id.lower() not in zero_setup_lower, (
            f"Disabled keyless provider '{provider_id}' must not be documented as zero-setup"
        )


def test_configured_filter_by_env():
    catalog = load_catalog()
    ids = {p.id for p in configured_providers(catalog, {"GROQ_API_KEY": "x"})}
    assert "groq" in ids
    assert "cerebras" not in ids  # no key → excluded
    assert "ovh" in ids  # keyless → always present


def test_cloudflare_requires_extra_env():
    catalog = load_catalog()
    # token alone is not enough; account id is also required
    with_token = {p.id for p in configured_providers(catalog, {"CLOUDFLARE_API_TOKEN": "t"})}
    assert "cloudflare" not in with_token
    with_both = {
        p.id
        for p in configured_providers(
            catalog, {"CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "acc"}
        )
    }
    assert "cloudflare" in with_both


def test_user_override(tmp_path):
    override = tmp_path / "providers.toml"
    override.write_text(
        "[[provider]]\n"
        'id = "groq"\n'
        'label = "My Groq"\n'
        'adapter = "openai"\n'
        'base_url = "https://example.test/v1"\n'
        'key_env = "GROQ_API_KEY"\n'
        'models = [{ name = "custom-model", rpd = 42 }]\n'
    )
    catalog = load_catalog(path=override)
    groq = next(p for p in catalog if p.id == "groq")
    assert groq.label == "My Groq"
    assert groq.models[0].name == "custom-model"


def test_split_provider_model_guards_against_slash_model_names():
    from freellmpool.config import split_provider_model

    pids = {"groq", "huggingface", "kilo", "openrouter"}
    # real provider prefix → split
    assert split_provider_model("groq/llama-3.1-8b", pids) == (["groq"], "llama-3.1-8b")
    # slash-bearing model on a real provider → only first slash is the provider boundary
    assert split_provider_model("huggingface/Qwen/Qwen3-Coder-30B-A3B-Instruct", pids) == (
        ["huggingface"],
        "Qwen/Qwen3-Coder-30B-A3B-Instruct",
    )
    # bare slash-model (no valid provider prefix) → kept whole, NOT mis-split into "Qwen"
    assert split_provider_model("Qwen/Qwen3-Coder-30B-A3B-Instruct", pids) == (
        None,
        "Qwen/Qwen3-Coder-30B-A3B-Instruct",
    )
    assert split_provider_model("deepseek-ai/DeepSeek-R1", pids) == (None, "deepseek-ai/DeepSeek-R1")
    # no slash, or no provider set → unchanged
    assert split_provider_model("gpt-4o-mini", pids) == (None, "gpt-4o-mini")
    assert split_provider_model("groq/x", None) == (None, "groq/x")
