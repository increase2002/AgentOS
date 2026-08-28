"""Token-bucket rate limiter.

Used by the OpenClaw D2 sidecar (examples/openclaw_sidecar.py) to cap
the rate at which we issue LLM calls in response to bus traffic. The
sidecar runs 24/7, so without rate limiting a burst of bus messages
(Codex status floods,老大 catching up on 5 days of silent messages)
would translate 1:1 into token-burning LLM calls.

Design notes
------------

- **Token bucket**: classic. ``capacity`` is the max burst size,
  ``refill_rate`` is tokens-per-second. Each LLM call costs 1 token.
  When the bucket is empty, ``try_consume()`` returns False and the
  caller (sidecar) sends a ``HANDOFF`` with ``rate_limited=True`` so
  the sender knows to back off.

- **Thread-safe**: a single ``threading.Lock`` guards both refill and
  consume. The sidecar calls from a single asyncio task, but the
  refill could theoretically happen from a timer thread if we ever
  add one — keep the lock for forward-compat.

- **Lazy refill**: we only refill on ``try_consume()`` (no background
  thread). Cheap, drift-free, and the test-suite-friendly.

- **No clock dependency**: uses ``time.monotonic()``. We never go
  backwards even if the system clock jumps.

This module has zero LLM / bus dependencies — it is a pure utility,
unit-tested in isolation in ``tests/test_token_bucket.py``.
"""

from __future__ import annotations

import threading
import time
from typing import NamedTuple


class RateLimitDecision(NamedTuple):
    """Result of a single ``try_consume()`` call.

    Attributes
    ----------
    allowed:
        True if a token was consumed, False if the bucket was empty.
    retry_after_s:
        Seconds the caller should wait before retrying. 0.0 when
        ``allowed`` is True. Always > 0 when ``allowed`` is False.
    tokens_remaining:
        Tokens left in the bucket after this call (0 when not allowed,
        decremented by 1 when allowed).
    """

    allowed: bool
    retry_after_s: float
    tokens_remaining: float


class TokenBucket:
    """Thread-safe lazy-refill token bucket.

    Parameters
    ----------
    capacity:
        Maximum number of tokens (max burst size). Must be >= 1.
    refill_rate:
        Tokens added per second. Must be > 0.
    clock:
        Override for tests. Defaults to ``time.monotonic``.
    """

    def __init__(
        self,
        capacity: int,
        refill_rate: float,
        *,
        clock: "type[time.monotonic]" = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError(f"capacity must be >= 1, got {capacity}")
        if refill_rate <= 0:
            raise ValueError(f"refill_rate must be > 0, got {refill_rate}")
        self._capacity: float = float(capacity)
        self._refill_rate: float = float(refill_rate)
        self._tokens: float = self._capacity  # start full
        self._last_refill: float = clock()
        self._lock: threading.Lock = threading.Lock()
        self._clock = clock

    # ----- introspection ------------------------------------------------ #

    @property
    def capacity(self) -> float:
        """Max burst size (tokens)."""
        return self._capacity

    @property
    def refill_rate(self) -> float:
        """Tokens added per second."""
        return self._refill_rate

    @property
    def tokens(self) -> float:
        """Approximate current token count (refills before returning).

        Useful for logging / metrics; not authoritative for fast paths
        because the lock is dropped immediately. Always call
        ``try_consume()`` for the real decision.
        """
        with self._lock:
            self._refill()
            return self._tokens

    # ----- public API --------------------------------------------------- #

    def try_consume(self, n: int = 1) -> RateLimitDecision:
        """Attempt to consume ``n`` tokens.

        Returns a ``RateLimitDecision`` with ``allowed`` set True if
        tokens were consumed, False otherwise. When not allowed,
        ``retry_after_s`` is the wait time until 1 token is available.
        """
        if n < 1:
            raise ValueError(f"n must be >= 1, got {n}")

        with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return RateLimitDecision(
                    allowed=True,
                    retry_after_s=0.0,
                    tokens_remaining=self._tokens,
                )
            # Compute wait time for 1 token (or n tokens, same idea)
            deficit = n - self._tokens
            retry_after_s = deficit / self._refill_rate
            return RateLimitDecision(
                allowed=False,
                retry_after_s=retry_after_s,
                tokens_remaining=self._tokens,
            )

    # ----- internal ----------------------------------------------------- #

    def _refill(self) -> None:
        """Add tokens since the last refill call. Caller holds the lock."""
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed <= 0:
            return
        added = elapsed * self._refill_rate
        self._tokens = min(self._capacity, self._tokens + added)
        self._last_refill = now


__all__ = ["TokenBucket", "RateLimitDecision"]