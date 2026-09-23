"""Tests for owner-scoped SSE update coalescing."""

from __future__ import annotations

import asyncio
import unittest

from pdf_web.infrastructure.notifications import OwnerUpdateQueue


class OwnerUpdateQueueTests(unittest.TestCase):
    """Verify that coalescing preserves the most useful update."""

    def test_higher_priority_update_replaces_lower_priority_update(self) -> None:
        """A queue change supersedes an ordinary job update."""
        queue = OwnerUpdateQueue()

        queue.publish(("job-updated", "job-1"))
        queue.publish(("queue-changed", "job-1"))

        self.assertEqual(
            asyncio.run(queue.get_batch()), [("queue-changed", "job-1")]
        )

    def test_lower_priority_update_does_not_replace_higher_priority_update(self) -> None:
        """An ordinary update cannot hide a queue change."""
        queue = OwnerUpdateQueue()

        queue.publish(("queue-changed", "job-1"))
        queue.publish(("job-updated", "job-1"))

        self.assertEqual(
            asyncio.run(queue.get_batch()), [("queue-changed", "job-1")]
        )

    def test_removal_remains_terminal(self) -> None:
        """A removal cannot be replaced by a later update for that job."""
        queue = OwnerUpdateQueue()

        queue.publish(("job-updated", "job-1"))
        queue.publish(("job-removed", "job-1"))
        queue.publish(("queue-changed", "job-1"))

        self.assertEqual(
            asyncio.run(queue.get_batch()), [("job-removed", "job-1")]
        )


if __name__ == "__main__":
    unittest.main()
