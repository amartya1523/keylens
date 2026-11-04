"""The metadata-only gateway.

Apps set   base_url = http://localhost:8000/v1   and   api_key = kl_live_...
KeyLens looks up the virtual key, swaps in the real provider key, forwards the
request, streams the answer back untouched, and records metadata only.

What is read from the request body: model, stream, and a few numeric params.
What is read from the response: the usage block and response headers.
Prompt and response text are never stored, logged or returned by any API.
"""
import json
import logging
import re
import time

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import pricing
from .classify import classify
from .db import jload, tx
from .providers import PROVIDERS, auth_headers
from .ratelimits import snapshot_from_headers
from .security import decrypt_key, sha256

router = APIRouter()
log = logging.getLogger("keylens.proxy")
_client: httpx.AsyncClient | None = None

# Only inference endpoints are reachable with a virtual key. A leaked browser key
# must not open files, batches, fine-tuning or account endpoints.
_ALLOWED = re.compile(r"^(chat/completions|completions|embeddings|responses|messages|models"
                      r"|models/[A-Za-z0-9._:/\-]+|audio/transcriptions|audio/translations)$")
_MAX_RAW_META = 4096

# Request headers worth forwarding upstream. Everything else is dropped,
# including the client's Authorization (it carries the virtual key).
_FORWARD = {"content-type", "accept", "anthropic-version", "anthropic-beta", "openai-beta",
            "openai-organization", "openai-project", "http-referer", "x-title"}
# Response headers we never pass back (hop-by-hop or recomputed).
_HOP = {"connection", "keep-alive", "transfer-encoding", "content-encoding",
        "content-length", "upgrade", "te", "trailer", "set-cookie"}
# Response headers kept in raw_meta.
_META_PREFIXES = ("x-ratelimit", "anthropic-ratelimit", "openai-", "x-groq", "retry-after")
_META_EXACT = {"x-request-id", "request-id", "anthropic-organization-id", "server"}
_SDK_PREFIX = "x-stainless-"


def client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(http2=True, timeout=httpx.Timeout(120, connect=10))
    return _client


async def close_client() -> None:
    if _client is not None:
        await _client.aclose()


def _error(status: int, message: str, kind: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind, "source": "keylens"}},
                        status_code=status)


def _resolve_vkey(token: str):
    with tx() as conn:
        row = conn.execute(
            """SELECT a.*, k.provider, k.key_enc, k.plan, k.key_info_json, k.user_id AS owner
               FROM apps a JOIN provider_keys k ON k.id = a.key_id
               WHERE a.vkey_hash = ?""", (sha256(token),)).fetchone()
        if not row:
            return None, None
        month_start = time.time() - 30 * 86400
        spent = conn.execute(
            "SELECT COALESCE(SUM(list_cost_usd),0) FROM events WHERE user_id=? AND app_id=? AND ts>?",
            (row["owner"], row["id"], month_start)).fetchone()[0]
        return dict(row), spent


def _request_params(data: dict) -> dict:
    """Numbers and flags only. Messages are counted, never read."""
    out = {}
    for k in ("model", "stream", "max_tokens", "max_completion_tokens", "temperature",
              "top_p", "n", "reasoning_effort", "service_tier"):
        if k in data and isinstance(data[k], (str, int, float, bool)):
            out[k] = data[k]
    if isinstance(data.get("messages"), list):
        out["messages_count"] = len(data["messages"])
    if isinstance(data.get("tools"), list):
        out["tools_count"] = len(data["tools"])
    if isinstance(data.get("input"), list):
        out["input_items"] = len(data["input"])
    return out


def _usage_numbers(usage: dict | None, provider: str) -> dict:
    """Normalise usage across providers. Returns numbers only."""
    u = usage if isinstance(usage, dict) else {}
    pt = u.get("prompt_tokens", u.get("input_tokens", 0)) or 0
    ct = u.get("completion_tokens", u.get("output_tokens", 0)) or 0
    det = u.get("completion_tokens_details") or u.get("output_tokens_details") or {}
    pdet = u.get("prompt_tokens_details") or u.get("input_tokens_details") or {}
    reasoning = int(det.get("reasoning_tokens") or 0)
    cached = int(pdet.get("cached_tokens") or 0)
    cache_write = 0
    if provider == "anthropic":
        # Anthropic's input_tokens excludes cache reads and cache writes
        cached = int(u.get("cache_read_input_tokens") or 0)
        cache_write = int(u.get("cache_creation_input_tokens") or 0)
        pt = int(pt) + cached + cache_write
    if provider == "xai":
        ct = int(ct) + reasoning   # xAI reports reasoning separately from completion_tokens
    out = {
        "prompt_tokens": int(pt),
        "completion_tokens": int(ct),
        "reasoning_tokens": reasoning,
        "cached_tokens": cached,
        "cache_write_tokens": cache_write,
    }
    if provider == "groq":
        out["queue_ms"] = round((u.get("queue_time") or 0) * 1000, 2)
        compute = (u.get("prompt_time") or 0) + (u.get("completion_time") or 0)
        out["compute_ms"] = round(compute * 1000, 2)
    if u.get("cost_in_usd_ticks") is not None:  # xAI reports its own cost: 1 USD = 1e10 ticks
        out["provider_cost_usd"] = u["cost_in_usd_ticks"] / 1e10
    return out


def _is_free(provider: str, plan: str, key_info: dict | None, snap: dict | None) -> bool:
    if plan == "free":
        return True
    if plan == "paid":
        return False
    if provider == "openrouter" and key_info:
        return bool(key_info.get("is_free_tier"))
    if provider == "groq" and snap and snap.get("limit_requests"):
        # Free-tier limits on Groq are small (e.g. 1K RPD / 8K TPM on gpt-oss).
        return (snap.get("limit_tokens") or 0) <= 30000
    return False


def _record(rec: dict, meta: dict, usage_raw: dict | None, resp_headers, req_headers):
    provider = rec["provider"]
    nums = _usage_numbers(usage_raw, provider)
    snap = snapshot_from_headers(resp_headers) if resp_headers is not None else None
    lc, price_key = pricing.list_cost(provider, meta.get("model"), nums["prompt_tokens"],
                                      nums["completion_tokens"], nums["cached_tokens"],
                                      nums["cache_write_tokens"])
    if "provider_cost_usd" in nums:
        lc = nums["provider_cost_usd"]
    free = _is_free(provider, rec["plan"], jload(rec["key_info_json"], {}), snap)
    src_class, src_detail = classify(req_headers)

    resp_meta = {}
    if resp_headers is not None:
        for k, v in resp_headers.items():
            kl = k.lower()
            if kl.startswith(_META_PREFIXES) or kl in _META_EXACT:
                resp_meta[kl] = v
    raw = {
        "path": meta["path"],
        "request": meta["params"],
        "sdk": {k.lower(): v for k, v in req_headers.items() if k.lower().startswith(_SDK_PREFIX)},
        "response_headers": resp_meta,
        "usage": usage_raw,
        "price_key": price_key,
        "tier": "free" if free else "paid",
        "injected_include_usage": meta.get("injected", False),
    }
    raw_json = json.dumps(raw)
    if len(raw_json) > _MAX_RAW_META:   # never let an unexpected field grow the row
        raw.pop("usage", None)
        raw["truncated"] = True
        raw_json = json.dumps(raw)[:_MAX_RAW_META]
    origin = _site(req_headers.get("origin") or req_headers.get("referer"))
    with tx() as conn:
        conn.execute(
            """INSERT INTO events (user_id, key_id, app_id, ts, provider, endpoint, model, stream,
                 status, error_type, source_class, source_detail, client_ip, user_agent, origin,
                 prompt_tokens, completion_tokens, reasoning_tokens, cached_tokens, latency_ms,
                 ttfb_ms, queue_ms, compute_ms, region, finish_reason, list_cost_usd,
                 actual_cost_usd, upstream_id, raw_meta)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (rec["owner"], rec["key_id"], rec["id"], meta["t0_wall"], provider, meta["path"],
             meta.get("model"), int(bool(meta.get("stream"))), meta.get("status"),
             meta.get("error_type"), src_class, src_detail, meta.get("ip"),
             (req_headers.get("user-agent") or "")[:200], origin,
             nums["prompt_tokens"], nums["completion_tokens"], nums["reasoning_tokens"],
             nums["cached_tokens"], meta.get("latency_ms"), meta.get("ttfb_ms"),
             nums.get("queue_ms"), nums.get("compute_ms"),
             resp_meta.get("x-groq-region"), meta.get("finish_reason"),
             lc, 0.0 if free else lc,
             resp_meta.get("x-request-id") or resp_meta.get("request-id"),
             raw_json))
        if snap:
            conn.execute(
                """INSERT INTO ratelimit_snapshots (key_id, ts, model, limit_requests,
                     remaining_requests, reset_requests_s, limit_tokens, remaining_tokens,
                     reset_tokens_s, via) VALUES (?,?,?,?,?,?,?,?,?, 'proxy')""",
                (rec["key_id"], meta.get("t_headers", time.time()), meta.get("model"), snap["limit_requests"],
                 snap["remaining_requests"], snap["reset_requests_s"], snap["limit_tokens"],
                 snap["remaining_tokens"], snap["reset_tokens_s"]))


def _site(url: str | None) -> str | None:
    """Keep scheme and host only: never store paths or query strings."""
    if not url:
        return None
    m = re.match(r"^([a-z][a-z0-9+.-]*://[^/?#]+)", url.strip(), re.I)
    return m.group(1)[:120] if m else url[:60]


def _safe_record(*args) -> None:
    """Recording must never break the caller's request (fail open)."""
    try:
        _record(*args)
    except Exception:  # noqa: BLE001
        log.exception("could not record request metadata")


def _upstream_url(provider_id: str, path: str) -> str:
    base = PROVIDERS[provider_id].base_url
    return f"{base}/{path.lstrip('/')}"


@router.options("/v1/{path:path}")
@router.options("/openai/v1/{path:path}")
async def preflight(path: str):
    return Response(status_code=204)


# /openai/v1 is the path the Groq SDK appends to its base_url.
@router.api_route("/v1/{path:path}", methods=["GET", "POST"])
@router.api_route("/openai/v1/{path:path}", methods=["GET", "POST"])
async def proxy(path: str, request: Request):
    clean = path.strip("/")
    if any(c in clean for c in "#?%\\") or ".." in clean or not _ALLOWED.match(clean):
        return _error(404, f"KeyLens only proxies inference endpoints, not /{clean}.", "endpoint_not_allowed")
    path = clean
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else request.headers.get("x-api-key", "")
    if not token.startswith("kl_live_"):
        return _error(401, "Use a KeyLens virtual key (kl_live_...) as the API key.", "invalid_api_key")

    rec, spent = _resolve_vkey(token)
    if not rec:
        return _error(401, "Unknown KeyLens virtual key.", "invalid_api_key")
    if not rec["enabled"]:
        return _error(403, f"App '{rec['name']}' is paused in KeyLens.", "app_paused")
    if rec["budget_usd"] is not None and spent >= rec["budget_usd"]:
        return _error(429, f"App '{rec['name']}' reached its ${rec['budget_usd']:.2f} budget.",
                      "budget_exceeded")

    provider = rec["provider"]
    raw_key = decrypt_key(rec["key_enc"])
    body = await request.body()
    data = {}
    if body:
        try:
            data = json.loads(body)
        except ValueError:
            data = {}
    if not isinstance(data, dict):
        data = {}
    meta = {"path": "/" + path, "t0": time.monotonic(), "t0_wall": time.time(),
            "model": data.get("model"), "stream": bool(data.get("stream")),
            "params": _request_params(data),
            "ip": request.client.host if request.client else None}

    # OpenAI and xAI only send usage on streams when asked to.
    if meta["stream"] and provider in ("openai", "xai"):
        opts = data.get("stream_options") or {}
        if not opts.get("include_usage"):
            data["stream_options"] = {**opts, "include_usage": True}
            body = json.dumps(data).encode()
            meta["injected"] = True

    headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARD}
    headers.update(auth_headers(PROVIDERS[provider], raw_key))
    headers["accept-encoding"] = "identity"
    req_headers = dict(request.headers)

    try:
        upstream = await client().send(
            client().build_request(request.method, _upstream_url(provider, path),
                                   headers=headers, content=body or None,
                                   params=request.query_params),
            stream=True)
    except httpx.TimeoutException:
        meta.update(status=504, error_type="upstream_timeout",
                    latency_ms=(time.monotonic() - meta["t0"]) * 1000)
        _safe_record(rec, meta, None, None, req_headers)
        return _error(504, "Provider timed out.", "upstream_timeout")
    except httpx.HTTPError as exc:
        meta.update(status=502, error_type=type(exc).__name__,
                    latency_ms=(time.monotonic() - meta["t0"]) * 1000)
        _safe_record(rec, meta, None, None, req_headers)
        return _error(502, "Could not reach the provider.", "upstream_unreachable")

    meta["status"] = upstream.status_code
    meta["t_headers"] = time.time()   # rate-limit headers describe this moment
    out_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in _HOP}
    content_type = upstream.headers.get("content-type", "")
    is_sse = "text/event-stream" in content_type

    if not is_sse:
        content = await upstream.aread()
        await upstream.aclose()
        meta["latency_ms"] = (time.monotonic() - meta["t0"]) * 1000
        usage = None
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                usage = parsed.get("usage") or (parsed.get("x_groq") or {}).get("usage")
                choices = parsed.get("choices") or []
                if choices and isinstance(choices[0], dict):
                    meta["finish_reason"] = choices[0].get("finish_reason")
                meta["finish_reason"] = meta.get("finish_reason") or parsed.get("stop_reason")
                if upstream.status_code >= 400 and isinstance(parsed.get("error"), dict):
                    err = parsed["error"]
                    meta["error_type"] = str(err.get("code") or err.get("type") or upstream.status_code)[:80]
            del parsed
        except ValueError:
            pass
        if path.rstrip("/").endswith("models"):
            return Response(content, status_code=upstream.status_code, headers=out_headers,
                            media_type=content_type or None)
        _safe_record(rec, meta, usage, upstream.headers, req_headers)
        if rec["budget_usd"]:
            out_headers["x-keylens-budget-used"] = f"{100 * spent / rec['budget_usd']:.0f}%"
        return Response(content, status_code=upstream.status_code, headers=out_headers,
                        media_type=content_type or None)

    async def relay():
        usage: dict = {}
        buffer = b""
        try:
            # aiter_bytes decodes gzip/br, which matches dropping content-encoding above
            async for chunk in upstream.aiter_bytes():
                if "ttfb_ms" not in meta:
                    meta["ttfb_ms"] = (time.monotonic() - meta["t0"]) * 1000
                yield chunk
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    if not line.startswith(b"data:") or line.strip() == b"data: [DONE]":
                        continue
                    if b'"usage"' not in line and b"finish_reason" not in line \
                            and b"message_" not in line:
                        continue
                    try:
                        evt = json.loads(line[5:])
                    except ValueError:
                        continue
                    _absorb_stream_event(evt, usage, meta)
                    del evt
        except httpx.HTTPError:
            meta["error_type"] = "stream_aborted"
        except BaseException:
            meta.setdefault("error_type", "client_aborted")
            raise
        finally:
            # record first: awaiting aclose() can re-raise a cancellation
            meta["latency_ms"] = (time.monotonic() - meta["t0"]) * 1000
            _safe_record(rec, meta, usage or None, upstream.headers, req_headers)
            await upstream.aclose()

    out_headers["x-accel-buffering"] = "no"
    return StreamingResponse(relay(), status_code=upstream.status_code,
                             headers=out_headers, media_type=content_type)


def _absorb_stream_event(evt: dict, usage: dict, meta: dict) -> None:
    """Copy usage numbers and finish reason out of one SSE event. No text is kept."""
    if not isinstance(evt, dict):
        return
    kind = evt.get("type")
    if kind == "message_start":                         # Anthropic
        usage.update((evt.get("message") or {}).get("usage") or {})
        return
    if kind == "message_delta":
        usage.update(evt.get("usage") or {})
        meta["finish_reason"] = (evt.get("delta") or {}).get("stop_reason")
        return
    found = (evt.get("usage") or (evt.get("response") or {}).get("usage")   # Responses API
             or (evt.get("x_groq") or {}).get("usage"))
    if found:
        usage.clear()
        usage.update(found)
    for c in evt.get("choices") or []:
        if isinstance(c, dict) and c.get("finish_reason"):
            meta["finish_reason"] = c["finish_reason"]
