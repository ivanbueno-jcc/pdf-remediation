'''Owner-scoped, bounded notifications shared by registry and SSE routes.'''

from __future__ import annotations

import asyncio


class OwnerUpdateQueue:
    '''Bounded event-loop queue that coalesces updates by job identifier.'''

    def __init__(self) -> None:
        self._wake = asyncio.Queue(maxsize=1)
        self._pending: dict[str, tuple[str, str]] = {}

    def publish(self, update: tuple[str, str]) -> None:
        update_type, job_id = update
        previous = self._pending.get(job_id)
        if previous is None or update_type == "job-removed":
            self._pending[job_id] = update
        elif previous[1] != "job-removed":
            priority = {"job-updated": 1, "job-added": 2, "queue-changed": 3}
            if priority.get(update_type, 0) >= priority.get(previous[1], 0):
                self._pending[job_id] = update
        if self._wake.empty():
            self._wake.put_nowait(None)

    async def get_batch(self) -> list[tuple[str, str]]:
        await self._wake.get()
        updates = list(self._pending.values())
        self._pending.clear()
        return updates

__all__ = ["OwnerUpdateQueue"]
