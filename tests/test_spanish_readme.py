from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

FEATURED_URLS = (
    "https://www.youtube.com/watch?v=1UfIlWoedho",
    "https://www.youtube.com/watch?v=oaM_E92WVGQ",
    "https://mcpmarket.com/server/freellm-pool",
)


def test_readme_featured_section_is_short_and_linked():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    section = readme.split("## Featured in", 1)[1].split("## Contributing", 1)[0]
    lines = [line for line in section.splitlines() if line.strip()]

    assert len(lines) <= 3
    for url in FEATURED_URLS:
        assert url in section


def test_spanish_readme_tracks_current_launch_surface():
    spanish = (ROOT / "README.es.md").read_text(encoding="utf-8")

    assert "Puede quedar por detrás" in spanish
    assert "![demostración de freellmpool tokenmax en terminal](assets/demo.svg)" in spanish
    assert "22 proveedores" in spanish
    assert "177 rutas de chat" in spanish
    assert "431 modelos de chat" in spanish
    assert "Última versión: 0.12.2" in spanish
    assert "main contiene cambios aún no publicados" not in spanish
    assert "0.11.4" not in spanish
    assert "Estado de publicación en npm: pendiente" in spanish
    assert "opencode-freellmpool" in spanish
    assert "opencode-freellmpool-tui" in spanish
    assert "freellmpool/spread" in spanish
    assert "hermes" in spanish
    assert "[plugins/llm-freellmpool](plugins/llm-freellmpool/)" in spanish
    assert "https://github.com/0xzr/llm-freellmpool" not in spanish
    assert "solo en main" not in spanish
    assert "ruta experimental compatible con Anthropic" in spanish
    assert "habla tanto la API de OpenAI como la de Anthropic" not in spanish

    for heading in (
        "## Inicio rápido en 30 segundos",
        "## Ejecuta un agente de código con modelos gratuitos",
        "## Como proxy",
        "## Como biblioteca",
        "## Capacidad y salud de proveedores",
        "## Cómo se compara",
        "## Preguntas frecuentes",
        "## Destacado en",
    ):
        assert heading in spanish

    for url in FEATURED_URLS:
        assert url in spanish
