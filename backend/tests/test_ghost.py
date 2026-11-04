"""Ghost-usage inference against a simulated Groq bucket (1,000 requests/day,
whole-request refills every 86.4 s, reported as whole requests).

The estimate must never exceed the real number of outside requests, and must be
exact when KeyLens sees traffic close to the outside calls.

    cd backend && .venv/bin/python tests/test_ghost.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ratelimits import ghost_analysis  # noqa: E402

L = 1000
R = L / 86400


def simulate(pattern: list[str], phase: float = 0.0, step: float = 5.0) -> int:
    """pattern items: 'own' (via KeyLens), 'ghost' (direct), 'idle:<seconds>'."""
    t = last = 0.0
    tokens, deficit = phase, 0
    snaps, own = [], []
    for item in pattern:
        if item.startswith("idle:"):
            t += float(item[5:])
            continue
        t += step
        tokens += R * (t - last)
        last = t
        refill = math.floor(tokens)
        tokens -= refill
        deficit = max(0, deficit - refill) + 1
        if item == "own":
            own.append(t - 0.3)
            snaps.append({"ts": t, "limit_requests": L, "remaining_requests": L - deficit,
                          "reset_requests_s": deficit / R})
    return ghost_analysis(snaps, own)["ghost_requests"]


def test_no_false_ghosts():
    for phase in (0.0, 0.3, 0.7, 0.99):
        for idle in (60, 600, 3600, 20000):
            got = simulate(["own"] * 6 + [f"idle:{idle}"] + ["own"] * 6, phase)
            assert got == 0, (phase, idle, got)


def test_exact_when_close():
    for n in range(1, 11):
        got = simulate(["own"] * 5 + ["ghost"] * n + ["own"] * 2)
        assert got == n, (n, got)


def test_lower_bound_after_idle():
    for phase in (0.0, 0.5, 0.95):
        for n in range(1, 6):
            got = simulate(["own"] * 5 + ["ghost"] * n + ["own"] * 2 + ["idle:600"] +
                           ["own"] * 3 + ["ghost"] * n + ["own"], phase)
            assert 2 * n - 1 <= got <= 2 * n, (phase, n, got)


def test_unsupported_short_window():
    r = ghost_analysis([{"ts": 1, "limit_requests": 500, "remaining_requests": 1}], [], 60)
    assert r["confidence"] == "unsupported" and r["ghost_requests"] == 0


if __name__ == "__main__":
    test_no_false_ghosts()
    test_exact_when_close()
    test_lower_bound_after_idle()
    test_unsupported_short_window()
    print("ghost tests passed: no false ghosts, exact for close readings, lower bound after idle gaps")
