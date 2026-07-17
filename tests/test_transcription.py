"""Pooled audio transcription: catalog, client.transcribe, Pool.transcribe, failover."""

from __future__ import annotations

import pytest

from freellmpool import client as C
from freellmpool.config import configured_transcribers, load_transcribers
from freellmpool.errors import AllProvidersExhausted, NoProvidersConfigured
from freellmpool.models import Model, Provider
from freellmpool.router import Pool


def _transcriber(tid: str, key_env: str) -> Provider:
    return Provider(
        id=tid,
        label=tid,
        adapter="openai",
        base_url=f"https://{tid}.test/v1",
        key_env=key_env,
        models=(Model("whisper-large-v3-turbo"),),
    )


def test_transcriber_catalog_loads():
    cat = load_transcribers()
    ids = [t.id for t in cat]
    assert "groq" in ids and "mistral" in ids
    # Catalog order IS failover/`auto` order (Pool.transcribe iterates the list). Quality-first
    # per the WER smoke test: Mistral Voxtral (most accurate) precedes Groq Whisper.
    assert ids.index("mistral") < ids.index("groq")
    # Keyless OVHcloud Whisper is LAST = the always-available fallback after keyed providers.
    assert ids[-1] == "ovh"
    # Within Groq, whisper-large-v3 precedes -turbo (quality-first).
    groq = next(t for t in cat if t.id == "groq")
    gm = [m.name for m in groq.models]
    assert gm.index("whisper-large-v3") < gm.index("whisper-large-v3-turbo")
    for t in cat:
        assert t.base_url.startswith("https://")
        assert t.models


def test_configured_transcribers_filter():
    cat = load_transcribers()
    got = {t.id for t in configured_transcribers(cat, {"GROQ_API_KEY": "x"})}
    assert got == {"groq", "ovh"}  # groq (keyed) + ovh (keyless); mistral needs its key
    both = {
        t.id for t in configured_transcribers(cat, {"GROQ_API_KEY": "x", "MISTRAL_API_KEY": "y"})
    }
    assert both == {"groq", "mistral", "ovh"}
    # keyless-only: with no keys, only the keyless OVH transcriber is configured.
    assert {t.id for t in configured_transcribers(cat, {})} == {"ovh"}


def test_client_transcribe_shape():
    def post(url, headers, files, data, timeout):
        assert url.endswith("/audio/transcriptions")
        assert files["file"][0] == "a.wav"
        assert files["file"][1] == b"AUDIO"
        assert data["model"] == "whisper-large-v3-turbo"
        assert headers["Authorization"] == "Bearer k"
        return C.HTTPResult(200, {"text": "hello world"}, "")

    t = _transcriber("groq", "GROQ_API_KEY")
    reply = C.transcribe(
        t, "whisper-large-v3-turbo", b"AUDIO", "a.wav", api_key="k", env={}, post=post
    )
    assert reply.text == "hello world"
    assert reply.provider_id == "groq"


def test_client_transcribe_text_format_plain_body():
    def post(url, headers, files, data, timeout):
        # response_format=text → transport returns plain text body
        return C.HTTPResult(200, {"text": "plain transcript"}, "plain transcript")

    t = _transcriber("groq", "GROQ_API_KEY")
    reply = C.transcribe(
        t,
        "whisper-large-v3-turbo",
        b"A",
        "a.wav",
        api_key="k",
        env={},
        response_format="text",
        post=post,
    )
    assert reply.text == "plain transcript"


def test_client_transcribe_empty_text_is_success_not_failover():
    # Silent audio → {"text": ""} on HTTP 200 is a VALID (empty) transcription, not an error.
    # It must return "" rather than raise (which would force needless failover/exhaustion).
    def post(url, headers, files, data, timeout):
        return C.HTTPResult(200, {"text": ""}, "")

    t = _transcriber("groq", "GROQ_API_KEY")
    reply = C.transcribe(t, "whisper-large-v3", b"AUDIO", "a.wav", api_key="k", env={}, post=post)
    assert reply.text == ""
    assert reply.provider_id == "groq"


def test_client_transcribe_no_text_field_raises():
    # A 200 JSON dict with no text field is malformed → retryable error (drives failover),
    # even when the raw response body text is non-empty (must NOT pass raw JSON as a transcript).
    from freellmpool.errors import ProviderHTTPError

    def post(url, headers, files, data, timeout):
        return C.HTTPResult(200, {"unexpected": "shape"}, '{"unexpected":"shape"}')

    t = _transcriber("groq", "GROQ_API_KEY")
    with pytest.raises(ProviderHTTPError):
        C.transcribe(t, "whisper-large-v3", b"AUDIO", "a.wav", api_key="k", env={}, post=post)


def test_pool_transcribe_failover():
    def post(url, headers, files, data, timeout):
        if "alpha.test" in url:
            return C.HTTPResult(429, {"error": {"message": "rl"}}, "")
        return C.HTTPResult(200, {"text": "ok"}, "")

    transcribers = [_transcriber("alpha", "A_KEY"), _transcriber("beta", "B_KEY")]
    pool = Pool(
        [], env={"A_KEY": "a", "B_KEY": "b"}, transcribers=transcribers, transcribe_post=post
    )
    reply = pool.transcribe(b"AUDIO", "a.wav")
    assert reply.provider_id == "beta"  # alpha 429 → failover
    assert reply.text == "ok"


def test_pool_transcribe_skips_disabled_models():
    seen = []

    def post(url, headers, files, data, timeout):
        seen.append(data["model"])
        return C.HTTPResult(200, {"text": "ok"}, "")

    tr = _transcriber("alpha", "A_KEY")
    tr = Provider(**{**tr.__dict__, "models": (Model("retired", enabled=False), Model("working"))})
    reply = Pool(
        [], env={"A_KEY": "a"}, transcribers=[tr], transcribe_post=post
    ).transcribe(b"AUDIO", "a.wav")
    assert reply.model == "working"
    assert seen == ["working"]


def test_pool_transcribe_provider_pin():
    def post(url, headers, files, data, timeout):
        return C.HTTPResult(200, {"text": url}, "")

    transcribers = [_transcriber("alpha", "A_KEY"), _transcriber("beta", "B_KEY")]
    pool = Pool(
        [], env={"A_KEY": "a", "B_KEY": "b"}, transcribers=transcribers, transcribe_post=post
    )
    reply = pool.transcribe(b"AUDIO", "a.wav", providers=["beta"])
    assert reply.provider_id == "beta"
    assert "beta.test" in reply.text


def test_pool_transcribe_no_transcribers_raises():
    pool = Pool([], env={}, transcribers=[])
    with pytest.raises(NoProvidersConfigured):
        pool.transcribe(b"AUDIO", "a.wav")


def test_pool_transcribe_unknown_pin_raises_no_providers():
    # pinning a provider/model that matches nothing must be NoProvidersConfigured (→ 503),
    # not AllProvidersExhausted([]) (→ 502).
    def post(url, headers, files, data, timeout):
        return C.HTTPResult(200, {"text": "ok"}, "")

    pool = Pool(
        [], env={"A_KEY": "a"}, transcribers=[_transcriber("alpha", "A_KEY")], transcribe_post=post
    )
    with pytest.raises(NoProvidersConfigured):
        pool.transcribe(b"AUDIO", "a.wav", providers=["nonexistent"])
    with pytest.raises(NoProvidersConfigured):
        pool.transcribe(b"AUDIO", "a.wav", model="no-such-model")


def test_pool_transcribe_all_fail_raises():
    def post(url, headers, files, data, timeout):
        return C.HTTPResult(500, {}, "")

    pool = Pool(
        [], env={"A_KEY": "a"}, transcribers=[_transcriber("alpha", "A_KEY")], transcribe_post=post
    )
    with pytest.raises(AllProvidersExhausted):
        pool.transcribe(b"AUDIO", "a.wav")
