"""Regression tests for transient cloud login server errors."""

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock

import aiohttp

PACKAGE = Path(__file__).resolve().parents[1] / "custom_components" / "amc_alarm"
if str(PACKAGE) not in sys.path:
    sys.path.insert(0, str(PACKAGE))

from amc_alarm_api.api import CommandState, ConnectionState, SimplifiedAmcApi  # noqa: E402
from amc_alarm_api.exceptions import AuthenticationFailed, LoginServerError  # noqa: E402


def _frame(status):
    return types.SimpleNamespace(
        data=json.dumps({"command": "loginUser", "status": status})
    )


class _WebSocket:
    def __init__(self, frames):
        self.frames = iter(frames)
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
            return next(self.frames)
        except StopIteration:
            raise StopAsyncIteration

    async def send_str(self, payload):
        self.sent.append(payload)

    async def close(self):
        self.closed = True


class _Session:
    def __init__(self, websocket):
        self.websocket = websocket
        self.closed = False

    def ws_connect(self, *_args, **_kwargs):
        return self.websocket

    async def close(self):
        self.closed = True


class _QueuedWebSocket(_WebSocket):
    """A websocket that stays connected after its queued frames are consumed."""

    def __init__(self, frames):
        self.frames = asyncio.Queue()
        for frame in frames:
            self.frames.put_nowait(frame)
        self.sent = []
        self.closed = False

    async def __anext__(self):
        frame = await self.frames.get()
        if frame is None:
            raise StopAsyncIteration
        return frame


class _ReconnectSession:
    def __init__(self, sockets):
        self.sockets = iter(sockets)
        self.closed = False

    def ws_connect(self, *_args, **_kwargs):
        return next(self.sockets)

    async def close(self):
        self.closed = True


class LoginServerErrorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.api = SimplifiedAmcApi("user@example.com", "password", "id", "user", "pass")
        self.api._ws_state = ConnectionState.CONNECTED
        self.delays = []

    async def _process_without_wait(self, status):
        async def record(delay):
            self.delays.append(delay)

        with mock.patch("asyncio.sleep", new=record):
            await self.api._process_message(_frame(status))

    async def test_exact_server_error_uses_transient_retry_and_command_ko(self):
        await self._process_without_wait("server error")
        message = self.api._get_message_info("loginUser")
        self.assertEqual(message.state, CommandState.KO)
        self.assertIsInstance(message.error, LoginServerError)
        self.assertEqual(self.api._ws_state, ConnectionState.DISCONNECTED)
        self.assertIsNone(self.api._ws_state_stop_exeception)
        self.assertGreaterEqual(self.api._retry_delay, 600)
        self.assertEqual(self.delays, [self.api._retry_delay])
        self.assertGreaterEqual(
            self.api._login_retry_until,
            self.api._event_loop.time() + self.api._retry_delay - 0.1,
        )

    async def test_status_near_miss_remains_authentication_rejection(self):
        await self._process_without_wait("temporary server error")
        self.assertEqual(self.api._ws_state, ConnectionState.STOPPED)
        self.assertIsInstance(self.api._ws_state_stop_exeception, AuthenticationFailed)
        self.assertEqual(self.delays, [])

    async def test_repeated_server_errors_do_not_shorten_cooldown(self):
        await self._process_without_wait("server error")
        retained_deadline = self.api._event_loop.time() + 900
        self.api._login_retry_until = retained_deadline
        self.api._ws_state = ConnectionState.CONNECTED
        # Model a changed prior failure and a fresh failure counter: neither
        # should erase an already-established longer login cooldown.
        self.api._failed_attempts_last_msg = "TimeoutError: unrelated failure"
        self.api._failed_attempts = 0
        await self._process_without_wait("server error")
        self.assertEqual(self.api._login_retry_until, retained_deadline)
        self.assertGreaterEqual(self.api._retry_delay, 600)

    async def test_callback_failure_still_keeps_retry_floor(self):
        async def fail_callback():
            raise RuntimeError("callback failed")

        self.api._callback = fail_callback
        await self._process_without_wait("server error")
        self.assertEqual(self.api._ws_state, ConnectionState.DISCONNECTED)
        self.assertGreaterEqual(self.api._retry_delay, 600)
        self.assertEqual(self.delays, [self.api._retry_delay])

    async def test_listener_waits_without_relogin_and_disconnect_cleans_up(self):
        websocket = _WebSocket([types.SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT, data=_frame("server error").data
        )])
        session = _Session(websocket)
        self.api._aiohttp_session = session
        sleep_started = asyncio.Event()
        sleeps = []

        async def blocked_sleep(delay):
            sleeps.append(delay)
            sleep_started.set()
            await asyncio.Event().wait()

        with mock.patch("asyncio.sleep", new=blocked_sleep):
            self.api._listen_task = asyncio.create_task(self.api._listen())
            await asyncio.wait_for(sleep_started.wait(), timeout=1)
            self.assertEqual(self.api._ws_state, ConnectionState.DISCONNECTED)
            self.assertEqual(sleeps, [self.api._retry_delay])
            self.assertEqual(len(websocket.sent), 1)
            await self.api.disconnect()

        self.assertIsNone(self.api._listen_task)
        self.assertTrue(websocket.closed)
        self.assertTrue(session.closed)
        self.assertEqual(len(websocket.sent), 1)

    async def test_listener_reconnects_after_server_error_and_recovers_states(self):
        server_error = types.SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT,
            data=_frame("server error").data,
        )
        first_socket = _WebSocket([server_error])
        login_ok = types.SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT,
            data=(
                '{"command":"loginUser","status":"Logged",'
                '"user":{"email":"user@example.com",'
                '"userState":"confirmed","token":"token-2"}}'
            ),
        )
        states_ok = types.SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT,
            data=(
                '{"command":"getStates","status":"ok","layout":null,'
                '"centrals":{"id":{"statusID":1,"status":"running fresh",'
                '"data":[]}}}'
            ),
        )
        second_socket = _QueuedWebSocket([login_ok, states_ok])
        session = _ReconnectSession([first_socket, second_socket])
        self.api._aiohttp_session = session
        sleep_started = asyncio.Event()
        release_retry = asyncio.Event()
        original_sleep = asyncio.sleep

        async def controlled_sleep(delay):
            if delay == self.api.LOGIN_SERVER_ERROR_RETRY_DELAY:
                sleep_started.set()
                await release_retry.wait()
            else:
                await original_sleep(0)

        def login_payloads(websocket):
            return [
                json.loads(payload)
                for payload in websocket.sent
                if json.loads(payload).get("command") == "loginUser"
            ]

        with mock.patch("asyncio.sleep", new=controlled_sleep):
            self.api._listen_task = asyncio.create_task(self.api._listen())
            await asyncio.wait_for(sleep_started.wait(), timeout=1)

            first_logins = login_payloads(first_socket)
            self.assertEqual(len(first_logins), 1)
            self.assertEqual(first_logins[0]["data"], {
                "email": "user@example.com", "password": "password"
            })
            self.assertEqual(len(second_socket.sent), 0)
            self.assertEqual(self.api._ws_state, ConnectionState.DISCONNECTED)

            release_retry.set()
            for _ in range(100):
                if self.api._ws_state == ConnectionState.CENTRAL_OK:
                    break
                await original_sleep(0.01)
            self.assertEqual(self.api._ws_state, ConnectionState.CENTRAL_OK)
            second_logins = login_payloads(second_socket)
            self.assertEqual(len(second_logins), 1)
            self.assertEqual(second_logins[0]["data"], first_logins[0]["data"])
            self.assertTrue(self.api._raw_states_central_valid)
            self.assertEqual(self.api.raw_states()["id"].status, "running fresh")
            self.assertIsNone(self.api._ws_state_stop_exeception)
            self.assertIsNone(self.api._get_message_info("loginUser").error)

            await self.api.disconnect()

        self.assertTrue(first_socket.closed)
        self.assertTrue(second_socket.closed)
        self.assertTrue(session.closed)
        self.assertIsNone(self.api._listen_task)

    async def test_successful_login_recovers_after_server_error(self):
        await self._process_without_wait("server error")
        await self.api._process_message(types.SimpleNamespace(data=(
            '{"command":"loginUser","status":"Logged",'
            '"user":{"email":"user@example.com","userState":"confirmed",'
            '"token":"token"}}'
        )))
        self.assertEqual(self.api._ws_state, ConnectionState.AUTHENTICATED)
        self.assertEqual(self.api._sessionToken, "token")
        self.assertIsNone(self.api._ws_state_stop_exeception)


if __name__ == "__main__":
    unittest.main()
