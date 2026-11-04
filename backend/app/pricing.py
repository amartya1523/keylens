"""Cost from token counts, using a bundled snapshot of LiteLLM's public price list.

Two numbers per request:
  list_cost   what the call costs at the provider's published on-demand price
  actual_cost what you pay: 0 on a free tier, list_cost otherwise
"""
import json
import re
from functools import lru_cache
from pathlib import Path

_PRICES = Path(__file__).parent / "data" / "prices.json"

# LiteLLM keys use different provider prefixes in a few places.
_PREFIX = {"groq": "groq/", "xai": "xai/", "openrouter": "openrouter/",
           "deepseek": "deepseek/", "openai": "", "anthropic": ""}


@lru_cache(maxsize=1)
def _table() -> dict:
    return json.loads(_PRICES.read_text())


def snapshot_info() -> dict:
    t = _table()
    return {"source": t["source"], "fetched": t["fetched"], "models": len(t["models"])}


def lookup(provider: str, model: str | None) -> dict | None:
    if not model:
        return None
    models = _table()["models"]
    base = model.removesuffix(":free")
    candidates = [_PREFIX.get(provider, provider + "/") + base, base]
    if provider == "openrouter":          # "openai/gpt-4o" -> "gpt-4o", "x-ai/grok-4.7" -> "xai/grok-4.7"
        tail = base.split("/", 1)[-1]
        candidates += [tail, "xai/" + tail]
    # Only strip date-shaped suffixes ("claude-x-20251001", "gpt-4o-2024-08-06"), never "-fast"
    candidates += [re.sub(r"-\d{4}-\d{2}-\d{2}$|-\d{8}$|-\d{4}$", "", c) for c in candidates]
    for key in candidates:
        if key in models:
            return {"key": key, **models[key]}
    return None


def list_cost(provider: str, model: str | None, prompt_tokens: int,
              completion_tokens: int, cached_tokens: int = 0,
              cache_write_tokens: int = 0) -> tuple[float, str | None]:
    if model and model.endswith(":free"):    # OpenRouter free variants
        return 0.0, None
    price = lookup(provider, model)
    if not price or "input_cost_per_token" not in price:
        return 0.0, None
    inp = price["input_cost_per_token"]
    out = price.get("output_cost_per_token", 0.0)
    cached_rate = price.get("cache_read_input_token_cost", inp)
    write_rate = price.get("cache_creation_input_token_cost", inp)
    uncached = max(prompt_tokens - cached_tokens - cache_write_tokens, 0)
    cost = (uncached * inp + cached_tokens * cached_rate + cache_write_tokens * write_rate
            + completion_tokens * out)
    return round(cost, 10), price["key"]
