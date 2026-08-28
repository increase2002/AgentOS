"""Tests for ``agentos.core.token_bucket`` (pure utility, no LLM / no bus).

Covers the lazy-refill token bucket used by the OpenClaw D2 sidecar to
cap LLM-call rates.
"""

from __future__ import annotations

import sys
from pathlib import Path

# ensure src/ on path (mirrors other test files)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402

from agentos.core.token_bucket import RateLimitDecision, TokenBucket  # noqa: E402


class FakeClock:
    """Manual monotonic clock for deterministic tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ----- constructor guards --------------------------------------------- #


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError, match="capacity"):
        TokenBucket(capacity=0, refill_rate=1.0)


def test_refill_rate_must_be_positive() -> None:
    with pytest.raises(ValueError, match="refill_rate"):
        TokenBucket(capacity=1, refill_rate=0.0)
    with pytest.raises(ValueError, match="refill_rate"):
        TokenBucket(capacity=1, refill_rate=-1.0)


def test_starts_full() -> None:
    clock = FakeClock()
    b = TokenBucket(capacity=5, refill_rate=1.0, clock=clock)
    assert b.tokens == 5


# ----- basic consume --------------------------------------------------- #


def test_consume_succeeds_when_full() -> None:
    clock = FakeClock()
    b = TokenBucket(capacity=3, refill_rate=1.0, clock=clock)
    decision = b.try_consume()
    assert decision.allowed is True
    assert decision.retry_after_s == 0.0
    assert decision.tokens_remaining == 2


def test_consume_fails_when_empty() -> None:
    clock = FakeClock()
    b = TokenBucket(capacity=1, refill_rate=1.0, clock=clock)
    assert b.try_consume().allowed is True
    decision = b.try_consume()
    assert decision.allowed is False
    assert decision.retry_after_s > 0
    # refill_rate=1, deficit=1 → exactly 1.0s
    assert decision.retry_after_s == 1.0


def test_consume_multi_tokens() -> None:
    clock = FakeClock()
    b = TokenBucket(capacity=5, refill_rate=2.0, clock=clock)
    assert b.try_consume(n=3).allowed is True
    assert b.try_consume(n=3).allowed is False  # only 2 left


def test_consume_n_must_be_positive() -> None:
    b = TokenBucket(capacity=2, refill_rate=1.0)
    with pytest.raises(ValueError, match="n must be"):
        b.try_consume(n=0)


# ----- refill timing --------------------------------------------------- #


def test_refill_over_time() -> None:
    clock = FakeClock()
    b = TokenBucket(capacity=3, refill_rate=2.0, clock=clock)  # 2 tok/s
    # Drain
    assert b.try_consume().allowed is True
    assert b.try_consume().allowed is True
    assert b.try_consume().allowed is True
    assert b.try_consume().allowed is False  # empty

    # After 1s → +2 tokens
    clock.advance(1.0)
    assert b.tokens == pytest.approx(2.0)

    # After another 0.5s → +1 token, capped at capacity (3)
    clock.advance(0.5)
    assert b.tokens == pytest.approx(3.0)  # capped

    # Long idle → still capped at capacity
    clock.advance(100.0)
    assert b.tokens == 3.0


def test_refill_partial_then_consume() -> None:
    clock = FakeClock()
    b = TokenBucket(capacity=2, refill_rate=1.0, clock=clock)
    assert b.try_consume().allowed is True
    assert b.try_consume().allowed is True
    assert b.try_consume().allowed is False

    clock.advance(0.5)  # only 0.5 tokens refilled
    decision = b.try_consume()
    assert decision.allowed is False
    # deficit = 1 - 0.5 = 0.5, refill_rate = 1.0 → 0.5s
    assert decision.retry_after_s == pytest.approx(0.5)
    assert decision.tokens_remaining == pytest.approx(0.5)


def test_no_double_refill_on_consecutive_consume() -> None:
    """Two consumes in the same instant should not double-refill.

    Lazy refill: time has not advanced between the two calls, so the
    bucket should reflect only one refill.
    """
    clock = FakeClock()
    b = TokenBucket(capacity=5, refill_rate=1.0, clock=clock)
    assert b.try_consume().tokens_remaining == 4
    # No time advance → same call should see 4 left, not 4 + epsilon
    assert b.try_consume().tokens_remaining == 3


# ----- burst behavior -------------------------------------------------- #


def test_burst_then_drain() -> None:
    """First N calls (capacity) succeed back-to-back, then drain."""
    clock = FakeClock()
    b = TokenBucket(capacity=10, refill_rate=0.001, clock=clock)  # very slow
    allowed = 0
    for _ in range(15):
        if b.try_consume().allowed:
            allowed += 1
    assert allowed == 10  # only capacity bursts succeed


def test_high_rpm_60_default() -> None:
    """The sidecar's default (60 rpm = 1 tok/s) behaves as expected."""
    clock = FakeClock()
    b = TokenBucket(capacity=10, refill_rate=60 / 60.0, clock=clock)  # 1/s
    # Burst 10 succeed
    for _ in range(10):
        assert b.try_consume().allowed is True
    # Next one fails
    assert b.try_consume().allowed is False
    # After 1s, exactly 1 token available
    clock.advance(1.0)
    assert b.try_consume().allowed is True
    assert b.try_consume().allowed is False


# ----- thread safety (smoke test, not exhaustive) ---------------------- #


def test_thread_safe_smoke() -> None:
    """Concurrent consumes should not exceed capacity + refill."""
    import threading

    clock = FakeClock()
    b = TokenBucket(capacity=100, refill_rate=10.0, clock=clock)
    consumed = []
    consumed_lock = threading.Lock()

    def worker() -> None:
        for _ in range(50):
            d = b.try_consume()
            if d.allowed:
                with consumed_lock:
                    consumed.append(1)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # With capacity=100 and no time advance, max consumed is 100
    assert len(consumed) <= 100