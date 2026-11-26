"""Central login rejection handling after a previously valid response."""

import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import aiohttp

_PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "amc_alarm"
if str(_PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_ROOT))

from amc_alarm_api.api import (  # noqa: E402
    AmcStatesParser,
    CommandState,
    ConnectionState,
    SimplifiedAmcApi,
)
from amc_alarm_api.exceptions import (  # noqa: E402
    AmcCentralStatusErrorException,
    AuthenticationFailed,
)


CENTRAL_ID = "central-id"


class _FakeWebSocket:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.sent = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self.messages)
        except StopIteration:
            raise StopAsyncIteration

    async def send_str(self, payload):
        self.sent.append(payload)

    async def close(self):
        self.closed = True


class _FakeSession:
    def __init__(self, websocket):
        self.websocket = websocket
        self.closed = False

    def ws_connect(self, *_args, **_kwargs):
        return self.websocket

    async def close(self):
        self.closed = True


def _frame(status_id: int, status: str, command_status: str = "ok"):
    payload = (
        '{"command":"getStates","status":"%s","layout":null,'
        '"centrals":{"%s":{"statusID":%s,"status":"%s","data":[]}}}'
        % (command_status, CENTRAL_ID, status_id, status)
    )
    return types.SimpleNamespace(data=payload)


class CentralLoginRetryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = SimplifiedAmcApi(
            "user@example.com", "password", CENTRAL_ID, "central-user", "central-password"
        )
        self.api._ws_state = ConnectionState.CENTRAL_OK
        self.slept = []

    async def _handle(self, status_id, central_status, command_status="ok"):
        async def record_sleep(delay):
            self.slept.append(delay)

        with mock.patch("asyncio.sleep", new=record_sleep):
            await self.api._process_message(
                _frame(status_id, central_status, command_status)
            )

    async def _establish_valid_central(self):
        await self._handle(1, "running")
        self.assertTrue(self.api._raw_states_central_valid)

    async def test_initial_central_rejection_remains_authentication_failure(self):
        await self._handle(0, "wrong login model/4.10")

        self.assertEqual(self.api._ws_state, ConnectionState.STOPPED)
        self.assertIsInstance(self.api._ws_state_stop_exeception, AuthenticationFailed)
        self.assertIsInstance(
            self.api._get_message_info("getStates").error, AuthenticationFailed
        )
        self.assertEqual(self.slept, [])

    async def test_prior_success_rejection_disconnects_and_applies_ten_minute_floor(self):
        await self._establish_valid_central()
        self.api._msg_quee_get_states = True

        await self._handle(0, "wrong login model/4.10")

        command = self.api._get_message_info("getStates")
        self.assertEqual(command.state, CommandState.KO)
        self.assertIsInstance(command.error, AmcCentralStatusErrorException)
        self.assertEqual(self.api._ws_state, ConnectionState.DISCONNECTED)
        self.assertIsNone(self.api._ws_state_stop_exeception)
        self.assertTrue(self.api._raw_states_central_valid)
        self.assertGreaterEqual(self.api._retry_delay, 600)
        self.assertEqual(self.slept, [self.api._retry_delay])

        websocket = mock.AsyncMock()
        self.api._websocket = websocket
        await self.api._send_msg_quee()
        websocket.send_str.assert_not_awaited()

    async def test_differing_rejections_each_keep_minimum_retry_delay(self):
        await self._establish_valid_central()
        await self._handle(0, "wrong login model/4.10")
        first_delay = self.api._retry_delay
        # Model the listener reconnecting after the first minimum-delay wait.
        self.api._ws_state = ConnectionState.CONNECTED
        await self._handle(0, "wrong login other-model/5.00")

        self.assertGreaterEqual(first_delay, 600)
        self.assertGreaterEqual(self.api._retry_delay, 600)
        self.assertEqual(self.slept, [first_delay, self.api._retry_delay])
        self.assertTrue(self.api._raw_states_central_valid)
        self.assertNotIsInstance(self.api._ws_state_stop_exeception, AuthenticationFailed)

    async def test_valid_response_recovers_after_rejection(self):
        await self._establish_valid_central()
        await self._handle(0, "wrong login model/4.10")

        await self._handle(1, "running")

        self.assertEqual(self.api._ws_state, ConnectionState.CENTRAL_OK)
        self.assertTrue(self.api._raw_states_central_valid)
        self.assertEqual(self.api._get_message_info("getStates").state, CommandState.OK)
        self.assertIsNone(self.api._ws_state_stop_exeception)

    async def test_listener_rejection_waits_then_disconnects_without_reconnect(self):
        login = types.SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT,
            data=(
                '{"command":"loginUser","status":"Logged",'
                '"user":{"email":"user@example.com","userState":"confirmed",'
                '"token":"token"}}'
            ),
        )
        success = types.SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT, data=_frame(1, "running").data
        )
        rejection = types.SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT,
            data=_frame(0, "wrong login model/4.10").data,
        )
        websocket = _FakeWebSocket([login, success, rejection])
        session = _FakeSession(websocket)
        self.api._aiohttp_session = session
        sleep_started = asyncio.Event()
        sleeps = []

        async def blocking_sleep(delay):
            sleeps.append(delay)
            sleep_started.set()
            await asyncio.Event().wait()

        with mock.patch("asyncio.sleep", new=blocking_sleep):
            self.api._listen_task = asyncio.create_task(self.api._listen())
            await asyncio.wait_for(sleep_started.wait(), timeout=1)

            self.assertTrue(self.api._raw_states_central_valid)
            self.assertEqual(self.api._ws_state, ConnectionState.DISCONNECTED)
            self.assertEqual(sleeps, [self.api.CENTRAL_LOGIN_RETRY_DELAY])
            self.assertEqual(len(websocket.sent), 2)  # one login and one getStates

            await self.api.disconnect()

        self.assertTrue(self.api._listen_task is None)
        self.assertTrue(websocket.closed)
        self.assertTrue(session.closed)
        self.assertEqual(len(websocket.sent), 2)  # no reconnect/re-login after rejection

    async def test_callback_failure_does_not_bypass_central_retry_floor(self):
        await self._establish_valid_central()

        async def broken_callback():
            raise RuntimeError("coordinator callback failed")

        self.api._callback = broken_callback
        await self._handle(0, "wrong login model/4.10")

        self.assertEqual(self.api._ws_state, ConnectionState.DISCONNECTED)
        self.assertEqual(self.slept, [self.api.CENTRAL_LOGIN_RETRY_DELAY])
        self.assertGreaterEqual(self.api._retry_delay, 600)

    async def test_availability_grace_expires_and_valid_states_restore_it(self):
        callback_states = []

        async def record_callback():
            callback_states.append(
                (self.api._ws_state, self.api._device_available)
            )

        self.api._callback = record_callback
        await self._handle(1, "running initial-model")
        self.assertTrue(self.api._device_available)
        self.assertTrue(self.api._raw_states_central_valid)

        await self._handle(0, "wrong login model/4.10")
        self.assertEqual(self.api._ws_state, ConnectionState.DISCONNECTED)
        self.assertTrue(self.api._device_available)  # five-second offline grace
        deadline = self.api._device_online_to_date
        self.assertIsNotNone(deadline)
        self.assertGreaterEqual(
            deadline - self.api._event_loop.time(), self.api.DEVICE_OFFLINE_DELAY - 0.1
        )

        # Cloud authentication alone must not claim that the central is online.
        await self.api._process_message(
            types.SimpleNamespace(
                data=(
                    '{"command":"loginUser","status":"Logged",'
                    '"user":{"email":"user@example.com",'
                    '"userState":"confirmed","token":"token"}}'
                )
            )
        )
        self.assertEqual(self.api._ws_state, ConnectionState.AUTHENTICATED)
        self.assertTrue(self.api._device_available)  # still only within grace

        # Advance the monotonic deadline without sleeping or changing system time.
        self.api._device_online_to_date = self.api._event_loop.time() - 1
        await self.api._set_device_available(True)
        self.assertFalse(self.api._device_available)
        self.assertEqual(callback_states[-1], (ConnectionState.AUTHENTICATED, False))

        await self._handle(1, "running fresh-model")
        self.assertEqual(self.api._ws_state, ConnectionState.CENTRAL_OK)
        self.assertTrue(self.api._device_available)
        self.assertEqual(
            AmcStatesParser(self.api.raw_states()).model(CENTRAL_ID), "fresh-model"
        )
        self.assertEqual(callback_states[-1], (ConnectionState.CENTRAL_OK, True))

    async def test_disconnect_cancels_long_central_retry_wait(self):
        await self._establish_valid_central()
        sleep_started = asyncio.Event()

        async def blocking_sleep(_delay):
            sleep_started.set()
            await asyncio.Event().wait()

        with mock.patch("asyncio.sleep", new=blocking_sleep):
            task = asyncio.create_task(
                self.api._process_message(_frame(0, "wrong login model/4.10"))
            )
            self.api._listen_task = task
            await asyncio.wait_for(sleep_started.wait(), timeout=1)

            await self.api.disconnect()

        self.assertTrue(task.done())
        self.assertTrue(task.cancelled())
        self.assertEqual(self.api._ws_state, ConnectionState.STOPPED)


if __name__ == "__main__":
    unittest.main()
