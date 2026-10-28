"""TID (timestamp identifier) generation and parsing.

A TID is a 64-bit value — microseconds since the UNIX epoch (53 bits, top bit
zero) followed by a 10-bit clock identifier — encoded as 13 characters of
base32-sortable. TIDs are the record-key scheme for most collections and the
``rev`` clock for repo commits.
"""

from __future__ import annotations

import os
import threading
import time

from fastatp.lex.types import validate_tid

BASE32_SORTABLE = "234567abcdefghijklmnopqrstuvwxyz"
_CHAR_VALUE = {c: i for i, c in enumerate(BASE32_SORTABLE)}

__all__ = ["TidClock", "decode_tid", "encode_tid", "generate_tid", "tid_clock"]


def encode_tid(timestamp_us: int, clock_id: int) -> str:
    """Encode microseconds-since-epoch + 10-bit clock id as a 13-char TID."""
    if not 0 <= timestamp_us < 1 << 53:
        raise ValueError("TID timestamp must fit in 53 bits")
    if not 0 <= clock_id < 1 << 10:
        raise ValueError("TID clock id must fit in 10 bits")
    value = (timestamp_us << 10) | clock_id
    chars = []
    for shift in range(60, -1, -5):
        chars.append(BASE32_SORTABLE[(value >> shift) & 0x1F])
    return "".join(chars)


def decode_tid(tid: str) -> tuple[int, int]:
    """Parse a TID string into (timestamp_us, clock_id)."""
    validate_tid(tid)
    value = 0
    for char in tid:
        value = (value << 5) | _CHAR_VALUE[char]
    return value >> 10, value & 0x3FF


class TidClock:
    """Monotonic TID source: strictly increasing even within one microsecond."""

    def __init__(self, clock_id: int | None = None) -> None:
        self._clock_id = (
            clock_id if clock_id is not None else int.from_bytes(os.urandom(2), "big") % 1024
        )
        self._last_us = 0
        self._lock = threading.Lock()

    @property
    def clock_id(self) -> int:
        return self._clock_id

    def now(self) -> str:
        with self._lock:
            timestamp_us = max(time.time_ns() // 1000, self._last_us + 1)
            self._last_us = timestamp_us
            return encode_tid(timestamp_us, self._clock_id)


tid_clock = TidClock()
"""Process-wide default clock."""


def generate_tid() -> str:
    """A fresh, monotonically-increasing TID from the process-wide clock."""
    return tid_clock.now()
