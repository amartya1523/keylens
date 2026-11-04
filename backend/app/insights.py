"""Rule-based insights. Each card says what was found, why it matters,
how sure we are, and what to do. No ML, so every number can be explained."""
import statistics
import time

from . import pricing
from .db import rows
from .ratelimits import REQUEST_WINDOW_S, forecast, ghost_analysis

# Smaller, cheaper sibling for "you could downgrade" hints (same provider).
_CHEAPER = {
    ("groq", "openai/gpt-oss-120b"): "openai/gpt-oss-20b",
    ("groq", "qwen/qwen3.8-27b"): "openai/gpt-oss-20b",
    ("xai", "grok-4.7"): "grok-4.3",
    ("xai", "grok-4.6"): "grok-4.3",
    ("openai", "gpt-4o"): "gpt-4o-mini",
    ("openai", "gpt-4.1"): "gpt-4.1-mini",
}


def _card(kind, severity, title, body, confidence, action, metric=None):
    return {"kind": kind, "severity": severity, "title": title, "body": body,
            "confidence": confidence, "action": action, "metric": metric}


def ghost_by_model(conn, user_id: int, key: dict, now: float | None = None) -> dict:
    """Ghost analysis per model for one key over the last 24h of snapshots.

    Own requests are read from 48h back (the first snapshot's deficit still contains
    earlier KeyLens traffic) and only those that reached the provider are counted.
    """
    now = now or time.time()
    window = REQUEST_WINDOW_S.get(key["provider"])
    snaps_all = rows(conn.execute(
        "SELECT * FROM ratelimit_snapshots WHERE key_id=? AND ts>? ORDER BY ts", (key["id"], now - 86400)))
    own_all = rows(conn.execute(
        """SELECT model, ts FROM events WHERE user_id=? AND key_id=? AND ts>?
           AND status IS NOT NULL AND status < 500 AND status != 429""",
        (user_id, key["id"], now - 2 * 86400)))
    out = {}
    for model in sorted({s["model"] for s in snaps_all if s["model"]}):
        snaps = [s for s in snaps_all if s["model"] == model]
        own = [e["ts"] for e in own_all if e["model"] == model]
        out[model] = ghost_analysis(snaps, own, window)
    return out


def latest_forecast(conn, user_id: int, key: dict, now: float | None = None) -> dict | None:
    """Forecast for the model whose daily bucket is tightest."""
    now = now or time.time()
    window = REQUEST_WINDOW_S.get(key["provider"])
    if not window:
        return None
    snaps = rows(conn.execute(
        """SELECT * FROM ratelimit_snapshots WHERE key_id=? AND id IN
             (SELECT MAX(id) FROM ratelimit_snapshots WHERE key_id=? GROUP BY model)""",
        (key["id"], key["id"])))
    best = None
    for s in snaps:
        ts = [r[0] for r in conn.execute(
            "SELECT ts FROM events WHERE user_id=? AND key_id=? AND model=? AND ts>?",
            (user_id, key["id"], s["model"], min(s["ts"], now - 3600)))]
        fc = forecast(ts, s, now, window)
        if fc and (best is None or fc["remaining"] < best["remaining"]):
            best = fc
    return best


def build(conn, user_id: int) -> list[dict]:
    now = time.time()
    cards: list[dict] = []
    keys = rows(conn.execute("SELECT * FROM provider_keys WHERE user_id=?", (user_id,)))
    week = now - 7 * 86400

    for key in keys:
        ev = rows(conn.execute(
            "SELECT * FROM events WHERE user_id=? AND key_id=? AND ts>? ORDER BY ts",
            (user_id, key["id"], week)))
        label = f"{key['label']} ({key['masked']})"

        # 1. Ghost usage: provider counters moved more than KeyLens traffic explains.
        for model, g in ghost_by_model(conn, user_id, key, now).items():
            if g["ghost_requests"] > 0:
                cards.append(_card(
                    "ghost", "critical",
                    f"~{g['ghost_requests']} requests on {model} did not go through KeyLens",
                    f"The provider's rate-limit counter for {label} dropped more than the "
                    f"traffic KeyLens proxied. Another app, another key in the same org, or a "
                    f"leaked key used it. This is a lower bound.",
                    g["confidence"], "Find the caller, or rotate the key if you can't explain it.",
                    g["ghost_requests"]))

        # 2. Daily quota forecast.
        fc = latest_forecast(conn, user_id, key, now)
        if fc and fc.get("exhausts_in_h") is not None and fc["exhausts_in_h"] < 24:
            cards.append(_card(
                "quota", "warning", f"Daily request limit runs out in ~{fc['exhausts_in_h']} h",
                f"{label}: {fc['message']} {fc['remaining']} of {fc['limit']} left.",
                "medium", "Spread batch jobs out, or move to a paid tier.", fc["exhausts_in_h"]))
        elif fc and fc["limit"] and fc["remaining"] / fc["limit"] <= 0.2:
            used_pct = 100 * (1 - fc["remaining"] / fc["limit"])
            cards.append(_card(
                "quota", "warning", f"{used_pct:.0f}% of the daily request limit used",
                f"{label} has about {fc['remaining']} of {fc['limit']} requests left for {fc['model']}.",
                "high", "Slow down non-urgent traffic.", round(used_pct)))

        if not ev:
            last = conn.execute("SELECT MAX(ts) FROM events WHERE user_id=? AND key_id=?",
                                (user_id, key["id"])).fetchone()[0]
            if key["created_at"] < now - 7 * 86400 and (not last or last < week):
                cards.append(_card(
                    "unused", "info", f"{key['label']} unused for 7+ days",
                    "A key nobody uses is pure risk.", "high", "Revoke it at the provider."))
            continue

        # 3. Cheaper model for short answers.
        by_model: dict[str, list] = {}
        for e in ev:
            if e["status"] == 200 and e["model"]:
                by_model.setdefault(e["model"], []).append(e)
        for model, items in by_model.items():
            alt = _CHEAPER.get((key["provider"], model))
            if not alt or len(items) < 5:
                continue
            med_out = statistics.median(i["completion_tokens"] for i in items)
            if med_out > 200:
                continue
            now_cost = sum(i["list_cost_usd"] for i in items)
            alt_cost = sum(pricing.list_cost(key["provider"], alt, i["prompt_tokens"],
                                             i["completion_tokens"], i["cached_tokens"])[0]
                           for i in items)
            saving = now_cost - alt_cost
            if saving > 0:
                monthly = saving / 7 * 30
                cards.append(_card(
                    "downgrade", "info",
                    f"Try {alt} for short {model} answers (~${monthly:.4f}/mo at list price)",
                    f"{len(items)} calls this week had a median output of {med_out:.0f} tokens. "
                    f"That pattern often fits a smaller model. This is a hypothesis: KeyLens never "
                    f"sees the content, so test quality on your own examples first.",
                    "low", f"Run a side-by-side test of {model} vs {alt}.", round(monthly, 6)))

        # 4. Reasoning-heavy output.
        out_tokens = sum(e["completion_tokens"] for e in ev)
        reasoning = sum(e["reasoning_tokens"] for e in ev)
        if out_tokens > 500 and reasoning / out_tokens > 0.6:
            cards.append(_card(
                "reasoning", "info", f"{100 * reasoning / out_tokens:.0f}% of output tokens are hidden reasoning",
                f"{label}: you pay for {reasoning:,} reasoning tokens that users never see.",
                "high", "Set reasoning_effort to low, or cap max_tokens.",
                round(100 * reasoning / out_tokens)))

        # 5. Queue time dominates latency (Groq reports queue vs compute).
        q = [e["queue_ms"] for e in ev if e["queue_ms"]]
        c = [e["compute_ms"] for e in ev if e["compute_ms"]]
        if len(q) >= 5 and statistics.median(q) > 2 * max(statistics.median(c), 1):
            cards.append(_card(
                "queue", "info", "Most latency is waiting in the provider's queue",
                f"Median queue {statistics.median(q):.0f} ms vs compute {statistics.median(c):.0f} ms. "
                f"Your code is not the slow part.", "high",
                "Retry at a quieter time, or use a paid / flex tier."))

        # 6. Error spike.
        recent = [e for e in ev if e["ts"] > now - 3600]
        errs = [e for e in recent if (e["status"] or 0) >= 400]
        if len(recent) >= 5 and len(errs) / len(recent) > 0.2:
            top = max({e["error_type"] or str(e["status"]) for e in errs},
                      key=lambda t: sum(1 for e in errs if (e["error_type"] or str(e["status"])) == t))
            cards.append(_card(
                "errors", "warning", f"{100 * len(errs) / len(recent):.0f}% of requests failed in the last hour",
                f"Most common error on {label}: {top}.", "high", "Open the request log filtered to errors."))

        # 7. New source IP for an app.
        seen_before = {(e["app_id"], e["client_ip"]) for e in ev if e["ts"] < now - 86400}
        if seen_before:
            fresh = {(e["app_id"], e["client_ip"]) for e in ev if e["ts"] >= now - 86400} - seen_before
            for app_id, ip in sorted(fresh, key=str)[:3]:
                cards.append(_card(
                    "new_ip", "warning", f"New caller IP {ip}",
                    f"App #{app_id} on {label} was called from an IP not seen in the past week.",
                    "medium", "Confirm it is yours, or pause the app."))

        # 8. Free tier: what this would cost on paid.
        free_value = sum(e["list_cost_usd"] for e in ev
                         if e["actual_cost_usd"] == 0 and e["source_class"] != "probe")
        if free_value > 0:
            cards.append(_card(
                "free_value", "success",
                f"Free tier covered ${free_value:.4f} of usage this week",
                f"At {key['provider']}'s list prices these {len(ev)} requests would have cost "
                f"${free_value:.4f}. Projected per month: ${free_value / 7 * 30:.4f}.",
                "high", "Plan the budget you would need on a paid tier.", round(free_value, 6)))

    order = {"critical": 0, "warning": 1, "info": 2, "success": 3}
    return sorted(cards, key=lambda c: order[c["severity"]])
