import asyncio
import logging
from enum import Enum

import aiohttp
from aiohttp import WSMessage

from .amc_proto import (
    AmcCommands,
    AmcCommand,
    AmcCommandResponse,
    AmcLogin,
    AmcCentral,
    AmcCentralResponse,
    CentralDataSections,
    AmcData,
    AmcEntry,
    AmcNotificationEntry,
    AmcNotification,
)
from .exceptions import AmcException, ConnectionFailed, AuthenticationFailed

_LOGGER = logging.getLogger(__name__)


class ConnectionState(Enum):
    STARTING = 0
    CONNECTED = 1
    AUTHENTICATED = 2
    DISCONNECTED = 3
    STOPPED = 4


class SimplifiedAmcApi:
    MAX_RETRY_DELAY = 600  # 10 min

    def __init__(
        self,
        login_email,
        password,
        central_id,
        central_username,
        central_password,
        async_state_updated_callback=None,
    ):
        self._raw_states: dict[str, AmcCentralResponse] = {}

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

        await self.command_get_states()

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
                            await self._process_message(message)

            except aiohttp.ClientResponseError as error:
                _LOGGER.error("Unexpected response received from server : %s", error)
                self._ws_state = ConnectionState.STOPPED
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError) as error:
                retry_delay = min(2**self._failed_attempts * 30, self.MAX_RETRY_DELAY)
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

    async def _process_message(self, message):
        try:
            data = AmcCommandResponse.parse_raw(message.data)
        except ValueError as e:
            _LOGGER.warning(
                "Can't process data from server: %s, data=%s" % (e, message.data)
            )
            return

        match data.command:
            case AmcCommands.LOGIN_USER:
                if data.status == AmcCommands.STATUS_LOGGED_IN:
                    _LOGGER.debug("Authorized")
                    self._ws_state = ConnectionState.AUTHENTICATED
                    self._sessionToken = data.user.token
                    self._failed_attempts = 0
                else:
                    _LOGGER.debug("Authorization failure: %s" % data.status)
                    raise AuthenticationFailed(data.status)

            case AmcCommands.GET_STATES:
                if data.status == AmcCommands.STATUS_OK:
                    self._raw_states = data.centrals
                    if self._callback:
                        await self._callback()
                else:
                    _LOGGER.debug("Error getting states: %s" % data.centrals)
                    raise AmcException(data.centrals)
            case _:
                _LOGGER.warning("Unknown command received from server : %s", data)

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
        payload = msg.json(exclude_none=True, exclude_unset=True)
        _LOGGER.debug("Websocket sending data: %s", payload)
        await self._websocket.send_str(payload)

    async def command_get_states(self):
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

    async def command_set_states(self, group: int, index: int, state: bool):
        await self._send_message(
            AmcCommand(
                command="setStates",
                centralID=self._central_id,
                centralUsername=self._central_username,
                centralPassword=self._central_password,
                group=group,
                index=index,
                state=state,
            )
        )

    def raw_states(self) -> dict[str, AmcCentralResponse]:
        return self._raw_states


class AmcStatesParser:
    def __init__(self, states: dict[str, AmcCentralResponse]):
        self._raw_states = states

    def raw_states(self) -> dict[str, AmcCentralResponse]:
        return self._raw_states

    def _get_section(self, central_id, section_index) -> AmcData | AmcNotification:
        central = self._raw_states[central_id]
        zones = next(x for x in central.data if x.index == section_index)
        return zones

    def groups(self, central_id: str) -> AmcData:
        return self._get_section(central_id, CentralDataSections.GROUPS)

    def group(self, central_id: str, entry_id: int) -> AmcEntry:
        return next(x for x in self.groups(central_id).list if x.Id == entry_id)

    def areas(self, central_id: str) -> AmcData:
        return self._get_section(central_id, CentralDataSections.AREAS)

    def area(self, central_id: str, entry_id: int) -> AmcEntry:
        return next(x for x in self.areas(central_id).list if x.Id == entry_id)

    def zones(self, central_id: str) -> AmcData:
        return self._get_section(central_id, CentralDataSections.ZONES)

    def zone(self, central_id: str, entry_id: int) -> AmcEntry:
        return next(x for x in self.zones(central_id).list if x.Id == entry_id)

    def outputs(self, central_id: str) -> AmcData:
        return self._get_section(central_id, CentralDataSections.OUTPUTS)

    def output(self, central_id: str, entry_id: int) -> AmcEntry:
        return next(x for x in self.outputs(central_id).list if x.Id == entry_id)

    def system_statuses(self, central_id: str) -> AmcData:
        return self._get_section(central_id, CentralDataSections.SYSTEM_STATUS)

    def system_status(self, central_id: str, entry_index: int) -> AmcEntry:
        return next(
            x for x in self.system_statuses(central_id).list if x.index == entry_index
        )

    def notifications(self, central_id: str) -> list[AmcNotificationEntry]:
        return self._get_section(central_id, CentralDataSections.NOTIFICATIONS).list

    def real_name(self, central_id: str) -> str:
        return self._raw_states[central_id].realName

    def status(self, central_id: str) -> str:
        return self._raw_states[central_id].status

    def model(self, central_id: str) -> str:
        # Assuming from status
        return self._raw_states[central_id].status.split(" ")[-1]
