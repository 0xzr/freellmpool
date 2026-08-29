from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

PLUGIN_MODULE = Path(__file__).resolve().parents[1] / "llm_freellmpool.py"
STABLE_TARGET = "groq/openai/gpt-oss-20b"


@pytest.fixture()
def plugin(monkeypatch):
    fake_llm = types.ModuleType("llm")

    class Model:
        pass

    class Options:
        pass

    fake_llm.Model = Model
    fake_llm.Options = Options
    fake_llm.hookimpl = lambda function: function
    monkeypatch.setitem(sys.modules, "llm", fake_llm)

    spec = importlib.util.spec_from_file_location("llm_freellmpool_under_test", PLUGIN_MODULE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_register_models_registers_the_expected_non_streaming_model(plugin) -> None:
    registered = []

    plugin.register_models(registered.append)

    assert len(registered) == 1
    model = registered[0]
    assert isinstance(model, plugin.llm.Model)
    assert model.model_id == "freellmpool"
    assert model.can_stream is False
    assert issubclass(model.Options, plugin.llm.Options)


def test_execute_rebuilds_conversation_and_routes_an_explicit_model(plugin, monkeypatch) -> None:
    calls = []

    class FakePool:
        @classmethod
        def from_default_config(cls):
            return cls()

        def chat(self, messages, *, model, providers):
            calls.append({"messages": messages, "model": model, "providers": providers})
            return SimpleNamespace(
                text="mocked answer",
                provider_id="groq",
                model="openai/gpt-oss-20b",
            )

    monkeypatch.setattr(plugin, "Pool", FakePool)
    previous = SimpleNamespace(
        prompt=SimpleNamespace(prompt="Earlier question"),
        text=lambda: "Earlier answer",
    )
    prompt = SimpleNamespace(
        system="Be concise",
        prompt="Current question",
        options=SimpleNamespace(target=STABLE_TARGET),
    )
    response = SimpleNamespace(response_json=None)

    chunks = list(
        plugin.Freellmpool().execute(
            prompt,
            stream=False,
            response=response,
            conversation=SimpleNamespace(responses=[previous]),
        )
    )

    assert chunks == ["mocked answer"]
    assert calls == [
        {
            "messages": [
                {"role": "system", "content": "Be concise"},
                {"role": "user", "content": "Earlier question"},
                {"role": "assistant", "content": "Earlier answer"},
                {"role": "user", "content": "Current question"},
            ],
            "model": "openai/gpt-oss-20b",
            "providers": ["groq"],
        }
    ]
    assert response.response_json == {
        "provider": "groq",
        "model": "openai/gpt-oss-20b",
    }


def test_execute_leaves_auto_routing_unpinned(plugin, monkeypatch) -> None:
    calls = []

    class FakePool:
        @classmethod
        def from_default_config(cls):
            return cls()

        def chat(self, messages, *, model, providers):
            calls.append((messages, model, providers))
            return SimpleNamespace(text="auto answer", provider_id="keyless", model="model")

    monkeypatch.setattr(plugin, "Pool", FakePool)
    prompt = SimpleNamespace(
        system=None,
        prompt="Hello",
        options=SimpleNamespace(target="auto"),
    )

    assert list(
        plugin.Freellmpool().execute(
            prompt,
            stream=False,
            response=SimpleNamespace(response_json=None),
            conversation=None,
        )
    ) == ["auto answer"]
    assert calls == [([{"role": "user", "content": "Hello"}], None, None)]
