# What each provider tells you about a key (research notes, 4 Nov 2026)

The question behind KeyLens: *if all I have is an LLM API key, how much can I find
out about where and how it is used?* The short answer: almost nothing from the key
alone, a lot once traffic goes through a layer you own. These notes record what
each provider actually exposes, checked against their docs and live calls.

## Live checks with a Groq key

| Call | What came back |
|---|---|
| `GET /openai/v1/models` | 11 models; headers `x-groq-region`, `x-request-id`; **no rate-limit headers** |
| `POST /openai/v1/chat/completions` | `usage` with `prompt_tokens`, `completion_tokens`, `completion_tokens_details.reasoning_tokens`, plus `queue_time`, `prompt_time`, `completion_time`; `x_groq.id`; `service_tier` |
| Same call, headers | `x-ratelimit-limit-requests: 1000`, `x-ratelimit-remaining-requests: 999`, `x-ratelimit-reset-requests: 1m26.4s`, `x-ratelimit-limit-tokens: 8000`, `x-ratelimit-remaining-tokens`, `x-ratelimit-reset-tokens` |
| Streaming | the last SSE chunk carries `usage` and `x_groq.usage` |
| `/openai/v1/usage`, `/openai/v1/organization/usage` | 404: Groq has no public usage API |

The reset value is the key observation: after one request on a 1,000-per-day limit
the bucket needs 86.4 s to refill, and 86,400 s / 1,000 = 86.4 s. After two requests
it was 172.8 s. So the daily limit behaves as a continuously refilling token bucket,
and `reset_s × limit / 86400` is exactly how many requests the organization has
spent recently. KeyLens compares that with what it proxied itself to estimate usage
it never saw ("ghost usage"). In a test, 4 calls sent straight to Groq were reported
as "~3 requests outside KeyLens" (the estimate is a deliberate lower bound).

## Provider matrix

| Provider | Key prefix | Free validation | What a normal key can learn about itself | Per-key usage with an admin key | Useful response data |
|---|---|---|---|---|---|
| Groq | `gsk_` | `GET /openai/v1/models` | nothing beyond models | none public (enterprise Prometheus metrics by project and model) | rate-limit headers per org + model; queue vs compute time; region |
| xAI (Grok) | `xai-` | `GET /v1/models` | `GET /v1/api-key`: name, ACLs, team, blocked/disabled flags | `POST management-api.x.ai/v1/billing/teams/{team}/usage`, `groupBy` includes **`api_key_id` and `request_ip`** | `usage.cost_in_usd_ticks` (1 USD = 1e10 ticks), reasoning tokens |
| OpenAI | `sk-`, `sk-proj-` | `GET /v1/models` | nothing | `/v1/organization/usage/completions` grouped by `api_key_id`, project, user, model (needs `sk-admin-`) | `x-ratelimit-*` per minute; streaming usage only with `stream_options.include_usage` |
| Anthropic | `sk-ant-api03-` | `GET /v1/models` | nothing | `/v1/organizations/usage_report/messages` grouped by `api_key_id`, workspace, model (needs admin key) | `anthropic-ratelimit-*` (token bucket, RFC 3339 reset) |
| OpenRouter | `sk-or-v1-` | `GET /api/v1/key` | **usage, daily usage, limit, free-tier flag** | n/a | app attribution via `HTTP-Referer` / `X-Title` |
| DeepSeek | `sk-` + 32 hex | `GET /user/balance` | balance | none | – |

Takeaways:

1. With only a normal key you can never answer *where* it was used. Providers keep
   no per-request record that the key holder can read. The exceptions are xAI with a
   management key (usage by key and caller IP) and OpenRouter (totals per key).
2. To see *where*, you have to be in the path: one virtual key per app plus a proxy.
   That is what KeyLens does, and it only needs a base-URL change (config, not code).
3. Rate-limit counters let you estimate traffic you did **not** route, which turns a
   plain proxy into a leak detector.

## Request-source signals used by the classifier

| Signal | Class |
|---|---|
| `Sec-Fetch-Mode`, `Origin` + `Mozilla/` UA, `x-stainless-runtime: browser:*` | browser (hard to fake from inside a real browser) |
| `User-Agent: OpenAI/Python …`, `Groq/Python …`, `x-stainless-lang/os/runtime` | official SDK, with language, runtime and OS |
| `curl/`, `HTTPie/`, `PostmanRuntime/`, `insomnia/` | CLI / API client |
| `python-requests/`, `python-httpx/`, `axios/`, `Go-http-client/` | hand-written script or server |
| `HTTP-Referer`, `X-Title` | IDE or agent tool (OpenRouter convention) |

Any client can set any User-Agent, so these are labels, not proof.

## Prices

Cost is computed from the token counts using a snapshot of LiteLLM's public price
list (`model_prices_and_context_window.json`, 732 models kept for the supported
providers, fetched 2026-11-04). Groq examples per 1M tokens: `openai/gpt-oss-20b`
$0.075 in / $0.30 out, `openai/gpt-oss-120b` $0.15 / $0.60, `qwen/qwen3.8-27b`
$0.80 / $4.00. On a free tier KeyLens records $0 actually paid and keeps the list
price as "what this would cost".

## Sources

- Groq rate limits: https://console.groq.com/docs/rate-limits
- Groq projects and limits: https://console.groq.com/docs/projects
- Groq models and pricing: https://console.groq.com/docs/models
- xAI API key info: https://docs.x.ai/developers/rest-api-reference/inference/other
- xAI usage by key and IP: https://docs.x.ai/developers/rest-api-reference/management/billing
- xAI cost tracking: https://docs.x.ai/developers/cost-tracking
- OpenAI rate limits: https://developers.openai.com/api/docs/guides/rate-limits
- Anthropic rate limits: https://platform.claude.com/docs/en/api/rate-limits
- Anthropic usage and cost API: https://platform.claude.com/docs/en/manage-claude/usage-cost-api
- OpenRouter key endpoint: https://openrouter.ai/docs/api/api-reference/api-keys/get-current-key
- DeepSeek balance: https://api-docs.deepseek.com/api/get-user-balance
- LiteLLM prices: https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json
