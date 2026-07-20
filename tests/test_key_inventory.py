from __future__ import annotations

import os

from freellmpool.key_inventory import (
    KeyRecord,
    append_inventory_record,
    load_inventory,
    records_by_provider,
    redact_secrets,
    upsert_config_key,
)


def test_load_inventory_reads_records(tmp_path):
    path = tmp_path / "keys.toml"
    path.write_text(
        '[[keys]]\n'
        'provider = "groq"\n'
        'env_var = "GROQ_API_KEY"\n'
        'label = "main"\n'
        'created_at = "2026-06-05"\n'
        'commercial_allowed = true\n'
    )
    records = load_inventory(path)
    assert len(records) == 1
    assert records[0].provider == "groq"
    assert records[0].env_var == "GROQ_API_KEY"
    assert records[0].display_label == "main"
    assert records[0].commercial_allowed is True


def test_load_inventory_missing_file_is_empty(tmp_path):
    assert load_inventory(tmp_path / "missing.toml") == []


def test_records_by_provider_groups_records(tmp_path):
    path = tmp_path / "keys.toml"
    path.write_text(
        '[[keys]]\nprovider = "groq"\nenv_var = "GROQ_API_KEY"\n'
        '[[keys]]\nprovider = "groq"\nenv_var = "GROQ_API_KEY_2"\n'
        '[[keys]]\nprovider = "mistral"\nenv_var = "MISTRAL_API_KEY"\n'
    )
    grouped = records_by_provider(load_inventory(path))
    assert len(grouped["groq"]) == 2
    assert len(grouped["mistral"]) == 1


def test_redact_secrets_common_shapes():
    text = "before gsk_abcdefghijk after"
    assert redact_secrets(text) == "before [redacted] after"


def test_upsert_config_key_creates_new_file(tmp_path):
    path = tmp_path / "config.toml"

    upsert_config_key("GROQ_API_KEY", "secret", path)

    assert path.read_text() == '[keys]\nGROQ_API_KEY = "secret"\n'
    if hasattr(os, "fchmod"):
        assert oct(path.stat().st_mode & 0o777) == "0o600"


def test_upsert_config_key_updates_keys_without_touching_other_tables(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        "[settings]\n"
        'default_provider = "groq"\n'
        "\n"
        "[keys]\n"
        'GROQ_API_KEY = "old"\n',
        encoding="utf-8",
    )

    upsert_config_key("GROQ_API_KEY", "new", path)
    upsert_config_key("CEREBRAS_API_KEY", "second", path)

    text = path.read_text()
    assert '[settings]\ndefault_provider = "groq"' in text
    assert 'GROQ_API_KEY = "new"' in text
    assert 'CEREBRAS_API_KEY = "second"' in text
    assert text.count("GROQ_API_KEY") == 1


def test_append_inventory_record_deduplicates_provider_env_pair(tmp_path):
    path = tmp_path / "keys.toml"
    record = KeyRecord(provider="groq", env_var="GROQ_API_KEY", label="main")

    append_inventory_record(record, path)
    append_inventory_record(record, path)

    records = load_inventory(path)
    assert len(records) == 1
    assert records[0].provider == "groq"
    assert records[0].env_var == "GROQ_API_KEY"
    assert records[0].label == "main"


def test_key_record_safe_notes_redacts_secrets():
    notes = "created with sk-abcdefghijk"
    record = KeyRecord(provider="groq", notes=notes)

    assert record.safe_notes() == redact_secrets(notes)
    assert "sk-abcdefghijk" not in record.safe_notes()
