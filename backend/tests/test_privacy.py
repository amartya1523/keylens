"""The privacy promise as a test: a canary string sent through the proxy must
never appear anywhere in the database. Runs against a fake upstream, no network.

    cd backend && .venv/bin/python -m pytest -q tests   (or: python tests/test_privacy.py)
"""
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["KEYLENS_DB"] = tempfile.mktemp(suffix=".db")

import httpx  # noqa: E402

from app import proxy  # noqa: E402
from app.db import init_db, tx  # noqa: E402
from app.security import encrypt_key, new_virtual_key, sha256  # noqa: E402

CANARY_PROMPT = "CANARY-PROMPT-7f3a9c"
CANARY_REPLY = "CANARY-REPLY-b21e04"


def fake_upstream(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["messages"][0]["content"] == CANARY_PROMPT
    if body.get("stream"):
        sse = (
            f'data: {{"choices":[{{"delta":{{"content":"{CANARY_REPLY}"}}}}]}}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],'
            '"x_groq":{"usage":{"prompt_tokens":11,"completion_tokens":3,"queue_time":0.01}}}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, content=sse.encode(), headers={
            "content-type": "text/event-stream",
            "x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "999",
            "x-ratelimit-reset-requests": "1m26.4s"})
    return httpx.Response(200, json={
        "choices": [{"message": {"role": "assistant", "content": CANARY_REPLY}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 11, "completion_tokens": 3}},
        headers={"x-ratelimit-limit-requests": "1000", "x-ratelimit-remaining-requests": "998"})


def setup() -> str:
    init_db()
    vkey = new_virtual_key()
    with tx() as conn:
        conn.execute("INSERT INTO users (id, email, name, password_hash, created_at) VALUES (1,'t@t','t','x',0)")
        conn.execute("""INSERT INTO provider_keys (id, user_id, provider, label, key_enc, fingerprint, masked,
                        created_at) VALUES (1,1,'groq','test',?,'fp','gsk_…',0)""",
                     (encrypt_key("gsk_" + "x" * 52),))
        conn.execute("""INSERT INTO apps (user_id, key_id, name, vkey_hash, vkey_hint, created_at)
                        VALUES (1,1,'test',?,'kl…',0)""", (sha256(vkey),))
    return vkey


async def run() -> None:
    vkey = setup()
    proxy._client = httpx.AsyncClient(transport=httpx.MockTransport(fake_upstream))
    from app.main import app
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        for stream in (False, True):
            r = await c.post("/v1/chat/completions", headers={"Authorization": f"Bearer {vkey}"},
                             json={"model": "openai/gpt-oss-20b", "stream": stream,
                                   "messages": [{"role": "user", "content": CANARY_PROMPT}]})
            assert r.status_code == 200
            assert CANARY_REPLY in r.text  # the client still gets the full answer

    dump = "\n".join(sqlite3.connect(os.environ["KEYLENS_DB"]).iterdump())
    assert CANARY_PROMPT not in dump, "prompt text leaked into the database"
    assert CANARY_REPLY not in dump, "response text leaked into the database"
    n = sqlite3.connect(os.environ["KEYLENS_DB"]).execute("SELECT COUNT(*), SUM(prompt_tokens) FROM events").fetchone()
    assert n == (2, 22), n
    print("privacy canary test passed: 2 requests logged, 0 bytes of prompt or reply stored")


def test_no_content_persisted():
    asyncio.run(run())


if __name__ == "__main__":
    test_no_content_persisted()
