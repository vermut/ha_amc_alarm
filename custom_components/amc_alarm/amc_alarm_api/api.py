import asyncio
import logging
from enum import Enum
from typing import Callable

import aiohttp
from aiohttp import WSMessage

from .exceptions import AmcException, ConnectionFailed, AuthenticationFailed
from .amc_proto import (
    AmcCommands,
    AmcCommand,
    AmcCommandResponse,
    AmcLogin,
    AmcCentral,
    AmcStatesType,
)

_LOGGER = logging.getLogger(__name__)


class ConnectionState(Enum):
    STARTING = 0
    CONNECTED = 1
    AUTHENTICATED = 2
    DISCONNECTED = 3
    STOPPED = 4


class SimplifiedAmcApi:
    MAX_FAILED_ATTEMPTS = 60

    def __init__(
        self,
        login_email,
        password,
        central_id,
        central_username,
        central_password,
        async_state_updated_callback=None,
    ):
        self.states: AmcStatesType = {}

        self._ws_url = "wss://service.amc-cloud.com/ws/client"
        self._login_email = login_email
        self._password = password
        self._central_id = central_id
        self._central_username = central_username
        self._central_password = central_password

        self._listen_task = None
        self._ws_state = ConnectionState.DISCONNECTED
        self._aiohttp_session = None
        self._websocket = None
        self._sessionToken = None

        self._callback = async_state_updated_callback

    async def connect(self):
        await self.disconnect()

        self._listen_task = asyncio.create_task(self._listen())
        for _ in range(30):  # Wait 30 secs
            await asyncio.sleep(1)
            if self._ws_state in [
                ConnectionState.DISCONNECTED,
                ConnectionState.STARTING,
                ConnectionState.CONNECTED,
            ]:
                continue

            if self._listen_task.done() and issubclass(
                self._listen_task.exception().__class__, AmcException
            ):
                raise self._listen_task.exception()  # Something known happened in the listener

            if self._ws_state == ConnectionState.AUTHENTICATED:
                break

        if self._ws_state != ConnectionState.AUTHENTICATED:
            raise ConnectionFailed()

        await self._query_states()

    async def _listen(self) -> None:
        """Listen to messages"""
        # Infinite loop to listen to messages on the websocket and manage retries.
        self._failed_attempts = 0
        while self._ws_state != ConnectionState.STOPPED:
            await self._running()

    async def _running(self) -> None:
        self._ws_state = ConnectionState.STARTING
        async with aiohttp.ClientSession() as session:
            self._aiohttp_session = session
            try:
                _LOGGER.debug("Logging into %s" % self._ws_url)
                async with session.ws_connect(
                    self._ws_url, heartbeat=15, autoping=True
                ) as ws_client:
                    self._ws_state = ConnectionState.CONNECTED
                    self._websocket = ws_client
                    await self._login()

                    message: WSMessage
                    async for message in ws_client:
                        if self._ws_state == ConnectionState.STOPPED:
                            break

                        if message.type == aiohttp.WSMsgType.ERROR:
                            _LOGGER.error("Error received from WS server: %s", message)
                            break

                        if message.type == aiohttp.WSMsgType.CLOSED:
                            _LOGGER.warning("AIOHTTP websocket connection closed")
                            break

                        if message.type == aiohttp.WSMsgType.TEXT:
                            _LOGGER.debug("Websocket received data: %s", message.data)
                            data = AmcCommandResponse.parse_raw(message.data)

                            match data.command:
                                case AmcCommands.LOGIN_USER:
                                    if data.status == AmcCommands.STATUS_LOGGED_IN:
                                        _LOGGER.debug("Authorized")
                                        self._ws_state = ConnectionState.AUTHENTICATED
                                        self._sessionToken = data.user.token
                                    else:
                                        _LOGGER.debug(
                                            "Authorization failure: %s" % data.status
                                        )
                                        raise AuthenticationFailed(data.status)

                                case AmcCommands.GET_STATES:
                                    if data.status == AmcCommands.STATUS_OK:
                                        for (
                                            central_id,
                                            central,
                                        ) in data.centrals.items():
                                            self.states[central_id] = {}
                                            self.states[central_id]["ZONES"] = [
                                                item
                                                for x in central.data
                                                if x.name == "ZONES"
                                                for item in x.list
                                            ]
                                            self.states[central_id]["GROUPS"] = [
                                                item
                                                for x in central.data
                                                if x.name == "GROUPS"
                                                for item in x.list
                                            ]
                                            self.states[central_id]["AREAS"] = [
                                                item
                                                for x in central.data
                                                if x.name == "AREAS"
                                                for item in x.list
                                            ]
                                            self.states[central_id]["NOTIFICATIONS"] = [
                                                item
                                                for x in central.data
                                                if x.name == "Notifications"
                                                for item in x.list
                                            ]
                                        if self._callback:
                                            await self._callback()
                                    else:
                                        _LOGGER.debug(
                                            "Error getting states: %s" % data.centrals
                                        )
                                        raise AmcException(data.centrals)

            except aiohttp.ClientResponseError as error:
                _LOGGER.error("Unexpected response received from server : %s", error)
                self._ws_state = ConnectionState.STOPPED
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as error:
                if self._failed_attempts >= self.MAX_FAILED_ATTEMPTS:
                    _LOGGER.error(
                        "Too many retries to reconnect to server. Please restart globally."
                    )
                    self._ws_state = ConnectionState.STOPPED
                elif self._ws_state != ConnectionState.STOPPED:
                    retry_delay = min(2 ** (self._failed_attempts - 1) * 30, 300)
                    self._failed_attempts += 1
                    _LOGGER.error(
                        "Websocket connection failed, retrying in %ds: %s",
                        retry_delay,
                        error,
                    )
                    self._ws_state = ConnectionState.DISCONNECTED
                    await asyncio.sleep(retry_delay)
            except AmcException:
                self._ws_state = ConnectionState.STOPPED
                raise
            except Exception as error:
                if self._ws_state != ConnectionState.STOPPED:
                    _LOGGER.exception("Unexpected exception occurred: %s", error)
                    self._ws_state = ConnectionState.STOPPED

    async def _login(self):
        _LOGGER.info("Logging in with email: %s", self._login_email)
        login_message = AmcCommand(
            command=AmcCommands.LOGIN_USER,
            data=AmcLogin(email=self._login_email, password=self._password),
        )
        await self._send_message(login_message)

    async def disconnect(self):
        _LOGGER.debug("Disconnecting")
        self._ws_state = ConnectionState.STOPPED
        if self._websocket:
            await self._websocket.close()
        if self._aiohttp_session:
            await self._aiohttp_session.close()
        self._ws_state = ConnectionState.DISCONNECTED

    async def _send_message(self, msg: AmcCommand):
        if self._sessionToken:
            msg.token = self._sessionToken
        await self._websocket.send_str(msg.json(exclude_none=True, exclude_unset=True))

    async def _query_states(self):
        await self._send_message(
            AmcCommand(
                command="getStates",
                centrals=[
                    AmcCentral(
                        centralID=self._central_id,
                        centralUsername=self._central_username,
                        centralPassword=self._central_password,
                    )
                ],
            )
        )
