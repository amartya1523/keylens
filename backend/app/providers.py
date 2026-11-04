"""Provider registry: how to recognise a key, validate it, and read what the
provider itself is willing to tell us about it.

Facts below were checked against provider docs on 2026-11-04 (see docs/research.md).
"""
import re
import time
from dataclasses import dataclass, field

import httpx


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    base_url: str                 # OpenAI-compatible base (or Anthropic native)
    key_pattern: str              # regex for auto-detection
    auth: str = "bearer"          # bearer | x-api-key
    models_path: str = "/models"
    key_info_url: str | None = None   # endpoint a normal key can call about itself
    rpd_header: bool = False      # x-ratelimit-*-requests means requests per DAY (Groq)
    notes: list[str] = field(default_factory=list)


PROVIDERS: dict[str, Provider] = {
    "groq": Provider(
        id="groq", name="Groq",
        base_url="https://api.groq.com/openai/v1",
        key_pattern=r"^gsk_[A-Za-z0-9]{40,}$",
        rpd_header=True,
        notes=[
            "Rate limits are per organization and per model, not per key.",
            "x-ratelimit-*-requests is requests per day; *-tokens is tokens per minute.",
            "No public usage API: KeyLens reconstructs usage from its own proxy log "
            "and infers outside usage from the rate-limit counters.",
        ],
    ),
    "xai": Provider(
        id="xai", name="xAI (Grok)",
        base_url="https://api.x.ai/v1",
        key_pattern=r"^xai-[A-Za-z0-9]{40,}$",
        key_info_url="https://api.x.ai/v1/api-key",
        notes=[
            "GET /v1/api-key returns the key's name, ACLs, team and blocked status.",
            "With a management key, POST management-api.x.ai/v1/billing/teams/{team}/usage "
            "can group usage by api_key_id and request_ip.",
            "Responses include usage.cost_in_usd_ticks (1 USD = 1e10 ticks).",
        ],
    ),
    "openrouter": Provider(
        id="openrouter", name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        key_pattern=r"^sk-or-v1-[A-Za-z0-9]{40,}$",
        key_info_url="https://openrouter.ai/api/v1/key",
        notes=["GET /api/v1/key returns usage, daily usage, limit and free-tier flag."],
    ),
    "anthropic": Provider(
        id="anthropic", name="Anthropic",
        base_url="https://api.anthropic.com/v1",
        key_pattern=r"^sk-ant-api\d\d-[A-Za-z0-9_\-]{40,}$",
        auth="x-api-key",
        notes=[
            "Per-key usage needs an Admin key (sk-ant-admin01-): "
            "/v1/organizations/usage_report/messages grouped by api_key_id.",
        ],
    ),
    "openai": Provider(
        id="openai", name="OpenAI",
        base_url="https://api.openai.com/v1",
        key_pattern=r"^sk-(proj-|svcacct-)?[A-Za-z0-9_\-]{20,}$",
        notes=[
            "Per-key usage needs an Admin key (sk-admin-): "
            "/v1/organization/usage/completions grouped by api_key_id.",
            "Streaming usage only arrives when stream_options.include_usage is set; "
            "the KeyLens proxy sets it.",
        ],
    ),
    "deepseek": Provider(
        id="deepseek", name="DeepSeek",
        base_url="https://api.deepseek.com/v1",
        key_pattern=r"^sk-[a-f0-9]{32}$",
        key_info_url="https://api.deepseek.com/user/balance",
        notes=["GET /user/balance returns remaining balance."],
    ),
}

# Most specific first: sk-ant / sk-or / deepseek hex must win over the generic OpenAI sk-.
DETECT_ORDER = ["groq", "xai", "openrouter", "anthropic", "deepseek", "openai"]


def detect(raw_key: str) -> str | None:
    raw_key = raw_key.strip()
    for pid in DETECT_ORDER:
        if re.match(PROVIDERS[pid].key_pattern, raw_key):
            return pid
    return None


def auth_headers(provider: Provider, raw_key: str) -> dict:
    if provider.auth == "x-api-key":
        return {"x-api-key": raw_key, "anthropic-version": "2023-06-01"}
    return {"Authorization": f"Bearer {raw_key}"}


async def check_key(provider_id: str, raw_key: str) -> dict:
    """Validate a key with free calls only: list models + provider key-info endpoint."""
    p = PROVIDERS[provider_id]
    result = {"ok": False, "status": None, "models": [], "key_info": None,
              "region": None, "checked_at": time.time(), "error": None}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(p.base_url + p.models_path, headers=auth_headers(p, raw_key))
        except httpx.HTTPError as exc:
            result["error"] = f"network: {type(exc).__name__}"
            return result
        result["status"] = r.status_code
        result["region"] = r.headers.get("x-groq-region")
        if r.status_code == 200:
            result["ok"] = True
            data = r.json().get("data", [])
            result["models"] = sorted(
                [{"id": m.get("id"), "owned_by": m.get("owned_by"),
                  "context_window": m.get("context_window")} for m in data],
                key=lambda m: m["id"] or "",
            )
        else:
            result["error"] = _error_type(r)

        if p.key_info_url and result["ok"]:
            try:
                ki = await client.get(p.key_info_url, headers=auth_headers(p, raw_key))
                if ki.status_code == 200:
                    result["key_info"] = _safe_key_info(provider_id, ki.json())
            except httpx.HTTPError:
                pass
    return result


def _error_type(r: httpx.Response) -> str:
    try:
        err = r.json().get("error", {})
        if isinstance(err, dict):
            return str(err.get("code") or err.get("type") or r.status_code)[:80]
    except ValueError:
        pass
    return str(r.status_code)


def _safe_key_info(provider_id: str, data: dict) -> dict:
    """Keep only metadata fields; drop anything unexpected."""
    if provider_id == "xai":
        keep = ["name", "api_key_id", "team_id", "acls", "api_key_blocked",
                "api_key_disabled", "team_blocked", "create_time", "modify_time",
                "redacted_api_key"]
        return {k: data.get(k) for k in keep if k in data}
    if provider_id == "openrouter":
        d = data.get("data", data)
        keep = ["label", "usage", "usage_daily", "limit", "limit_remaining",
                "limit_reset", "is_free_tier"]
        return {k: d.get(k) for k in keep if k in d}
    if provider_id == "deepseek":
        return {"is_available": data.get("is_available"),
                "balance_infos": data.get("balance_infos")}
    return {}
