# Getting your free API keys (step by step)

`freellmpool` is only as good as the free tiers you plug into it. The good news:
**every provider below is free and none require a credit card.** You don't need
all of them — even **one** key gets you going. Start with Groq + Cerebras (the
two fastest, most generous, and quickest to sign up for), then add more later.

> **No keys at all?** freellmpool still works: **Pollinations**, **OVHcloud**,
> and **Kilo Gateway** are keyless (anonymous), and **LLM7** works without a key. So
> `freellmpool ask "hi"` runs the moment you install. The keys below just add more
> models, higher limits, and better failover.

Each key takes about a minute. Once you have one, either `export` it in your
shell or put it in a `.env` file (copy [`.env.example`](../.env.example)).

> Tip: run `freellmpool providers` at any time to see which keys are detected.

---

## Start here (fastest to sign up for)

### Groq — *~1 min, no card*
1. Go to <https://console.groq.com/keys> and sign in with Google/GitHub.
2. Click **Create API Key**, name it anything, copy the value (`gsk_...`).
3. `export GROQ_API_KEY=gsk_...`

   The same key also powers free **audio transcription** (Whisper) via
   `/v1/audio/transcriptions`.

### Cerebras — *~1 min, no card*
1. Go to <https://cloud.cerebras.ai> and sign in.
2. Open **API Keys** → **Generate key**, copy it (`csk-...`).
3. `export CEREBRAS_API_KEY=csk-...`

That's enough to start. Run `freellmpool ask "hello"`.

---

## Add more free pools (optional)

### OpenRouter — *many `:free` models*
1. <https://openrouter.ai/keys> → sign in → **Create Key**.
2. `export OPENROUTER_API_KEY=sk-or-...`

### Google Gemini (AI Studio) — *generous free tier*
1. <https://aistudio.google.com/apikey> → **Create API key**.
2. `export GEMINI_API_KEY=...`

### GitHub Models — *you probably already have this*
1. Any GitHub Personal Access Token works: <https://github.com/settings/tokens>
   → **Generate new token** (classic). No special scopes are required for
   Models; a token with no scopes is fine.
2. `export GITHUB_TOKEN=ghp_...`

### Mistral — *free tier*
1. <https://console.mistral.ai/api-keys> → **Create new key**.
2. `export MISTRAL_API_KEY=...`

   Also gives free **audio transcription** (Voxtral) — a failover for Groq's
   Whisper on `/v1/audio/transcriptions`.

### Cohere — *free trial keys*
1. <https://dashboard.cohere.com/api-keys> → copy your **Trial key**.
2. `export COHERE_API_KEY=...`

### SambaNova — *free tier*
1. <https://cloud.sambanova.ai/apis> → **Generate API key**.
2. `export SAMBANOVA_API_KEY=...`

### NVIDIA NIM — *free credits, huge catalog*
1. <https://build.nvidia.com> → sign in → pick a model → **Get API Key**.
2. `export NVIDIA_API_KEY=nvapi-...`

### Z.ai / Zhipu GLM — *free GLM flash models*
1. <https://z.ai> → sign in → API keys.
2. `export ZHIPU_API_KEY=...`

### Ollama Cloud — *free tier*
1. <https://ollama.com/settings/keys> → **Create key**.
2. `export OLLAMA_API_KEY=...`

### LongCat (Meituan) — *free tier*
1. <https://longcat.chat> → developer/API keys.
2. `export LONGCAT_API_KEY=...`

### Aion Labs — *20K free tokens/day, no card*
1. <https://api.aionlabs.ai> → create an account and API key.
2. `export AION_API_KEY=...`

   The free account tier is 15 requests/minute and 20,000 tokens/day. The
   allowance is shared across Aion's priced models and renews daily.

### ModelScope API Inference — *2,000 free calls/day*
1. <https://modelscope.cn/my/myaccesstoken> → create a long-lived access token.
2. `export MODELSCOPE_API_KEY=...`

   API-Inference-enabled models receive dynamic free quotas: currently 2,000
   calls/day in total and up to 500 calls/day for one model. Availability and
   per-model caps can change with platform capacity.

### SiliconFlow — *free models, identity verification required*
1. <https://cloud.siliconflow.cn/account/ak> → sign in and create an API key.
2. Complete identity verification to unlock the platform's free models.
3. `export SILICONFLOW_API_KEY=...`

   Free models have fixed rate limits and zero usage cost. They do not require
   a payment card, but the identity-verification requirement may make this
   provider unsuitable for some users or regions.

### Pollinations, OVHcloud, Kilo Gateway & LLM7 — *no signup needed*
Nothing to do — Pollinations, OVHcloud, and Kilo Gateway are anonymous, and LLM7
works without a key. For higher LLM7 limits you can optionally grab a token at
<https://token.llm7.io> and `export LLM7_API_KEY=...`.

**Kilo Gateway** is a keyless OpenAI-compatible aggregator of free models
(~200 req/hour per IP). Heads up: its free routes may log prompts — don't send
confidential data through Kilo models (`kilo/…`).

**OpenCode Zen** is cataloged as a keyless OpenAI-compatible gateway, but its
routes are disabled by default pending explicit opt-in and provider policy
review. Treat it like other anonymous routes: not for confidential prompts.

### Cloudflare Workers AI — *needs two values*
1. Account ID: Cloudflare dashboard → **Workers & Pages** (right sidebar shows
   your Account ID), or **Workers AI** → **Use REST API**.
2. API token: <https://dash.cloudflare.com/profile/api-tokens> → **Create
   Token** → use the **Workers AI** template (read is enough to run models).
3. `export CLOUDFLARE_ACCOUNT_ID=...` and `export CLOUDFLARE_API_TOKEN=...`

---

## Keeping keys around

Rather than re-exporting every shell, drop them in a `.env` file at your project
root (it's gitignored by default in this repo):

```bash
cp .env.example .env
# edit .env, fill in the keys you have
```

`freellmpool` reads from the **environment**, so load the file however you like —
e.g. `set -a; source .env; set +a`, or a tool like
[`direnv`](https://direnv.net/).

## A note on free-tier limits

Free tiers change. The per-day hints in
[`providers.toml`](../src/freellmpool/providers.toml) are conservative guesses
used only to spread load; `freellmpool` reacts to real `429` rate limits at call
time regardless. If a provider changes its limits, a one-line PR to
`providers.toml` keeps everyone current — see [CONTRIBUTING.md](../CONTRIBUTING.md).
