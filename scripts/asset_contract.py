"""Shared contract for generated release-card assets."""

ASSETS = ("demo", "social-preview", "tokenmax-results")
RENDERER_VERSION = "rsvg-convert version 2.58.0"
FONTCONFIG_VERSION = "fontconfig version 2.15.0"
FONT_SOURCES = {
    "DejaVuSans.ttf": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "ae7b7855e115a5966d8b1b3f80f254ccc117ec86f9965e202ee2940453837280",
    ),
    "DejaVuSans-Bold.ttf": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "5c1247acef7f2b8522a31742c76d6adcb5569bacc0be7ceaa4dc39dd252ce895",
    ),
    "DejaVuSansMono.ttf": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "c805f9436dbc268644c1d9584f01a601a653e028e08fd74b9b949f6cf8304d88",
    ),
    "DejaVuSansMono-Bold.ttf": (
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "3a3c502eeff669a231549e80df9f7c49de109bafe303170409e905d0b31a38fe",
    ),
}
FONT_MATCHES = {
    "DejaVu Sans:style=Book": "DejaVuSans.ttf",
    "DejaVu Sans:style=Bold": "DejaVuSans-Bold.ttf",
    "DejaVu Sans Mono:style=Book": "DejaVuSansMono.ttf",
    "DejaVu Sans Mono:style=Bold": "DejaVuSansMono-Bold.ttf",
}
