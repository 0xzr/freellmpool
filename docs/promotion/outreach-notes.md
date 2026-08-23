# Outreach notes

Use these for low-volume, targeted outreach. Do not spam. Send only to people who
have already covered free LLM tooling, MCP servers, coding agents, or local AI
developer workflows.

## Short creator note

Subject: Open-source free LLM tier pool for coding agents

Hi <name>,

You have covered free LLM APIs / coding agents / MCP tools before, so I thought
this might be relevant.

I built `freellmpool`, an MIT-licensed Python tool that pools free LLM provider
tiers behind one local CLI/proxy/MCP server. It supports keyless start when
default keyless routes are available and can use optional user-owned free-tier
keys for more models and capacity.

Repo: https://github.com/0xzr/freellmpool
Docs: https://0xzr.github.io/freellmpool/

Current catalog: 24 cataloged providers, 223 enabled chat routes, 407 cataloged chat
models.

The honest caveat: it is not a privacy layer. Prompts go to the selected
provider. The FAQ covers that explicitly:
https://github.com/0xzr/freellmpool/blob/main/FAQ.md

If it is useful for your audience, I would be glad to answer questions or fix
provider catalog drift you run into.

## Spanish creator note

Subject: Herramienta open-source para usar free tiers de LLMs desde una sola API

Hola <name>,

Vi que has cubierto herramientas de IA y APIs gratuitas. Publiqué
`freellmpool`, una herramienta MIT/open-source en Python para agrupar free tiers
de proveedores LLM detrás de una sola interfaz local.

Repo: https://github.com/0xzr/freellmpool
Docs: https://0xzr.github.io/freellmpool/

Funciona como CLI, proxy local compatible con OpenAI, librería de Python y
servidor MCP. Puede responder sin API keys cuando hay una ruta keyless
disponible, y también permite sumar
keys gratuitas propias de Groq, Cerebras, Gemini, Mistral, OpenRouter, NVIDIA,
etc.

Catálogo actual: 24 proveedores, 223 rutas chat habilitadas, 407 modelos chat
catalogados.

Nota importante: no es una capa de privacidad; los prompts van al proveedor
seleccionado. Lo explico en el FAQ:
https://github.com/0xzr/freellmpool/blob/main/FAQ.md

Si te sirve para un video o post, encantado de responder preguntas.

## Newsletter pitch

freellmpool is a local MIT-licensed gateway for pooling free LLM provider tiers.
It exposes a CLI, Python library, OpenAI-compatible local proxy, experimental
Anthropic-compatible path, and MCP server. It supports keyless start when
default keyless routes are available, optional user-owned free-tier keys, and
currently catalogs 24 cataloged providers with 223 enabled chat routes. Useful for
coding-agent side tasks, docs, triage, and scripts where free-tier models are
good enough.

GitHub: https://github.com/0xzr/freellmpool
FAQ / prompt destination: https://github.com/0xzr/freellmpool/blob/main/FAQ.md
