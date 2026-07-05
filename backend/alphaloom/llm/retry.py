"""Transport retry wrapper: long waits on 429, exponential backoff otherwise.

xf-yun (讯飞 Spark) MaaS throttles bursts (~4 rapid calls -> 429 code 11210,
AlphaLoom D3). Rate-limit waits are deliberately long; generic errors get a
short exponential ladder. Exhaustion re-raises the last exception.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from alphaloom.llm.client import Transport

RATE_LIMIT_WAITS = (15.0, 30.0, 60.0)
GENERIC_WAITS = (2.0, 4.0, 8.0)


def _is_rate_limit(exc: Exception) -> bool:
    if type(exc).__name__ == "RateLimitError":
        return True
    text = str(exc)
    return "429" in text or "11210" in text


def with_retry(
    transport: Transport,
    sleep: Callable[[float], None] = time.sleep,
) -> Transport:
    def send(request: dict[str, Any]) -> dict[str, Any]:
        rate_i = 0
        generic_i = 0
        while True:
            try:
                return transport(request)
            except Exception as exc:  # noqa: BLE001 - classified below, re-raised on exhaustion
                if _is_rate_limit(exc):
                    if rate_i >= len(RATE_LIMIT_WAITS):
                        raise
                    sleep(RATE_LIMIT_WAITS[rate_i])
                    rate_i += 1
                else:
                    if generic_i >= len(GENERIC_WAITS):
                        raise
                    sleep(GENERIC_WAITS[generic_i])
                    generic_i += 1

    return send
