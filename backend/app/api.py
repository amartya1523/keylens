"""Dashboard API: auth, keys, apps (virtual keys), stats, insights, scanner."""
import csv
import io
import json
import statistics
import time
from collections import Counter, defaultdict

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from . import insights, pricing, scanner
from .config import SESSION_DAYS
from .db import jload, rows, tx
from .providers import PROVIDERS, auth_headers, check_key, detect
from .ratelimits import forecast, ghost_analysis, snapshot_from_headers
from .security import (decrypt_key, encrypt_key, fingerprint, hash_password, hint, mask,
                       new_session_token, new_virtual_key, sha256, verify_password)

router = APIRouter(prefix="/api")
RANGES = {"1h": 3600, "24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400}


# ---------------- auth ----------------

class SignupIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    name: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8, max_length=200)


class LoginIn(BaseModel):
    email: str
    password: str


def _issue_session(conn, user_id: int) -> str:
    token = new_session_token()
    conn.execute("INSERT INTO sessions VALUES (?,?,?)",
                 (sha256(token), user_id, time.time() + SESSION_DAYS * 86400))
    return token


def current_user(authorization: str = Header(default="")) -> dict:
    token = authorization[7:] if authorization.lower().startswith("bearer ") else ""
    with tx() as conn:
        row = conn.execute(
            """SELECT u.id, u.email, u.name FROM sessions s JOIN users u ON u.id = s.user_id
               WHERE s.token_hash=? AND s.expires_at>?""", (sha256(token), time.time())).fetchone()
    if not row:
        raise HTTPException(401, "Not signed in")
    return dict(row)


@router.post("/auth/signup")
def signup(body: SignupIn):
    email = body.email.strip().lower()
    if "@" not in email:
        raise HTTPException(400, "Enter a valid email")
    with tx() as conn:
        if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(409, "An account with this email already exists")
        cur = conn.execute("INSERT INTO users (email, name, password_hash, created_at) VALUES (?,?,?,?)",
                           (email, body.name.strip(), hash_password(body.password), time.time()))
        token = _issue_session(conn, cur.lastrowid)
    return {"token": token, "user": {"id": cur.lastrowid, "email": email, "name": body.name.strip()}}


@router.post("/auth/login")
def login(body: LoginIn):
    with tx() as conn:
        row = conn.execute("SELECT * FROM users WHERE email=?", (body.email.strip().lower(),)).fetchone()
        if not row or not verify_password(body.password, row["password_hash"]):
            raise HTTPException(401, "Wrong email or password")
        token = _issue_session(conn, row["id"])
    return {"token": token, "user": {"id": row["id"], "email": row["email"], "name": row["name"]}}


@router.get("/auth/me")
def me(user=Depends(current_user)):
    return user


@router.post("/auth/logout")
def logout(authorization: str = Header(default="")):
    with tx() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (sha256(authorization[7:]),))
    return {"ok": True}


# ---------------- provider keys ----------------

class KeyIn(BaseModel):
    key: str = Field(min_length=10, max_length=500)
    label: str | None = Field(default=None, max_length=80)
    plan: str = "auto"


class KeyPatch(BaseModel):
    label: str | None = None
    plan: str | None = None


class DetectIn(BaseModel):
    key: str


def _public_key(row: dict) -> dict:
    out = {k: row[k] for k in ("id", "provider", "label", "masked", "fingerprint", "plan",
                               "status", "last_checked", "created_at")}
    out["provider_name"] = PROVIDERS[row["provider"]].name
    out["models"] = jload(row["models_json"], [])
    out["key_info"] = jload(row["key_info_json"], {})
    out["notes"] = PROVIDERS[row["provider"]].notes
    return out


@router.post("/keys/detect")
def detect_key(body: DetectIn, user=Depends(current_user)):
    pid = detect(body.key)
    return {"provider": pid, "provider_name": PROVIDERS[pid].name if pid else None}


@router.post("/keys")
async def add_key(body: KeyIn, user=Depends(current_user)):
    raw = body.key.strip()
    pid = detect(raw)
    if not pid:
        raise HTTPException(400, "Could not recognise this key. Supported: Groq (gsk_), xAI (xai-), "
                                 "OpenAI (sk-), Anthropic (sk-ant-), OpenRouter (sk-or-), DeepSeek.")
    result = await check_key(pid, raw)
    status = "valid" if result["ok"] else f"invalid ({result['error']})"
    label = (body.label or f"{PROVIDERS[pid].name} key").strip()
    with tx() as conn:
        try:
            cur = conn.execute(
                """INSERT INTO provider_keys (user_id, provider, label, key_enc, fingerprint, masked,
                     plan, status, models_json, key_info_json, last_checked, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (user["id"], pid, label, encrypt_key(raw), fingerprint(raw), mask(raw), body.plan,
                 status, json.dumps(result["models"]), json.dumps(result["key_info"] or {}),
                 result["checked_at"], time.time()))
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(409, "You already added this key")
            raise
        row = dict(conn.execute("SELECT * FROM provider_keys WHERE id=?", (cur.lastrowid,)).fetchone())
    return {"key": _public_key(row), "check": {k: result[k] for k in ("ok", "status", "error", "region")}}


def _own_key(conn, user_id: int, key_id: int) -> dict:
    row = conn.execute("SELECT * FROM provider_keys WHERE id=? AND user_id=?", (key_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "Key not found")
    return dict(row)


@router.get("/keys")
def list_keys(user=Depends(current_user)):
    now = time.time()
    with tx() as conn:
        keys = rows(conn.execute("SELECT * FROM provider_keys WHERE user_id=? ORDER BY created_at", (user["id"],)))
        out = []
        for k in keys:
            item = _public_key(k)
            agg = conn.execute(
                """SELECT COUNT(*) n, COALESCE(SUM(prompt_tokens+completion_tokens),0) tokens,
                          COALESCE(SUM(list_cost_usd),0) list_cost, COALESCE(SUM(actual_cost_usd),0) actual,
                          MAX(ts) last_used FROM events WHERE key_id=?""", (k["id"],)).fetchone()
            item["stats"] = dict(agg)
            item["apps"] = conn.execute("SELECT COUNT(*) FROM apps WHERE key_id=?", (k["id"],)).fetchone()[0]
            snaps = rows(conn.execute(
                """SELECT * FROM ratelimit_snapshots WHERE key_id=? AND id IN
                     (SELECT MAX(id) FROM ratelimit_snapshots WHERE key_id=? GROUP BY model)""",
                (k["id"], k["id"])))
            item["limits"] = snaps
            ev_ts = [r[0] for r in conn.execute("SELECT ts FROM events WHERE key_id=? AND ts>?", (k["id"], now - 3600))]
            item["forecast"] = forecast(ev_ts, max(snaps, key=lambda s: s["ts"]) if snaps else None, now)
            out.append(item)
    return out


@router.patch("/keys/{key_id}")
def patch_key(key_id: int, body: KeyPatch, user=Depends(current_user)):
    with tx() as conn:
        _own_key(conn, user["id"], key_id)
        if body.label:
            conn.execute("UPDATE provider_keys SET label=? WHERE id=?", (body.label[:80], key_id))
        if body.plan in ("auto", "free", "paid"):
            conn.execute("UPDATE provider_keys SET plan=? WHERE id=?", (body.plan, key_id))
    return {"ok": True}


@router.delete("/keys/{key_id}")
def delete_key(key_id: int, user=Depends(current_user)):
    with tx() as conn:
        _own_key(conn, user["id"], key_id)
        conn.execute("DELETE FROM provider_keys WHERE id=?", (key_id,))
        conn.execute("DELETE FROM ratelimit_snapshots WHERE key_id=?", (key_id,))
    return {"ok": True}


@router.post("/keys/{key_id}/check")
async def recheck_key(key_id: int, user=Depends(current_user)):
    with tx() as conn:
        k = _own_key(conn, user["id"], key_id)
    result = await check_key(k["provider"], decrypt_key(k["key_enc"]))
    with tx() as conn:
        conn.execute("UPDATE provider_keys SET status=?, models_json=?, key_info_json=?, last_checked=? WHERE id=?",
                     ("valid" if result["ok"] else f"invalid ({result['error']})",
                      json.dumps(result["models"]), json.dumps(result["key_info"] or {}),
                      result["checked_at"], key_id))
    return {k2: result[k2] for k2 in ("ok", "status", "error", "region")}


_SKIP_MODELS = ("whisper", "guard", "orpheus", "tts", "embed", "safeguard")


@router.post("/keys/{key_id}/probe")
async def probe_key(key_id: int, user=Depends(current_user)):
    """One tiny request (max 1 output token) to read the rate-limit headers.
    Costs one request from the daily quota; recorded so it is never counted as ghost usage."""
    with tx() as conn:
        k = _own_key(conn, user["id"], key_id)
    p = PROVIDERS[k["provider"]]
    models = [m["id"] for m in jload(k["models_json"], []) if not any(s in (m["id"] or "") for s in _SKIP_MODELS)]
    if not models:
        raise HTTPException(400, "No chat model available on this key")
    model = sorted(models, key=lambda m: ("20b" not in m, "mini" not in m, m))[0]
    raw = decrypt_key(k["key_enc"])
    body = {"model": model, "max_tokens": 1, "messages": [{"role": "user", "content": "."}]}
    path = "/messages" if k["provider"] == "anthropic" else "/chat/completions"
    t0 = time.time()
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(p.base_url + path, headers=auth_headers(p, raw), json=body)
    snap = snapshot_from_headers(r.headers)
    with tx() as conn:
        # A probe is traffic KeyLens itself sent: log it as an event of the KeyLens probe.
        usage = {}
        try:
            usage = r.json().get("usage") or {}
        except ValueError:
            pass
        lc, _ = pricing.list_cost(k["provider"], model, usage.get("prompt_tokens", 0),
                                  usage.get("completion_tokens", 0))
        conn.execute(
            """INSERT INTO events (user_id, key_id, app_id, ts, provider, endpoint, model, status,
                 source_class, source_detail, prompt_tokens, completion_tokens, latency_ms,
                 region, list_cost_usd, actual_cost_usd, upstream_id, raw_meta)
               VALUES (?,?,NULL,?,?,?,?,?, 'probe','KeyLens rate-limit probe',?,?,?,?,?,0,?,?)""",
            (user["id"], key_id, t0, k["provider"], path, model, r.status_code,
             usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
             (time.time() - t0) * 1000, r.headers.get("x-groq-region"), lc,
             r.headers.get("x-request-id"),
             json.dumps({"probe": True, "response_headers": {h: v for h, v in r.headers.items()
                                                              if "ratelimit" in h or h in ("x-request-id", "x-groq-region")}})))
        if snap:
            conn.execute(
                """INSERT INTO ratelimit_snapshots (key_id, ts, model, limit_requests, remaining_requests,
                     reset_requests_s, limit_tokens, remaining_tokens, reset_tokens_s, via)
                   VALUES (?,?,?,?,?,?,?,?,?, 'probe')""",
                (key_id, time.time(), model, snap["limit_requests"], snap["remaining_requests"],
                 snap["reset_requests_s"], snap["limit_tokens"], snap["remaining_tokens"],
                 snap["reset_tokens_s"]))
    return {"model": model, "status": r.status_code, "limits": snap}


@router.get("/keys/{key_id}/ghost")
def key_ghost(key_id: int, user=Depends(current_user)):
    now = time.time()
    with tx() as conn:
        _own_key(conn, user["id"], key_id)
        models = [r[0] for r in conn.execute(
            "SELECT DISTINCT model FROM ratelimit_snapshots WHERE key_id=? AND ts>?", (key_id, now - 86400))]
        out = {}
        for m in models:
            snaps = rows(conn.execute("SELECT * FROM ratelimit_snapshots WHERE key_id=? AND model=? AND ts>? ORDER BY ts",
                                      (key_id, m, now - 86400)))
            own = [r[0] for r in conn.execute("SELECT ts FROM events WHERE key_id=? AND model=? AND ts>?",
                                              (key_id, m, now - 86400))]
            out[m] = ghost_analysis(snaps, own)
    return out


# ---------------- apps / virtual keys ----------------

class AppIn(BaseModel):
    key_id: int
    name: str = Field(min_length=1, max_length=60)
    kind: str = "other"
    budget_usd: float | None = None


class AppPatch(BaseModel):
    name: str | None = None
    enabled: bool | None = None
    budget_usd: float | None = None
    clear_budget: bool = False


def _snippets(vkey: str, base: str, provider: str, model: str | None) -> dict:
    model = model or "openai/gpt-oss-20b"
    return {
        "base_url": base,
        "python": f'from openai import OpenAI\nclient = OpenAI(base_url="{base}", api_key="{vkey}")\n'
                  f'r = client.chat.completions.create(model="{model}",\n'
                  f'    messages=[{{"role": "user", "content": "Hello"}}])',
        "curl": f'curl {base}/chat/completions \\\n  -H "Authorization: Bearer {vkey}" \\\n'
                f'  -H "Content-Type: application/json" \\\n'
                f'  -d \'{{"model": "{model}", "messages": [{{"role": "user", "content": "Hello"}}]}}\'',
        "env": f"OPENAI_BASE_URL={base}\nOPENAI_API_KEY={vkey}",
    }


@router.post("/apps")
def create_app(body: AppIn, request: Request, user=Depends(current_user)):
    vkey = new_virtual_key()
    with tx() as conn:
        k = _own_key(conn, user["id"], body.key_id)
        cur = conn.execute(
            """INSERT INTO apps (user_id, key_id, name, kind, vkey_hash, vkey_hint, budget_usd, created_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (user["id"], body.key_id, body.name.strip(), body.kind, sha256(vkey), hint(vkey),
             body.budget_usd, time.time()))
    base = str(request.base_url).rstrip("/") + "/v1"
    models = [m["id"] for m in jload(k["models_json"], [])]
    model = next((m for m in models if "gpt-oss-20b" in m), models[0] if models else None)
    return {"id": cur.lastrowid, "virtual_key": vkey,
            "snippets": _snippets(vkey, base, k["provider"], model),
            "warning": "Copy this key now. KeyLens stores only its hash."}


@router.get("/apps")
def list_apps(user=Depends(current_user)):
    with tx() as conn:
        apps = rows(conn.execute(
            """SELECT a.id, a.key_id, a.name, a.kind, a.vkey_hint, a.enabled, a.budget_usd, a.created_at,
                      k.label key_label, k.provider, k.masked FROM apps a
               JOIN provider_keys k ON k.id=a.key_id WHERE a.user_id=? ORDER BY a.created_at""",
            (user["id"],)))
        for a in apps:
            agg = conn.execute(
                """SELECT COUNT(*) n, COALESCE(SUM(prompt_tokens+completion_tokens),0) tokens,
                          COALESCE(SUM(list_cost_usd),0) list_cost, MAX(ts) last_used,
                          COUNT(DISTINCT client_ip) ips FROM events WHERE app_id=?""", (a["id"],)).fetchone()
            a["stats"] = dict(agg)
            a["month_list_cost"] = conn.execute(
                "SELECT COALESCE(SUM(list_cost_usd),0) FROM events WHERE app_id=? AND ts>?",
                (a["id"], time.time() - 30 * 86400)).fetchone()[0]
            a["sources"] = [dict(r) for r in conn.execute(
                "SELECT source_class, COUNT(*) n FROM events WHERE app_id=? GROUP BY source_class", (a["id"],))]
    return apps


def _own_app(conn, user_id: int, app_id: int):
    row = conn.execute("SELECT * FROM apps WHERE id=? AND user_id=?", (app_id, user_id)).fetchone()
    if not row:
        raise HTTPException(404, "App not found")
    return dict(row)


@router.patch("/apps/{app_id}")
def patch_app(app_id: int, body: AppPatch, user=Depends(current_user)):
    with tx() as conn:
        _own_app(conn, user["id"], app_id)
        if body.name:
            conn.execute("UPDATE apps SET name=? WHERE id=?", (body.name[:60], app_id))
        if body.enabled is not None:
            conn.execute("UPDATE apps SET enabled=? WHERE id=?", (int(body.enabled), app_id))
        if body.clear_budget:
            conn.execute("UPDATE apps SET budget_usd=NULL WHERE id=?", (app_id,))
        elif body.budget_usd is not None:
            conn.execute("UPDATE apps SET budget_usd=? WHERE id=?", (body.budget_usd, app_id))
    return {"ok": True}


@router.post("/apps/{app_id}/rotate")
def rotate_app(app_id: int, request: Request, user=Depends(current_user)):
    vkey = new_virtual_key()
    with tx() as conn:
        app = _own_app(conn, user["id"], app_id)
        conn.execute("UPDATE apps SET vkey_hash=?, vkey_hint=? WHERE id=?", (sha256(vkey), hint(vkey), app_id))
        k = dict(conn.execute("SELECT * FROM provider_keys WHERE id=?", (app["key_id"],)).fetchone())
    base = str(request.base_url).rstrip("/") + "/v1"
    return {"virtual_key": vkey, "snippets": _snippets(vkey, base, k["provider"], None)}


@router.delete("/apps/{app_id}")
def delete_app(app_id: int, user=Depends(current_user)):
    with tx() as conn:
        _own_app(conn, user["id"], app_id)
        conn.execute("DELETE FROM apps WHERE id=?", (app_id,))
    return {"ok": True}


# ---------------- stats ----------------

def _events(conn, user_id: int, since: float, key_id: int | None, app_id: int | None = None):
    sql = "SELECT * FROM events WHERE user_id=? AND ts>?"
    args: list = [user_id, since]
    if key_id:
        sql += " AND key_id=?"
        args.append(key_id)
    if app_id:
        sql += " AND app_id=?"
        args.append(app_id)
    return rows(conn.execute(sql + " ORDER BY ts", args))


def _bucket_size(span: float) -> int:
    return 300 if span <= 3600 else 3600 if span <= 86400 else 6 * 3600 if span <= 7 * 86400 else 86400


@router.get("/overview")
def overview(window: str = Query("24h", alias="range"), key_id: int | None = None, user=Depends(current_user)):
    span = RANGES.get(window, 86400)
    now = time.time()
    with tx() as conn:
        ev = _events(conn, user["id"], now - span, key_id)
        prev = _events(conn, user["id"], now - 2 * span, key_id)
        prev = [e for e in prev if e["ts"] <= now - span]
        apps = {a["id"]: a["name"] for a in rows(conn.execute("SELECT id, name FROM apps WHERE user_id=?", (user["id"],)))}
        keys = rows(conn.execute("SELECT id, label, provider, masked FROM provider_keys WHERE user_id=?", (user["id"],)))
        latest_snaps = rows(conn.execute(
            """SELECT s.* FROM ratelimit_snapshots s JOIN provider_keys k ON k.id=s.key_id
               WHERE k.user_id=? AND s.id IN (SELECT MAX(id) FROM ratelimit_snapshots GROUP BY key_id, model)""",
            (user["id"],)))
        ghost_total = 0
        for k in keys:
            if key_id and k["id"] != key_id:
                continue
            for m in {s["model"] for s in latest_snaps if s["key_id"] == k["id"]}:
                snaps = rows(conn.execute("SELECT * FROM ratelimit_snapshots WHERE key_id=? AND model=? AND ts>? ORDER BY ts",
                                          (k["id"], m, now - 86400)))
                own = [e["ts"] for e in _events(conn, user["id"], now - 86400, k["id"]) if e["model"] == m]
                ghost_total += ghost_analysis(snaps, own)["ghost_requests"]

    ok = [e for e in ev if (e["status"] or 0) < 400]
    lat = [e["latency_ms"] for e in ok if e["latency_ms"]]
    headroom = None
    for s in latest_snaps:
        if key_id and s["key_id"] != key_id:
            continue
        if s["limit_requests"] and s["remaining_requests"] is not None:
            pct = 100 * s["remaining_requests"] / s["limit_requests"]
            headroom = pct if headroom is None else min(headroom, pct)

    def total(items, field):
        return sum(e[field] or 0 for e in items)

    kpis = {
        "requests": len(ev), "requests_prev": len(prev),
        "tokens_in": total(ev, "prompt_tokens"), "tokens_out": total(ev, "completion_tokens"),
        "tokens_reasoning": total(ev, "reasoning_tokens"),
        "tokens_prev": total(prev, "prompt_tokens") + total(prev, "completion_tokens"),
        "actual_cost": total(ev, "actual_cost_usd"), "list_cost": total(ev, "list_cost_usd"),
        "latency_p50": statistics.median(lat) if lat else None,
        "latency_p95": sorted(lat)[int(0.95 * (len(lat) - 1))] if lat else None,
        "error_rate": (len(ev) - len(ok)) / len(ev) if ev else 0,
        "headroom_pct": headroom, "ghost_requests": ghost_total,
        "apps": len({e["app_id"] for e in ev if e["app_id"]}),
        "sources": len({e["source_class"] for e in ev}),
        "ips": len({e["client_ip"] for e in ev if e["client_ip"]}),
    }

    size = _bucket_size(span)
    start = int((now - span) // size * size)
    series = {b: {"t": b, "requests": 0, "tokens_in": 0, "tokens_out": 0, "reasoning": 0,
                  "list_cost": 0.0, "actual_cost": 0.0, "errors": 0}
              for b in range(start, int(now) + size, size)}
    by_app_time = defaultdict(lambda: defaultdict(int))
    for e in ev:
        b = int(e["ts"] // size * size)
        if b not in series:
            continue
        s = series[b]
        s["requests"] += 1
        s["tokens_in"] += e["prompt_tokens"] or 0
        s["tokens_out"] += (e["completion_tokens"] or 0) - (e["reasoning_tokens"] or 0)
        s["reasoning"] += e["reasoning_tokens"] or 0
        s["list_cost"] += e["list_cost_usd"] or 0
        s["actual_cost"] += e["actual_cost_usd"] or 0
        s["errors"] += 1 if (e["status"] or 0) >= 400 else 0
        by_app_time[b][apps.get(e["app_id"], "KeyLens probe" if e["source_class"] == "probe" else "unknown")] += 1

    app_names = sorted({n for d in by_app_time.values() for n in d})
    app_series = [{"t": b, **{n: by_app_time[b].get(n, 0) for n in app_names}} for b in sorted(series)]

    def group(field, label_fn=lambda v: v or "unknown"):
        agg = defaultdict(lambda: {"requests": 0, "tokens": 0, "list_cost": 0.0, "errors": 0})
        for e in ev:
            g = agg[label_fn(e[field])]
            g["requests"] += 1
            g["tokens"] += (e["prompt_tokens"] or 0) + (e["completion_tokens"] or 0)
            g["list_cost"] += e["list_cost_usd"] or 0
            g["errors"] += 1 if (e["status"] or 0) >= 400 else 0
        return sorted(({"name": k, **v} for k, v in agg.items()), key=lambda x: -x["requests"])

    latency_by_model = []
    for model in {e["model"] for e in ok if e["model"]}:
        items = [e for e in ok if e["model"] == model]
        q = [e["queue_ms"] for e in items if e["queue_ms"] is not None]
        c = [e["compute_ms"] for e in items if e["compute_ms"] is not None]
        t = [e["latency_ms"] for e in items if e["latency_ms"]]
        latency_by_model.append({
            "model": model,
            "queue_ms": statistics.median(q) if q else 0,
            "compute_ms": statistics.median(c) if c else 0,
            "other_ms": max((statistics.median(t) if t else 0) - (statistics.median(q) if q else 0)
                            - (statistics.median(c) if c else 0), 0),
        })

    return {
        "range": window, "bucket_s": size, "kpis": kpis,
        "series": [series[b] for b in sorted(series)],
        "app_series": app_series, "app_names": app_names,
        "by_app": group("app_id", lambda v: apps.get(v, "KeyLens probe" if v is None else f"app #{v}")),
        "by_source": group("source_class"),
        "by_source_detail": group("source_detail")[:10],
        "by_model": group("model"),
        "by_region": group("region"),
        "by_ip": group("client_ip")[:10],
        "by_provider": group("provider"),
        "latency_by_model": latency_by_model,
        "limits": latest_snaps,
        "pricing": pricing.snapshot_info(),
    }


@router.get("/where")
def where_used(window: str = Query("7d", alias="range"), key_id: int | None = None, user=Depends(current_user)):
    """Key -> app -> source -> IP flows, for the 'where is my key used' map."""
    span = RANGES.get(window, 7 * 86400)
    with tx() as conn:
        ev = _events(conn, user["id"], time.time() - span, key_id)
        apps = {a["id"]: a["name"] for a in rows(conn.execute("SELECT id, name FROM apps WHERE user_id=?", (user["id"],)))}
        keys = {k["id"]: f"{k['label']} · {k['masked']}" for k in rows(conn.execute(
            "SELECT id, label, masked FROM provider_keys WHERE user_id=?", (user["id"],)))}
    flows = Counter()
    for e in ev:
        flows[(keys.get(e["key_id"], "?"), apps.get(e["app_id"], "KeyLens probe"),
               e["source_detail"] or e["source_class"] or "unknown", e["client_ip"] or "-")] += 1
    return [{"key": k, "app": a, "source": s, "ip": ip, "requests": n}
            for (k, a, s, ip), n in flows.most_common(100)]


@router.get("/events")
def list_events(limit: int = 200, key_id: int | None = None, app_id: int | None = None,
                source: str | None = None, errors_only: bool = False, user=Depends(current_user)):
    sql = """SELECT e.id, e.ts, e.provider, e.endpoint, e.model, e.stream, e.status, e.error_type,
                    e.source_class, e.source_detail, e.client_ip, e.origin, e.prompt_tokens,
                    e.completion_tokens, e.reasoning_tokens, e.cached_tokens, e.latency_ms, e.ttfb_ms,
                    e.queue_ms, e.compute_ms, e.region, e.finish_reason, e.list_cost_usd,
                    e.actual_cost_usd, e.upstream_id, a.name app_name, k.label key_label
             FROM events e LEFT JOIN apps a ON a.id=e.app_id JOIN provider_keys k ON k.id=e.key_id
             WHERE e.user_id=?"""
    args: list = [user["id"]]
    if key_id:
        sql += " AND e.key_id=?"
        args.append(key_id)
    if app_id:
        sql += " AND e.app_id=?"
        args.append(app_id)
    if source:
        sql += " AND e.source_class=?"
        args.append(source)
    if errors_only:
        sql += " AND e.status>=400"
    sql += " ORDER BY e.ts DESC LIMIT ?"
    args.append(min(limit, 1000))
    with tx() as conn:
        return rows(conn.execute(sql, args))


@router.get("/events/{event_id}")
def event_detail(event_id: int, user=Depends(current_user)):
    with tx() as conn:
        row = conn.execute("SELECT * FROM events WHERE id=? AND user_id=?", (event_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Event not found")
    out = dict(row)
    out["raw_meta"] = jload(out["raw_meta"], {})
    return out


@router.get("/insights")
def get_insights(user=Depends(current_user)):
    with tx() as conn:
        return insights.build(conn, user["id"])


@router.get("/export.csv")
def export_csv(window: str = Query("30d", alias="range"), user=Depends(current_user)):
    with tx() as conn:
        ev = _events(conn, user["id"], time.time() - RANGES.get(window, 30 * 86400), None)
    cols = ["ts", "provider", "model", "app_id", "source_class", "source_detail", "client_ip", "status",
            "prompt_tokens", "completion_tokens", "reasoning_tokens", "latency_ms", "list_cost_usd",
            "actual_cost_usd", "region", "upstream_id"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(cols)
    for e in ev:
        w.writerow([e[c] for c in cols])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": "attachment; filename=keylens-events.csv"})


# ---------------- scanner + meta ----------------

class ScanIn(BaseModel):
    path: str = Field(default="~/Desktop", max_length=500)


@router.post("/scan")
def scan_files(body: ScanIn, request: Request, user=Depends(current_user)):
    if request.client and request.client.host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "The file scanner only runs for local requests.")
    with tx() as conn:
        keys = rows(conn.execute("SELECT key_enc FROM provider_keys WHERE user_id=?", (user["id"],)))
    stored = {}
    for k in keys:
        raw = decrypt_key(k["key_enc"])
        stored[fingerprint(raw)] = raw
    return scanner.scan(body.path, stored)


@router.get("/meta")
def meta():
    return {
        "providers": [{"id": p.id, "name": p.name, "notes": p.notes} for p in PROVIDERS.values()],
        "pricing": pricing.snapshot_info(),
        "privacy": {
            "stored": ["timestamps", "model", "token counts", "latency", "status", "cost",
                       "app", "source class", "client IP", "user agent", "origin",
                       "rate-limit headers", "provider request id", "request params (numbers only)"],
            "never_stored": ["prompt text", "response text", "system prompts", "tool arguments",
                             "file contents", "your raw provider key (encrypted at rest)"],
        },
    }
