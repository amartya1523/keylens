"""Send real traffic through KeyLens from different kinds of clients, so the
dashboard has something true to show. Optionally send a few calls straight to
the provider (skipping KeyLens) to demonstrate ghost-usage detection.

    python scripts/demo_traffic.py --vkey kl_live_... [--vkey kl_live_...]
                                   [--rounds 3] [--ghost 4]

--ghost N needs DEMO_GROQ_KEY in backend/.env (the real provider key).
Free-tier friendly: about one request every 2.5 s, short answers only.
"""
import argparse
import os
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402,F401  (loads backend/.env)

PROMPTS = ["Name one planet.", "Give me a synonym for fast.", "What is 12*7?",
           "One word for happy.", "Capital of Japan?", "Write a 5-word slogan for tea."]
MODELS = ["openai/gpt-oss-20b", "openai/gpt-oss-20b", "openai/gpt-oss-120b"]


def via_openai_sdk(base, vkey, model, prompt, stream=False):
    from openai import OpenAI
    client = OpenAI(base_url=f"{base}/v1", api_key=vkey)
    if stream:
        for _ in client.chat.completions.create(model=model, stream=True, max_tokens=80,
                                                messages=[{"role": "user", "content": prompt}]):
            pass
        return "openai-sdk stream"
    client.chat.completions.create(model=model, max_tokens=80,
                                   messages=[{"role": "user", "content": prompt}])
    return "openai-sdk"


def via_groq_sdk(base, vkey, model, prompt):
    from groq import Groq
    Groq(base_url=base, api_key=vkey).chat.completions.create(
        model=model, max_tokens=80, messages=[{"role": "user", "content": prompt}])
    return "groq-sdk"


def via_requests(base, vkey, model, prompt):
    import requests
    requests.post(f"{base}/v1/chat/completions", timeout=60,
                  headers={"Authorization": f"Bearer {vkey}"},
                  json={"model": model, "max_tokens": 80,
                        "messages": [{"role": "user", "content": prompt}]})
    return "python-requests"


def via_curl(base, vkey, model, prompt):
    body = '{"model":"%s","max_tokens":80,"messages":[{"role":"user","content":"%s"}]}' % (model, prompt)
    subprocess.run(["curl", "-s", "-o", "/dev/null", f"{base}/v1/chat/completions",
                    "-H", f"Authorization: Bearer {vkey}", "-H", "Content-Type: application/json",
                    "-d", body], check=False, timeout=60)
    return "curl"


def ghost_calls(n: int) -> None:
    """Calls that skip KeyLens on purpose. KeyLens should infer them from rate-limit headers."""
    import requests
    key = os.environ.get("DEMO_GROQ_KEY", "").strip()
    if not key:
        print("skip ghost calls: set DEMO_GROQ_KEY in backend/.env")
        return
    for i in range(n):
        requests.post("https://api.groq.com/openai/v1/chat/completions", timeout=60,
                      headers={"Authorization": f"Bearer {key}"},
                      json={"model": "openai/gpt-oss-20b", "max_tokens": 5,
                            "messages": [{"role": "user", "content": "hi"}]})
        print(f"  ghost call {i + 1}/{n} sent directly to Groq (KeyLens did not see it)")
        time.sleep(2.5)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--vkey", action="append", required=True, help="KeyLens virtual key (repeatable)")
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--ghost", type=int, default=0)
    args = ap.parse_args()

    senders = [via_openai_sdk, via_groq_sdk, via_requests, via_curl,
               lambda b, v, m, p: via_openai_sdk(b, v, m, p, stream=True)]
    for r in range(args.rounds):
        for vkey in args.vkey:
            for send in senders:
                model, prompt = random.choice(MODELS), random.choice(PROMPTS)
                try:
                    label = send(args.base, vkey, model, prompt)
                    print(f"round {r + 1}: {label:18} {model:22} via {vkey[:12]}…")
                except Exception as exc:  # keep going; errors show up in the dashboard too
                    print(f"round {r + 1}: error {type(exc).__name__}: {str(exc)[:80]}")
                time.sleep(2.5)
    if args.ghost:
        ghost_calls(args.ghost)
        # one more proxied call so KeyLens reads fresh rate-limit headers after the ghosts
        via_requests(args.base, args.vkey[0], "openai/gpt-oss-20b", "ok")
        print("sent one proxied call to refresh rate-limit headers")


if __name__ == "__main__":
    main()
