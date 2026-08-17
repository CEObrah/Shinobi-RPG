"""Persistable SHA-256 counter RNG required by ``game/data/mechanics/core.json``."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Tuple


_DOMAIN = b"shinobi.sha256_counter_u64.v1\x00"


def _part(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(4, "big") + encoded


@dataclass(frozen=True)
class DrawReceipt:
    algorithm: str
    world_seed_hash: str
    transaction_id: str
    stream: str
    draw_index: int
    value_u64: int


class CounterRNG:
    """A named, deterministic stream with receipts for every raw draw."""

    algorithm = "sha256_counter_u64"

    def __init__(
        self,
        *,
        world_seed: str,
        transaction_id: str,
        stream: str,
        start_index: int = 0,
    ) -> None:
        if not world_seed or not transaction_id or not stream:
            raise ValueError("world_seed, transaction_id, and stream must be non-empty")
        if start_index < 0:
            raise ValueError("start_index must be non-negative")
        self._world_seed = world_seed
        self.transaction_id = transaction_id
        self.stream = stream
        self.draw_index = start_index
        self._seed_material = (
            _DOMAIN + _part(world_seed) + _part(transaction_id) + _part(stream)
        )
        self.world_seed_hash = hashlib.sha256(world_seed.encode("utf-8")).hexdigest()
        self._receipts: List[DrawReceipt] = []

    @property
    def receipts(self) -> Tuple[DrawReceipt, ...]:
        return tuple(self._receipts)

    def draw_u64(self) -> int:
        index = self.draw_index
        digest = hashlib.sha256(
            self._seed_material + index.to_bytes(16, "big")
        ).digest()
        value = int.from_bytes(digest[:8], "big", signed=False)
        self._receipts.append(
            DrawReceipt(
                algorithm=self.algorithm,
                world_seed_hash=self.world_seed_hash,
                transaction_id=self.transaction_id,
                stream=self.stream,
                draw_index=index,
                value_u64=value,
            )
        )
        self.draw_index += 1
        return value

    def randbelow(self, upper_bound: int) -> int:
        """Return an unbiased value in ``range(upper_bound)``.

        Rejection sampling may consume more than one raw draw; every consumed
        draw remains visible in ``receipts``.
        """

        if not isinstance(upper_bound, int) or upper_bound <= 0:
            raise ValueError("upper_bound must be a positive integer")
        modulus = 1 << 64
        limit = modulus - (modulus % upper_bound)
        while True:
            value = self.draw_u64()
            if value < limit:
                return value % upper_bound
