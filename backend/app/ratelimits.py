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


# Length of the request-limit window per provider. Only long windows can reveal
# ghost traffic: on a per-minute bucket an outside request refills in ~0.1 s.
REQUEST_WINDOW_S = {"groq": 86400.0}
MIN_GHOST_WINDOW_S = 3600.0


def ghost_analysis(snapshots: list[dict], own_request_ts: list[float],
                   window_s: float | None = 86400.0) -> dict:
    """Compare the provider's request bucket with a bucket fed only by our own requests.

    snapshots: ordered by ts (the moment the provider's headers arrived), each with
               limit_requests, remaining_requests, reset_requests_s
    own_request_ts: start times of requests KeyLens itself sent with this key+model
                    and that the provider actually counted (no transport errors)
    Returns the number of requests the provider counted that KeyLens never sent.
    """
    if not window_s or window_s < MIN_GHOST_WINDOW_S:
        return {"ghost_requests": 0, "points": [], "confidence": "unsupported",
                "note": "This provider's request limit refills within minutes, so outside "
                        "usage can't be inferred from its counters."}
    snaps = [s for s in snapshots if s.get("limit_requests")]
    if not snaps:
        return {"ghost_requests": 0, "points": [], "confidence": "none"}

    own = sorted(own_request_ts)
    sim = 0.0          # our simulated deficit (requests not yet refilled)
    last_t = None
    ghost_total = 0
    points = []
    i = 0
    for n, s in enumerate(snaps):
        t, L = s["ts"], s["limit_requests"]
        r = L / window_s
        while i < len(own) and own[i] <= t:
            if last_t is not None:
                sim = max(0.0, sim - r * (own[i] - last_t))
            sim += 1
            last_t = own[i]
            i += 1
        if last_t is not None:
            sim = max(0.0, sim - r * (t - last_t))
        last_t = t

        # reset * r is precise to a fraction of a request; L - remaining is rounded up
        if s.get("reset_requests_s") is not None:
            observed = s["reset_requests_s"] * r
        elif s.get("remaining_requests") is not None:
            observed = float(L - s["remaining_requests"])
        else:
            continue

        gap = observed - sim
        saw = sim
        if n == 0:
            sim = observed            # first reading: anchor, traffic before it is unknown
        elif gap > 0.6:
            ghost_total += round(gap)
            sim = observed            # absorb so the same requests are not counted twice
        points.append({"ts": t, "provider_used": round(observed, 2),
                       "keylens_saw": round(saw, 2),
                       "limit": L, "remaining": s.get("remaining_requests")})

    return {
        "ghost_requests": int(ghost_total),
        "points": points[-200:],
        "confidence": "medium" if len(snaps) >= 3 else "low",
        "note": "Counts requests the provider saw that KeyLens did not, within the time the "
                "bucket takes to refill (about 86 s per request on a 1,000/day limit). Limits "
                "are per organization and model, so any traffic in the same org counts.",
    }


def forecast(events_ts: list[float], latest: dict | None, now: float | None = None,
             window_s: float = 86400.0) -> dict | None:
    """When does the request budget for latest['model'] run out at the last hour's pace?

    events_ts must be this model's own request times. 'remaining' is aged forward from
    the snapshot time: the bucket refilled since then, and our later requests used it.
    """
    if not latest or not latest.get("limit_requests") or latest.get("remaining_requests") is None:
        return None
    now = now or time.time()
    L = latest["limit_requests"]
    r = L / window_s
    since = [t for t in events_ts if t > latest["ts"]]
    remaining = min(L, latest["remaining_requests"] + (now - latest["ts"]) * r - len(since))
    remaining = max(0, int(remaining))
    per_hour = len([t for t in events_ts if t > now - 3600])
    net = per_hour - r * 3600
    model = latest.get("model") or "this model"
    if net <= 0:
        return {"per_hour": per_hour, "limit": L, "remaining": remaining, "model": model,
                "exhausts_in_h": None, "message": f"{model}: current pace stays inside the limit."}
    hours = remaining / net
    return {"per_hour": per_hour, "limit": L, "remaining": remaining, "model": model,
            "exhausts_in_h": round(hours, 1),
            "message": f"{model}: at {per_hour} requests/hour the limit runs out in ~{hours:.1f} h."}
