'''Focused tests for SSE stream disconnect handling.'''

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest import mock

from pdf_web.api import jobs


class QueueEventsDisconnectTests(unittest.IsolatedAsyncioTestCase):
    '''Verify an idle queue stream releases its subscription on disconnect.'''

    async def test_disconnect_interrupts_idle_update_wait(self) -> None:
        '''Stop an idle stream and release its owner subscription promptly.'''
        class Store:  # pylint: disable=too-few-public-methods
            '''Minimal subscription store used by the route generator.'''

            def __init__(self) -> None:
                '''Track whether the stream releases its subscription.'''
                self.unsubscribed = False

            def subscribe_owner(self, _owner: str):
                '''Return an update source that remains idle until cancelled.'''
                class IdleUpdates:  # pylint: disable=too-few-public-methods
                    '''An update queue with no pending notifications.'''

                    async def get_batch(self):
                        '''Wait indefinitely, like an empty owner update queue.'''
                        await asyncio.Future()

                return 1, IdleUpdates()

            def unsubscribe_owner(self, _owner: str, _subscriber_id: int) -> None:
                '''Record cleanup of the active subscription.'''
                self.unsubscribed = True

        class Request:  # pylint: disable=too-few-public-methods
            '''Report a connection that disconnects at the first poll.'''

            calls = 0

            async def is_disconnected(self) -> bool:
                '''Return connected once, then disconnected.'''
                self.calls += 1
                return self.calls > 1

        store = Store()
        runtime = SimpleNamespace(store=store)
        request = Request()

        with mock.patch.object(jobs, "_queue_snapshot", return_value={"jobs": []}):
            response = await jobs.queue_events(request, "owner", runtime)
            stream = response.body_iterator
            self.assertTrue((await anext(stream)).startswith("event: queue\n"))

            with mock.patch.object(jobs, "SSE_DISCONNECT_POLL_SECONDS", 0.01):
                with self.assertRaises(StopAsyncIteration):
                    await asyncio.wait_for(anext(stream), timeout=0.2)

        self.assertTrue(store.unsubscribed)


if __name__ == "__main__":
    unittest.main()
