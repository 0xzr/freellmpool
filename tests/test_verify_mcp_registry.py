from __future__ import annotations

from copy import deepcopy

import pytest

from scripts.verify_mcp_registry import RegistryVerificationError, verify_payload

MANIFEST = {
    "$schema": "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json",
    "name": "io.github.0xzr/freellmpool",
    "title": "freellmpool",
    "description": "A reproducible manifest.",
    "version": "0.12.1",
    "packages": [
        {
            "registryType": "pypi",
            "identifier": "freellmpool",
            "version": "0.12.1",
            "transport": {"type": "stdio"},
        }
    ],
}


def _payload() -> dict:
    return {
        "servers": [
            {
                "server": deepcopy(MANIFEST),
                "_meta": {
                    "io.modelcontextprotocol.registry/official": {
                        "status": "active",
                        "isLatest": True,
                    }
                },
            }
        ],
        "metadata": {"count": 1},
    }


def test_verify_payload_accepts_exact_active_latest_manifest():
    verify_payload(MANIFEST, _payload())


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda payload: payload.update(servers=[]), "exactly one"),
        (
            lambda payload: payload["servers"][0]["server"].update(
                description="registry drift"
            ),
            "does not exactly match",
        ),
        (
            lambda payload: payload["servers"][0]["_meta"][
                "io.modelcontextprotocol.registry/official"
            ].update(status="deprecated"),
            "not active",
        ),
        (
            lambda payload: payload["servers"][0]["_meta"][
                "io.modelcontextprotocol.registry/official"
            ].update(isLatest=False),
            "not marked latest",
        ),
    ],
)
def test_verify_payload_rejects_nonreproducible_registry_state(mutate, message):
    payload = _payload()
    mutate(payload)

    with pytest.raises(RegistryVerificationError, match=message):
        verify_payload(MANIFEST, payload)
