"""Regression tests for how a rejected login is classified.

A login the cloud throttles is transient and must go through the back-off, so
the integration keeps working on its own once the throttling ends. A login the
cloud rejects on the credentials must still stop the client and raise
AuthenticationFailed, so Home Assistant starts the re-auth flow.
"""

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "amc_alarm"
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from amc_alarm_api.api import ConnectionState, SimplifiedAmcApi  # noqa: E402
from amc_alarm_api.exceptions import AuthenticationFailed, LoginThrottled  # noqa: E402


def _frame(payload: str) -> types.SimpleNamespace:
    """A websocket text frame; only `data` is read on this path."""
    return types.SimpleNamespace(data=payload)


class LoginRejectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.api = SimplifiedAmcApi(
            "user@example.com",
            "password",
            "central-id",
            "central-user",
            "central-password",
        )
        # _manage_running_error returns early unless a connection is live.
        self.api._ws_state = ConnectionState.CONNECTED
        self.slept: list[float] = []

    async def _handle(self, payload: str) -> None:
        async def _record(delay):
            self.slept.append(delay)

        # The back-off really sleeps; record the delay instead of waiting it out.
        with mock.patch("asyncio.sleep", new=_record):
            await self.api._process_message(_frame(payload))

    async def test_throttled_login_backs_off_instead_of_asking_for_reauth(self):
        await self._handle('{"command": "loginUser", "status": "Too Many Requests"}')

        self.assertIsNone(self.api._ws_state_stop_exeception)
        self.assertNotEqual(self.api._ws_state, ConnectionState.STOPPED)
        self.assertGreaterEqual(
            self.api._retry_delay, SimplifiedAmcApi.LOGIN_THROTTLED_RETRY_DELAY
        )
        self.assertIsInstance(
            self.api._get_message_info("loginUser").error, LoginThrottled
        )
        self.assertAlmostEqual(
            self.api._login_retry_until,
            self.api._event_loop.time() + self.api._retry_delay,
            places=2,
        )
        self.assertEqual(self.slept, [self.api._retry_delay])

    async def test_listener_throttle_wait_is_cancelled_by_disconnect(self):
        sleep_started = asyncio.Event()

        async def blocking_sleep(_delay):
            sleep_started.set()
            await asyncio.Event().wait()

        payload = _frame('{"command": "loginUser", "status": "Too Many Requests"}')
        with mock.patch("asyncio.sleep", new=blocking_sleep):
            listener_task = asyncio.create_task(self.api._process_message(payload))
            self.api._listen_task = listener_task
            await asyncio.wait_for(sleep_started.wait(), timeout=1)

            login = self.api._get_message_info("loginUser")
            self.assertEqual(login.state.name, "KO")
            self.assertIsInstance(login.error, LoginThrottled)
            self.assertIsNotNone(self.api._login_retry_until)
            self.assertAlmostEqual(
                self.api._login_retry_until,
                self.api._event_loop.time() + self.api._retry_delay,
                places=1,
            )

            await self.api.disconnect()
            self.assertTrue(listener_task.done())
            self.assertIsNone(self.api._listen_task)


    async def test_rejected_credentials_still_stop_the_client(self):
        await self._handle('{"command": "loginUser", "status": "Wrong credentials"}')

        self.assertIsInstance(
            self.api._ws_state_stop_exeception, AuthenticationFailed
        )
        self.assertEqual(self.api._ws_state, ConnectionState.STOPPED)
        self.assertEqual(self.slept, [])

    async def test_successful_login_is_untouched(self):
        # No user.password, as the cloud now answers — see #24.
        await self._handle(
            '{"command": "loginUser", "status": "Logged",'
            ' "user": {"email": "user@example.com",'
            ' "userState": "confirmed", "token": "login-token"}}'
        )

        self.assertIsNone(self.api._ws_state_stop_exeception)
        self.assertEqual(self.api._ws_state, ConnectionState.AUTHENTICATED)
        self.assertEqual(self.api._sessionToken, "login-token")
        self.assertEqual(self.slept, [])


if __name__ == "__main__":
    unittest.main()
