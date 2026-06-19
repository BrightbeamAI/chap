"""
chap_coordinator.ids

ULID-format identifier generation for CHAP envelopes, tasks, and
artefacts. CHAP ids are 26-character Crockford base32 ULIDs.
Task / artefact / logical / workspace ids carry the
``tsk_`` / ``art_`` / ``lgl_`` / ``wsp_`` prefixes.

The factory supports a deterministic mode so demos and tests
produce byte-for-byte replayable evidence chains.
"""
from __future__ import annotations

import os
import time

CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # excludes I, L, O, U


def _encode(value: int, length: int) -> str:
    chars: list[str] = []
    for _ in range(length):
        value, rem = divmod(value, 32)
        chars.append(CROCKFORD[rem])
    return "".join(reversed(chars))


class IdFactory:
    """Generates valid ULID-format identifiers.

    In deterministic mode the timestamp advances by a fixed step and
    randomness is drawn from a seeded counter, so a given sequence of
    calls always yields the same ids.
    """

    def __init__(
        self,
        deterministic: bool = False,
        seed: int = 0,
        start_ms: int = 1_700_000_000_000,
    ):
        self.deterministic = deterministic
        self._counter = seed
        self._clock_ms = start_ms

    def now_ms(self) -> int:
        if self.deterministic:
            self._clock_ms += 1
            return self._clock_ms
        return int(time.time() * 1000)

    def _randomness(self) -> int:
        if self.deterministic:
            self._counter += 1
            # 80 bits derived deterministically from the counter
            return (self._counter * 0x9E3779B97F4A7C15) & ((1 << 80) - 1)
        return int.from_bytes(os.urandom(10), "big")

    def ulid(self) -> str:
        ts = self.now_ms() & ((1 << 48) - 1)
        rand = self._randomness()
        return _encode(ts, 10) + _encode(rand, 16)

    def envelope_id(self) -> str:
        return self.ulid()

    def task_id(self) -> str:
        return "tsk_" + self.ulid()

    def artefact_id(self) -> str:
        return "art_" + self.ulid()

    def logical_id(self) -> str:
        return "lgl_" + self.ulid()

    def workspace_id(self) -> str:
        return "wsp_" + self.ulid()

    def deliberation_id(self) -> str:
        return "dlb_" + self.ulid()

    def handoff_id(self) -> str:
        return "hnd_" + self.ulid()

    def snapshot_id(self) -> str:
        return "snp_" + self.ulid()
