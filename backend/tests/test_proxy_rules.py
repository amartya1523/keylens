"""Proxy rules: only inference endpoints, kill switch, budget, bad keys, odd bodies.
No network: the upstream is a mock.

    cd backend && .venv/bin/python tests/test_proxy_rules.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["KEYLENS_DB"] = tempfile.mktemp(suffix=".db")

import httpx  # noqa: E402

from app import proxy  # noqa: E402
from app.db import init_db, tx  # noqa: E402
from app.security import encrypt_key, new_virtual_key, sha256  # noqa: E402

CALLS: list[str] = []


def upstream(request: httpx.Request) -> httpx.Response:
    CALLS.append(request.url.path)
    return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
                                     "usage": {"prompt_tokens": 5, "completion_tokens": 1}})


def setup() -> str:
    init_db()
    vkey = new_virtual_key()
    with tx() as conn:
        conn.execute("INSERT INTO users (id, email, name, password_hash, created_at) VALUES (1,'t@t','t','x',0)")
        conn.execute("""INSERT INTO provider_keys (id, user_id, provider, label, key_enc, fingerprint, masked,
                        created_at) VALUES (1,1,'groq','test',?,'fp','gsk_…',0)""", (encrypt_key("gsk_" + "x" * 52),))
        conn.execute("""INSERT INTO apps (id, user_id, key_id, name, vkey_hash, vkey_hint, created_at)
                        VALUES (1,1,1,'test',?,'kl…',0)""", (sha256(vkey),))
    return vkey


async def run() -> None:
    vkey = setup()
    proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
    from app.main import app
    auth = {"Authorization": f"Bearer {vkey}"}
    body = {"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": "hi"}]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as c:
        ok = await c.post("/v1/chat/completions", headers=auth, json=body)
        assert ok.status_code == 200, ok.text
        groq_sdk = await c.post("/openai/v1/chat/completions", headers=auth, json=body)
        assert groq_sdk.status_code == 200

        for bad in ["/v1/files", "/v1/batches", "/v1/fine_tuning/jobs",
                    "/v1/chat/completions%23models", "/v1/..%2F..%2Fkey"]:
            r = await c.post(bad, headers=auth, json=body)
            assert r.status_code == 404, (bad, r.status_code)

        r = await c.post("/v1/chat/completions", headers=auth, content=b"[1,2]")
        assert r.status_code == 200, "a non-object JSON body must not crash the proxy"

        r = await c.post("/v1/chat/completions", headers={"Authorization": "Bearer nope"}, json=body)
        assert r.status_code == 401

        with tx() as conn:
            conn.execute("UPDATE apps SET enabled=0 WHERE id=1")
        r = await c.post("/v1/chat/completions", headers=auth, json=body)
        assert r.status_code == 403 and r.json()["error"]["type"] == "app_paused"

        with tx() as conn:
            conn.execute("UPDATE apps SET enabled=1, budget_usd=0 WHERE id=1")
        r = await c.post("/v1/chat/completions", headers=auth, json=body)
        assert r.status_code == 429 and r.json()["error"]["type"] == "budget_exceeded"

        r = await c.get("/health", headers={"Host": "evil.example"})
        assert r.status_code == 400, "unknown Host headers must be rejected (DNS rebinding)"

    assert all(p.endswith("/chat/completions") for p in CALLS), CALLS
    print(f"proxy rules test passed: {len(CALLS)} upstream calls, all to chat/completions")


def test_proxy_rules():
    asyncio.run(run())


if __name__ == "__main__":
    test_proxy_rules()
