# Model activity audit — 2026-07-14

This audit compared the packaged provider catalog with live provider model-list
endpoints and sent a harmless `Reply with the single word: pong` completion to
configured routes. It did not send repository content or user prompts.

## Scope and method

- 18 of 19 chat providers were configured and tested. Google Gemini was the
  only provider without a usable local credential.
- All 355 pre-audit catalog chat models, including disabled entries, received a
  live probe through `freellmpool.client.call` with retries for transient
  failures.
- HTTP 200 responses with empty text were re-tested with a 256-token completion
  budget. This converted 31 of 32 empty 16-token responses into real `pong`
  completions; one Cloudflare Llama-2 LoRA route remained empty twice.
- Definitive 404/410/unsupported responses were re-tested before catalog
  changes. Slow NVIDIA routes were also re-tested serially with a 60-second
  timeout after the concurrent sweep.
- A second exhaustive pass completion-tested 124 remaining chat-shaped listing
  gaps. Six Hugging Face routes returned non-empty completions. Four answered a
  repeat probe before the account depleted its monthly included credits; every
  subsequent Hugging Face request then returned the same provider-wide 402.
- 429s, provider-level authentication failures, subscription/payment gates,
  transient 5xx responses, and isolated successes followed by model-specific
  payment gates were not used as evidence to enable or remove a route.

## Provider findings

| Provider | Live finding and action |
|---|---|
| Pollinations | Both enabled routes answered. Legacy `mistral` remains disabled and returned 404. |
| LLM7 | `gemma3:27b` answered keylessly and was added. Three enabled routes repeatedly reported currently unavailable and were disabled. Ten newly listed routes required valid paid/auth access and were excluded. |
| OVHcloud | 12/13 chat routes answered. `Llama-3.1-8B-Instruct` returned 404 and was disabled; the new `ppl` listing is not a chat model. |
| Kilo | Laguna XS.2 returned 404 and was replaced by live XS 2.1. Hy3 was added. The previously slow 550B Nemotron returned three non-empty completions in 0.60–0.84 seconds and was re-enabled. Paid listings are now filtered from discovery. |
| OpenCode Zen | Five `-free` routes answered, including new Hy3 and MiMo 2.5. They remain disabled pending the existing privacy/retention opt-in gate. |
| Hugging Face | All eight original routes answered, and eight current frontier candidates were completion-tested and added in the first pass. The exhaustive gap pass found six more working CohereLabs routes. Four of those repeated successfully before the account exhausted its monthly included credits; the remaining 94 listing gaps are inconclusive and were not added. |
| Groq | All 10 chat routes answered. No new chat route was added. |
| Cerebras | Both enabled routes answered. `gemma-4-31b` was listed, returned `pong`, and was added. |
| NVIDIA | GLM-5.1 returned 410 and was replaced by live GLM-5.2. Six routes timed out again in isolated 60-second tests, Kimi K2.6 returned 404, and Phi-4 Multimodal reported a degraded function; all were disabled. Phi-4 Mini returned `pong` and was re-enabled. DeepSeek V4 Flash recovered in the isolated pass and stayed enabled. |
| OpenRouter | Seven 429-limited routes stayed enabled. Five routes returned repeat 404s; Laguna XS.2 was replaced by XS 2.1 and the other four were disabled. Hy3 was added after a non-empty completion. |
| GitHub Models | The configured credential returned provider-wide 401 responses, so no model-level catalog decisions were made. |
| Cloudflare | Kimi K2.7 Code was listed and returned `pong`, so it was added. GLM-5.2 was account-gated (403) and excluded. The Llama-2 LoRA route remained empty with 256 output tokens and was disabled. |
| Mistral | All 38 enabled routes answered. Two new Labs listings required organization opt-in and were excluded. |
| Cohere | All 15 existing routes answered. `command-a-translate-08-2025` returned `pong` and was added; transcription listings are now classified as non-chat. |
| SambaNova | Gemma 4 answered once, then three isolated repeats required payment. It remains disabled, as do the other payment-gated routes. |
| Z.ai / Zhipu | Both catalog flash routes answered. Eight new listings returned 429 throughout retries, so none were added. |
| Ollama Cloud | 20/21 enabled routes answered. `rnj-1:8b` returned an explicit retirement 410 and was disabled. The two new listings required a subscription and were excluded. |
| LongCat | The preview route was unsupported and its listed successor required payment, leaving the provider with no enabled route. |
| Google Gemini | Not live-tested because no usable credential was configured; catalog state was unchanged. |

## Resulting catalog

- 19 providers
- 240 enabled chat routes
- 380 cataloged chat models
- 23 live-verified active model additions or successor replacements
- 2 newly cataloged OpenCode free routes kept disabled by policy
- 2 previously disabled routes re-enabled after repeat probes
- 19 existing stale, retired, empty, degraded, or repeatedly timing-out routes
  disabled for automatic routing
- 3 obsolete model identifiers replaced by live successor identifiers

The raw transient reports, including `/tmp/flp_deep_hunt_20260714.json`, were
kept outside the repository because provider error payloads can contain
account-specific diagnostic identifiers.
