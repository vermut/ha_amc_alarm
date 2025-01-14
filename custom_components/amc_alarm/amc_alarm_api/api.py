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
    AmcPatch,
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

    async def connect_if_disconnected(self):
        if self._ws_state != ConnectionState.CONNECTED or self._ws_state == ConnectionState.DISCONNECTED:
            await self.connect()

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

            except AuthenticationFailed:
                self._ws_state = ConnectionState.STOPPED
                raise
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError, aiohttp.ClientResponseError) as error:
                retry_delay = min(2**self._failed_attempts * 30, self.MAX_RETRY_DELAY)
                self._failed_attempts += 1
                _LOGGER.error(
                    "Websocket connection failed, retrying in %ds: %s",
                    retry_delay,
                    error,
                )
                self._ws_state = ConnectionState.DISCONNECTED
                await asyncio.sleep(retry_delay)
            #except AmcException:
            #    self._ws_state = ConnectionState.STOPPED
            #    raise
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
                    _LOGGER.debug("Authorization failure: %s, data=%s" % (data.status, message.data))
                    raise AuthenticationFailed(data.status or message.data)

            case AmcCommands.GET_STATES:
                
                #Websocket received data: {"command":"getStates","status":"error","message":"not logged, please login"}
                if data.status == AmcCommands.STATUS_ERROR and data.message == AmcCommands.MESSAGE_PLEASE_LOGIN:
                    _LOGGER.debug("Logging after received request to relogin: %s" % (message.data))
                    await self._login()
                    return

                if data.status == AmcCommands.STATUS_OK:
                    self._raw_states = data.centrals
                    if self._callback:
                        await self._callback()
                #elif data.status == AmcCommands.STATUS_NOT_AVAILABLE:
                #    _LOGGER.debug("Error getting states (not available): %s" % data.centrals)
                #    await self.disconnect()
                else:
                    _LOGGER.warning("Error getting states: %s, data=%s" % (data.centrals, message.data))
                    raise AmcException(data.centrals or message.data)
            case AmcCommands.APPLY_PATCH:
                try:
                    for patch in data.patch:
                        await self._process_message_patch(patch)
                    if self._callback:
                        await self._callback()
                except Exception as e:
                    _LOGGER.warning(
                        "Can't process patch from server: %s, data=%s" % (e, message.data)
                    )
            case _:
                _LOGGER.warning("Unknown command received from server : %s, data=%s" % (data, message.data))

    async def _process_message_patch(self, patch):
        obj_parent = None
        obj = None
        nodes = patch.path.split('/')
        curr_node = ""
        for node in nodes:
            if node == "":
                continue
            curr_node = curr_node + "/" + node
            if node == 'centrals' and obj == None:
                obj = self._raw_states
                continue
            if not obj:
                _LOGGER.warning("Can't process patch, obj is null: node: %s, data=%s" % (curr_node, patch))
                return
            obj_parent = obj
            if isinstance(obj, list) and node.isnumeric():
                #_LOGGER.debug("Getting list node %s: list count: %s, data=%s" % (curr_node, len(obj_parent), patch))
                obj = None
                #gli accessi alle liste lavorano sugli index nel modello
                for lst_item in obj_parent:
                    if int(getattr(lst_item, "index", -1)) == int(node):
                        obj = lst_item
                        #_LOGGER.debug("Finded list item %s" % (obj))
                        break
            elif isinstance(obj, dict):
                obj = obj[node]
            else:
                obj = getattr(obj, node)
            if not obj:
                _LOGGER.warning("Can't process patch, obj is null: node: %s, data=%s" % (curr_node, patch))
                return

        if isinstance(obj, dict):
            obj.update(patch.value)
        else:
            obj.parse_obj(patch.value)



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
        try:
            section = next(x for x in central.data if x.index == section_index)
            return section
        except StopIteration:
            return AmcData(index=0, list=[], name="_none")

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
