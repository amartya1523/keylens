"""Rate-limit headers -> headroom, exhaustion forecast and "ghost usage".

Groq's request limit is a token bucket of size L (requests per day) that refills
continuously at r = L / 86400 per second. x-ratelimit-reset-requests is the time
until the bucket is full again, so the bucket's current deficit is reset_s * r.
(Live check on 2026-11-04: L=1000, one request -> reset 1m26.4s = 86.4 s, and
86.4 s * 1000/86400 = 1.0 request. The model fits exactly.)

KeyLens also simulates the same bucket using only the requests it proxied.
If the provider's deficit is bigger than ours, the difference was spent
somewhere KeyLens did not see: another app, another key in the org, or a leak.
"""
import re
import time

_DUR = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def parse_duration(value: str | None) -> float | None:
    """'1m26.4s' -> 86.4, '7.66s' -> 7.66, '2h1m' -> 7260, '120ms' -> 0.12."""
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    m = _DUR.match(value)
    if not m or not any(m.groups()):
        return None
    h, mins, s, ms = (float(x) if x else 0.0 for x in m.groups())
    return h * 3600 + mins * 60 + s + ms / 1000


def _int(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def snapshot_from_headers(headers) -> dict | None:
    h = {k.lower(): v for k, v in headers.items()}
    if "x-ratelimit-limit-requests" not in h and "x-ratelimit-limit-tokens" not in h:
        return None
    return {
        "limit_requests": _int(h.get("x-ratelimit-limit-requests")),
        "remaining_requests": _int(h.get("x-ratelimit-remaining-requests")),
        "reset_requests_s": parse_duration(h.get("x-ratelimit-reset-requests")),
        "limit_tokens": _int(h.get("x-ratelimit-limit-tokens")),
        "remaining_tokens": _int(h.get("x-ratelimit-remaining-tokens")),
        "reset_tokens_s": parse_duration(h.get("x-ratelimit-reset-tokens")),
    }


def ghost_analysis(snapshots: list[dict], own_request_ts: list[float],
                   window_s: float = 86400.0) -> dict:
    """Compare the provider's request bucket with a bucket fed only by our own requests.

    snapshots: ordered by ts, each with ts, limit_requests, remaining_requests, reset_requests_s
    own_request_ts: timestamps of requests KeyLens itself sent with this key+model
    Returns a conservative (lower-bound) count of requests made outside KeyLens.
    """
    snaps = [s for s in snapshots if s.get("limit_requests")]
    if not snaps:
        return {"ghost_requests": 0, "points": [], "confidence": "none"}

    own = sorted(own_request_ts)
    sim = 0.0          # our simulated deficit
    last_t = None
    ghost_total = 0.0
    points = []
    i = 0
    for s in snaps:
        t, L = s["ts"], s["limit_requests"]
        r = L / window_s
        # feed our own requests that happened up to (and including) this response
        while i < len(own) and own[i] <= t + 0.5:
            if last_t is not None:
                sim = max(0.0, sim - r * (own[i] - last_t))
            sim += 1
            last_t = own[i]
            i += 1
        if last_t is not None:
            sim = max(0.0, sim - r * (t - last_t))
        last_t = t

        observed = 0.0
        if s.get("reset_requests_s") is not None:
            observed = s["reset_requests_s"] * r
        if s.get("remaining_requests") is not None:
            # live check: limit 1000, remaining 999 right after our first request
            observed = max(observed, L - s["remaining_requests"])

        eps = 1.5      # rounding of 'remaining' + one request in flight
        gap = observed - sim
        if gap > eps:
            ghost_total += gap - eps
            sim = observed          # absorb so the same ghosts are not counted twice
        points.append({"ts": t, "provider_used": round(observed, 2),
                       "keylens_saw": round(sim, 2), "limit": L,
                       "remaining": s.get("remaining_requests")})

    return {
        "ghost_requests": int(ghost_total),
        "points": points[-200:],
        "confidence": "medium" if len(snaps) >= 3 else "low",
        "note": "Lower bound. Limits are per organization and model, so this counts "
                "any traffic in the same org that did not go through KeyLens.",
    }


def forecast(events_ts: list[float], latest: dict | None, now: float | None = None) -> dict | None:
    """When does the daily request budget run out at the last hour's pace?"""
    if not latest or not latest.get("limit_requests") or latest.get("remaining_requests") is None:
        return None
    now = now or time.time()
    last_hour = [t for t in events_ts if t > now - 3600]
    per_hour = len(last_hour)
    L = latest["limit_requests"]
    refill_per_hour = L / 24
    remaining = latest["remaining_requests"]
    net = per_hour - refill_per_hour
    if net <= 0:
        return {"per_hour": per_hour, "limit": L, "remaining": remaining,
                "exhausts_in_h": None, "message": "Current pace stays inside the daily limit."}
    hours = remaining / net
    return {"per_hour": per_hour, "limit": L, "remaining": remaining,
            "exhausts_in_h": round(hours, 1),
            "message": f"At {per_hour} requests/hour the daily limit runs out in ~{hours:.1f} h."}
