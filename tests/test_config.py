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


def test_kimi_k27_catalog_entries_declare_verified_context_window():
    providers = {provider.id: provider for provider in load_catalog()}
    assert providers["cloudflare"].model("@cf/moonshotai/kimi-k2.7-code").context == 262_144
    assert providers["huggingface"].model("moonshotai/Kimi-K2.7-Code").context == 262_144


def test_cloudflare_catalog_matches_current_free_billing_and_lifecycle():
    cloudflare = next(provider for provider in load_catalog() if provider.id == "cloudflare")

    assert cloudflare.model("@cf/qwen/qwen3.8-27b").enabled
    assert cloudflare.model("@cf/qwen/qwen3.8-27b").context == 262_144
    assert not cloudflare.model("@cf/moonshotai/kimi-k2.6").enabled
    assert not cloudflare.model("@cf/moonshotai/kimi-k2.7-code").enabled
    assert not cloudflare.model("@cf/meta/llama-3.1-70b-instruct").enabled


def test_packaged_catalog_reflects_july_live_model_audit():
    providers = {provider.id: provider for provider in load_catalog()}
    expected_enabled = {
        "cerebras": {"gemma-4-31b"},
        "cloudflare": {"@cf/qwen/qwen3.8-27b"},
        "cohere": {"command-a-translate-08-2025"},
        "huggingface": {
            "CohereLabs/aya-expanse-32b",
            "CohereLabs/aya-vision-32b",
            "CohereLabs/c4ai-command-a-03-2025",
            "CohereLabs/c4ai-command-r-08-2024",
            "CohereLabs/command-a-reasoning-08-2025",
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
        },
        "openrouter": {"poolside/laguna-xs-2.1:free"},
    }
    expected_disabled = {
        "cloudflare": {"@cf/meta-llama/llama-2-7b-chat-hf-lora"},
        "llm7": {
            "qwen3-235b",
            "mistral-small-3.2",
            "devstral-small-2:24b",
            "gemma3:27b",
        },
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
            "aion-labs/aion-3.0",
            "aion-labs/aion-3.0-mini",
            "aion-labs/aion-rp-llama-3.1-8b",
        },
        "modelscope": {
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
    assert not llm7.model("gpt-oss:20b").enabled
    assert llm7.model("gemma3:27b") is not None
    assert not llm7.model("gemma3:27b").enabled

    ovh = {provider.id: provider for provider in load_embedders()}["ovh"]
    assert ovh.model("Qwen3-Embedding-8B") is not None
    assert ovh.model("Qwen3-Embedding-8B").enabled


def test_packaged_catalog_reflects_july_17_exhaustive_live_audit():
    providers = {provider.id: provider for provider in load_catalog()}

    assert not providers["llm7"].model("minimax-m2.7").enabled
    assert not providers["kilo"].model("kwaipilot/kat-coder-pro-v2.5:free").enabled

    revived_nvidia = {
        "poolside/laguna-xs-2.1",
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

    nvidia_embedders = {provider.id: provider for provider in load_embedders()}["nvidia"]
    removed_nvidia_embedders = {
        "nvidia/embed-qa-4",
        "nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1",
        "nvidia/llama-3.2-nv-embedqa-1b-v1",
        "nvidia/nv-embedqa-mistral-7b-v2",
        "snowflake/arctic-embed-l",
    }
    assert all(not nvidia_embedders.model(name).enabled for name in removed_nvidia_embedders)


def test_packaged_catalog_omits_retired_github_models():
    assert "github" not in {provider.id for provider in load_catalog()}
    assert "github" not in {embedder.id for embedder in load_embedders()}


def test_packaged_catalog_retires_gemini_2_and_uses_live_verified_llm7_selectors():
    providers = {provider.id: provider for provider in load_catalog()}

    gemini = providers["gemini"]
    assert not gemini.model("gemini-2.0-flash").enabled
    assert not gemini.model("gemini-2.0-flash-lite").enabled
    assert gemini.model("gemini-2.5-flash").enabled
    current_unverified = {
        "gemini-3.1-flash-lite",
        "gemini-3.5-flash",
        "gemini-3.5-flash-lite",
        "gemini-3.6-flash",
        "gemini-3.7-flash",
    }
    assert all(gemini.model(name) is not None for name in current_unverified)
    assert all(not gemini.model(name).enabled for name in current_unverified)

    llm7 = providers["llm7"]
    assert llm7.model("default").enabled
    assert llm7.model("fast").enabled
    assert llm7.model("pro") is None


def test_packaged_catalog_reflects_august_29_upstream_reconciliation():
    providers = {provider.id: provider for provider in load_catalog()}

    assert "longcat" not in providers

    def assert_disabled_pins(provider_id: str, names: set[str]) -> None:
        provider = providers[provider_id]
        for name in names:
            model = provider.model(name)
            assert model is not None, f"missing {provider_id}/{name}"
            assert not model.enabled, f"unexpectedly enabled {provider_id}/{name}"
            assert not model.auto, f"unexpectedly automatic {provider_id}/{name}"

    assert_disabled_pins("llm7", {"gpt-oss:20b"})
    assert_disabled_pins("aion", {"aion-labs/aion-2.5"})
    assert_disabled_pins(
        "modelscope",
        {
            "deepseek-ai/DeepSeek-V4-Flash",
            "deepseek-ai/DeepSeek-V4-Flash-0731",
        },
    )
    assert_disabled_pins(
        "morph",
        {
            "morph-glm52-744b",
            "morph-minimax3-428b",
            "morph-glm53-744b",
            "morph-glm53flash",
            "morph-dsv4flash",
            "morph-kimik3",
        },
    )

    nvidia_retired = {
        "deepseek-ai/deepseek-v4-flash",
        "meta/llama-3.1-70b-instruct",
        "meta/llama-3.1-8b-instruct",
        "meta/llama-3.2-3b-instruct",
        "mistralai/mistral-medium-3.5-128b",
        "nvidia/llama-3.1-nemotron-nano-vl-8b-v1",
        "nvidia/llama-3.3-nemotron-super-49b-v1",
        "nvidia/llama-3.3-nemotron-super-49b-v1.5",
        "nvidia/nemotron-nano-12b-v2-vl",
        "nvidia/nvidia-nemotron-nano-9b-v2",
        "stepfun-ai/step-3.7-flash",
        "thinkingmachines/inkling",
        "z-ai/glm-5.2",
    }
    nvidia_candidates = {
        "deepseek-ai/deepseek-v4-flash-0731",
        "deepseek-ai/deepseek-v4-pro-0813",
        "meta/muse-glimmer-30b",
        "moonshotai/kimi-k3",
        "nvidia/nemotron-3.5-lightning-30b-a3b",
    }
    assert_disabled_pins("nvidia", nvidia_retired | nvidia_candidates)

    openrouter_retired = {
        "nvidia/nemotron-3-nano-30b-a3b:free",
        "nvidia/nemotron-nano-12b-v2-vl:free",
        "nvidia/nemotron-nano-9b-v2:free",
        "openai/gpt-oss-20b:free",
    }
    openrouter_candidates = {
        "dots-studio/dots-3-note-preview:free",
        "inclusionai/ling-3.0-flash-fin:free",
        "liquid/lfm-2.5-2.6b:free",
        "minimax/minimax-m2.7:free",
        "minimax/minimax-m3:free",
        "nvidia/nemotron-3.5-lightning:free",
        "poolside/laguna-s-2.1:free",
        "thinkingmachines/inkling-small:free",
        "thinkingmachines/inkling:free",
        "z-ai/glm-5.2:free",
    }
    assert_disabled_pins("openrouter", openrouter_retired | openrouter_candidates)

    assert_disabled_pins(
        "opencode",
        {
            "laguna-s-2.1-free",
            "ling-3.0-flash-fin-free",
            "muse-spark-1.2-contributor-free",
            "nemotron-3.5-lightning-free",
        },
    )

    kilo = providers["kilo"]
    kilo_verified = {
        "dots-studio/dots-3-note-preview:free",
        "inclusionai/ling-3.0-flash-fin:free",
        "liquid/lfm-2.5-2.6b:free",
        "meituan/longcat-2.0-free",
        "minimax/minimax-m2.7:free",
        "nvidia/nemotron-3.5-lightning:free",
        "poolside/laguna-s-2.1:free",
        "tencent/hy3:free",
    }
    assert all(
        kilo.model(name) and kilo.model(name).enabled and kilo.model(name).auto
        for name in kilo_verified
    )
    assert_disabled_pins(
        "kilo",
        {
            "minimax/minimax-m3:free",
            "thinkingmachines/inkling-small:free",
            "thinkingmachines/inkling:free",
        },
    )

    cerebras = providers["cerebras"]
    for name in {"gpt-oss-120b", "gemma-4-31b"}:
        model = cerebras.model(name)
        assert model is not None and model.enabled and not model.auto and model.rpd == 0
    assert_disabled_pins(
        "cerebras",
        {"zai-glm-4.7", "qwen-3-235b-a22b-instruct-2507", "llama3.1-8b"},
    )
    assert all(
        cerebras.model(name).rpd == 0
        for name in {"zai-glm-4.7", "qwen-3-235b-a22b-instruct-2507", "llama3.1-8b"}
    )

    mistral_retired = {
        "open-mistral-nemo",
        "open-mistral-nemo-2407",
        "mistral-tiny-2407",
        "mistral-tiny-latest",
        "mistral-medium",
        "mistral-medium-3.5",
        "mistral-medium-2604",
        "mistral-medium-c21211-r0-75",
        "labs-leanstral-2603",
    }
    assert_disabled_pins("mistral", mistral_retired | {"zai-glm-5-2", "labs-leanstral-1-5"})
    mistral_pin_only = {
        "mistral-medium-2505",
        "mistral-medium-2508",
        "devstral-2512",
        "devstral-latest",
        "magistral-medium-2509",
        "magistral-medium-latest",
        "magistral-small-2509",
        "magistral-small-latest",
        "mistral-small-2506",
        "mistral-vibe-cli-with-tools",
        "mistral-vibe-cli-fast",
        "mistral-vibe-cli-latest",
        "mistral-code-latest",
        "mistral-code-fim-latest",
        "mistral-code-agent-latest",
        "devstral-medium-latest",
    }
    for name in mistral_pin_only:
        model = providers["mistral"].model(name)
        assert model is not None and model.enabled and not model.auto

    assert_disabled_pins(
        "ollama",
        {
            "minimax-m2.5",
            "deepseek-v4-flash:0731",
            "deepseek-v4-pro:0813",
            "glm-5.2",
            "glm-5.3",
            "glm-5.3-flash",
            "kimi-k2.7-code",
            "kimi-k3",
        },
    )
    assert_disabled_pins(
        "gemini",
        {
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-3.5-flash-lite",
            "gemini-3.1-flash-lite",
        },
    )
    assert_disabled_pins(
        "siliconflow",
        {
            "Qwen/Qwen3.5-4B",
            "Qwen/Qwen2.5-7B-Instruct",
            "Qwen/Qwen2.5-Coder-7B-Instruct",
            "THUDM/GLM-4-9B-0414",
            "internlm/internlm2_5-7b-chat",
            "THUDM/glm-4-9b-chat",
        },
    )
    assert_disabled_pins("sambanova", {"MiniMax-M3"})

    zhipu = providers["zhipu"]
    assert zhipu.model("glm-4.7-flash").enabled and zhipu.model("glm-4.7-flash").auto
    assert zhipu.model("glm-4.5-flash").enabled and not zhipu.model("glm-4.5-flash").auto


def test_nvidia_embedding_catalog_reflects_august_29_listing():
    nvidia = {provider.id: provider for provider in load_embedders()}["nvidia"]
    disabled_pins = {
        "baai/bge-m3",
        "nvidia/llama-nemotron-embed-1b-v2",
        "nvidia/nv-embed-v1",
        "nvidia/nv-embedcode-7b-v1",
        "nvidia/nv-embedqa-e5-v5",
        "nvidia/embed-qa-4",
        "nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1",
        "nvidia/llama-3.2-nv-embedqa-1b-v1",
        "nvidia/nemotron-3-embed-1b",
        "nvidia/nv-embedqa-mistral-7b-v2",
        "snowflake/arctic-embed-l",
    }
    for name in disabled_pins:
        model = nvidia.model(name)
        assert model is not None, f"missing nvidia embedder {name}"
        assert not model.enabled and not model.auto
    assert nvidia.model("nvidia/llama-nemotron-embed-vl-1b-v2").enabled


def test_packaged_catalog_disables_july_29_repeat_definitive_failures():
    providers = {provider.id: provider for provider in load_catalog()}
    retired = {
        "groq": {
            "meta-llama/llama-4-scout-17b-16e-instruct",
            "qwen/qwen3-32b",
        },
        "huggingface": {"CohereLabs/command-a-vision-07-2025"},
        "kilo": {
            "kwaipilot/kat-coder-pro-v2.5:free",
            "poolside/laguna-m.1:free",
        },
        "llm7": {"minimax-m2.7"},
        "nvidia": {
            "abacusai/dracarys-llama-3.1-70b-instruct",
            "bytedance/seed-oss-36b-instruct",
            "minimaxai/minimax-m2.7",
            "mistralai/mistral-large-3-675b-instruct-2512",
            "mistralai/mistral-small-4-119b-2603",
            "qwen/qwen3-next-80b-a3b-instruct",
            "qwen/qwen3.5-122b-a10b",
            "sarvamai/sarvam-m",
            "stepfun-ai/step-3.5-flash",
            "upstage/solar-10.7b-instruct",
        },
        "openrouter": {
            "cognitivecomputations/dolphin-mistral-24b-venice-edition:free",
            "meta-llama/llama-3.2-3b-instruct:free",
            "meta-llama/llama-3.3-70b-instruct:free",
            "nousresearch/hermes-3-llama-3.1-405b:free",
            "poolside/laguna-m.1:free",
            "qwen/qwen3-coder:free",
            "qwen/qwen3-next-80b-a3b-instruct:free",
            "tencent/hy3:free",
        },
    }

    for provider_id, model_names in retired.items():
        provider = providers[provider_id]
        assert all(not provider.model(name).enabled for name in model_names)


def test_groq_catalog_matches_august_29_free_plan_and_retirements():
    groq = next(provider for provider in load_catalog() if provider.id == "groq")
    models = {model.name: model for model in groq.models}

    for retired in (
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "allam-2-7b",
    ):
        assert models[retired].enabled is False
        assert models[retired].auto is False

    assert models["groq/compound"].rpd == 250
    assert models["groq/compound-mini"].rpd == 250
    assert models["qwen/qwen3.8-27b"].enabled is True
    assert models["qwen/qwen3.8-27b"].auto is False
    assert models["qwen/qwen3.8-27b"].context == 131_042


def test_packaged_catalog_includes_frontier_free_providers():
    providers = {provider.id: provider for provider in load_catalog()}

    morph = providers["morph"]
    assert morph.base_url == "https://api.morphllm.com/v1"
    assert morph.key_env == "MORPH_API_KEY"
    assert {model.name for model in morph.models if model.enabled} == set()
    assert all(not model.auto for model in morph.models)

    vercel = providers["vercel"]
    assert vercel.base_url == "https://ai-gateway.vercel.sh/v1"
    assert vercel.key_env == "AI_GATEWAY_API_KEY"
    assert vercel.extra_env == ()
    assert vercel.is_configured({"AI_GATEWAY_API_KEY": "free-tier-key"})
    assert {model.name for model in vercel.models if model.enabled and model.auto} == {
        "poolside/laguna-s-2.1-free",
    }
    assert {model.name for model in vercel.models if model.enabled and not model.auto} == {
        "nvidia/nemotron-3.5-lightning",
        "deepseek/deepseek-v4-flash-0731",
        "zai/glm-5.2",
        "minimax/minimax-m3",
        "deepseek/deepseek-v4-pro",
        "moonshotai/kimi-k2.6",
        "xiaomi/mimo-v2.5-pro",
    }
    assert vercel.model("nvidia/nemotron-3.5-lightning-free").enabled is False
    assert vercel.model("deepseek/deepseek-v4-flash-0731").rpd == 50
    for pending_free in (
        "inclusionai/ling-3.0-flash-fin",
        "inclusionai/ling-3.0-flash-fin-free",
        "minimax/minimax-m2.7-free",
        "minimax/minimax-m3-free",
    ):
        assert vercel.model(pending_free).enabled is False
        assert vercel.model(pending_free).auto is False

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


def test_pollinations_catalog_matches_live_chat_selectors():
    pollinations = next(provider for provider in load_catalog() if provider.id == "pollinations")
    models = {model.name: model for model in pollinations.models}

    assert set(models) == {
        "openai",
        "openai-fast",
        "gpt-oss",
        "gpt-oss-20b",
        "ovh-reasoning",
    }
    assert models["gpt-oss"].enabled is True
    assert models["gpt-oss-20b"].enabled is False
    assert models["ovh-reasoning"].enabled is False
    assert models["openai-fast"].auto is True
    assert models["openai"].auto is False
    assert models["gpt-oss"].auto is False
    assert models["gpt-oss-20b"].auto is False
    assert models["ovh-reasoning"].auto is False


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


def test_custom_provider_rejects_unsafe_environment_variable_names():
    from freellmpool.config import _parse_rows

    def row(provider_id, **fields):
        return {
            "id": provider_id,
            "base_url": "https://provider.example/v1",
            "models": [{"name": "model"}],
            **fields,
        }

    providers = _parse_rows(
        [
            row("valid", key_env="PROVIDER_KEY", extra_env=["ACCOUNT_ID"]),
            row("newline", key_env="BAD\nKEY"),
            row("space", extra_env=["BAD NAME"]),
            row("scalar-extra", extra_env="ACCOUNT_ID"),
        ]
    )

    assert [provider.id for provider in providers] == ["valid"]
    assert providers[0].key_env == "PROVIDER_KEY"
    assert providers[0].extra_env == ("ACCOUNT_ID",)


def test_catalog_toml_is_parsed_once_across_surfaces(tmp_path, monkeypatch):
    import freellmpool.config as config_module

    path = tmp_path / "all.toml"
    path.write_text(
        "[[provider]]\n"
        'id = "chat"\nbase_url = "https://chat.test/v1"\n'
        'models = [{ name = "chat-model" }]\n'
        "[[embedder]]\n"
        'id = "embed"\nbase_url = "https://embed.test/v1"\n'
        'models = [{ name = "embed-model" }]\n'
        "[[transcriber]]\n"
        'id = "audio"\nbase_url = "https://audio.test/v1"\n'
        'models = [{ name = "audio-model" }]\n'
    )
    original = config_module.tomllib.load
    calls = 0

    def counted(handle):
        nonlocal calls
        calls += 1
        return original(handle)

    monkeypatch.setattr(config_module.tomllib, "load", counted)

    assert load_catalog(path)[0].id == "chat"
    assert load_embedders(path)[0].id == "embed"
    assert config_module.load_transcribers(path)[0].id == "audio"
    assert calls == 1

    first = load_catalog(path)
    first.clear()
    assert load_catalog(path)[0].id == "chat"


def test_local_catalog_marker_only_accepts_canonical_literal_loopback(monkeypatch):
    from freellmpool.config import _parse_rows

    monkeypatch.delenv("FREELLMPOOL_ALLOW_LOCAL_PROVIDERS", raising=False)

    def row(provider_id, base_url):
        return {
            "id": provider_id,
            "base_url": base_url,
            "local": True,
            "models": [{"name": "model"}],
        }

    parsed = _parse_rows(
        [
            row("v4", "http://127.0.0.2:11434/v1"),
            row("v6", "http://[::1]:1234/v1"),
            row("lan", "http://192.168.1.2:11434/v1"),
            row("localhost", "http://localhost:11434/v1"),
            row("short", "http://127.1:11434/v1"),
            row("mapped", "http://[::ffff:127.0.0.1]:11434/v1"),
            row("public", "https://api.example.test/v1"),
        ]
    )

    assert [provider.id for provider in parsed] == ["v4", "v6"]


def test_catalog_cache_invalidates_on_local_opt_in_and_same_size_replace(
    tmp_path, monkeypatch
):
    import os

    path = tmp_path / "providers.toml"
    first = (
        "[[provider]]\n"
        'id = "local"\nbase_url = "http://192.168.1.2:1234/v1"\n'
        'models = [{ name = "model-a" }]\n'
    )
    second = first.replace("model-a", "model-b")
    path.write_text(first)
    original_stat = path.stat()

    monkeypatch.delenv("FREELLMPOOL_ALLOW_LOCAL_PROVIDERS", raising=False)
    assert load_catalog(path) == []
    monkeypatch.setenv("FREELLMPOOL_ALLOW_LOCAL_PROVIDERS", "1")
    assert load_catalog(path)[0].models[0].name == "model-a"

    path.write_text(second)
    os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    assert load_catalog(path)[0].models[0].name == "model-b"
