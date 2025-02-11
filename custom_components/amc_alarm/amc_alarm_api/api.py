import asyncio
import logging
import json 
from enum import Enum
from datetime import datetime, timedelta

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
    AmcUsers,
    AmcUserEntry,
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
        self._last_login_date = None

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

    def isConnected(self) -> bool:
        if self._ws_state == ConnectionState.DISCONNECTED:
            return False
        if not self._websocket or not self._aiohttp_session:
            return False
        # File "/usr/local/lib/python3.13/site-packages/aiohttp/_websocket/writer.py", line 73, in send_frame
        #    raise ClientConnectionResetError("Cannot write to closing transport")
        #aiohttp.client_exceptions.ClientConnectionResetError: Cannot write to closing transport
        if self._aiohttp_session.closed or self._websocket.closed:
            return False
        return True

    async def connect_if_disconnected(self):
        if not self.isConnected():
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
            except aiohttp.ClientResponseError as error:
                _LOGGER.error("Unexpected response received from server : %s", error)
            #    self._ws_state = ConnectionState.STOPPED
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError, aiohttp.client_exceptions.ClientConnectionResetError) as error:
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
                exstr = str(error)
                if "'NoneType' object has no attribute 'connect'" in exstr:
                    _LOGGER.debug("Unexpected exception occurred: %s", error)
                    self._ws_state = ConnectionState.DISCONNECTED
                if self._ws_state != ConnectionState.STOPPED and self._ws_state != ConnectionState.DISCONNECTED:
                    _LOGGER.exception("Unexpected exception occurred: %s", error)
                    self._ws_state = ConnectionState.STOPPED

    async def _process_message(self, message):
        try:
            #parse_raw and parse_file are now deprecated. In Pydantic V2
            data = AmcCommandResponse.model_validate_json(message.data, strict=False)
        except ValueError as e:
            _LOGGER.warning(
                "Can't process data from server: %s, data=%s" % (e, message.data)
            )
            return
        
        match data.command:
            case AmcCommands.CHECK_CENTRALS:
                _LOGGER.debug("Received message %s" % data.command)
            case "updateVideoList":
                _LOGGER.debug("Received message %s" % data.command)
            case "visitedOK":
                _LOGGER.debug("Received message %s" % data.command)

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
                    if self._last_login_date + timedelta(seconds=15) < datetime.now():
                        _LOGGER.debug("Logging after received request to relogin: %s" % (message.data))
                        await self._login()
                    return

                if data.status == AmcCommands.STATUS_OK:
                    #if not self._raw_states:
                    self._raw_states = data.centrals
                    #lo aggiorno cosi il valore passato al coordinator non cambia e viene aggiornato subito
                    #self._raw_states.update(data.centrals)
                    if self._callback:
                        self._raw_states[self._central_id].returned = 0
                        await self._callback()
                elif data.status == AmcCommands.STATUS_KO:
                    status_new = data.centrals[self._central_id].status
                    status_old = self._raw_states[self._central_id].status;
                    if status_new != status_old:
                        _LOGGER.debug("Error getting states (%s): %s" % (status_new, message.data))
                        self._raw_states[self._central_id].status = status_new                        
                        if self._callback:
                            self._raw_states[self._central_id].returned = 0
                            await self._callback()
                    #{"command":"getStates","status":"ko","layout":null,"centrals":{"10EF60834A5436323003323338310000":{"statusID":-1,"status":"not available"}}}
                #    _LOGGER.debug("Error getting states (not available): %s" % data.centrals)
                    #await self.disconnect()
                else:
                    _LOGGER.warning("Error getting states: %s, data=%s" % (data.centrals, message.data))
                    raise AmcException(data.centrals or message.data)
            case AmcCommands.APPLY_PATCH:
                #try:
                #zona = AmcStatesParser(self._raw_states).zone(self._central_id, 40282).states.bit_opened
                for patch in data.patch:
                    await self._process_message_patch(patch)
                #zona_new = AmcStatesParser(self._raw_states).zone(self._central_id, 40282).states.bit_opened
                #_LOGGER.debug(
                #    "APPLY_PATCH log test id 19: old: %s, new: %s" % (zona, zona_new)
                #)
                if self._callback:
                    self._raw_states[self._central_id].returned = 0
                    await self._callback()
                #except Exception as e:
                #    _LOGGER.warning(
                #        "Can't process patch from server: %s, data=%s" % (e, message.data)
                #    )
            case _:
                _LOGGER.warning("Unknown command received from server : %s, data=%s" % (data, message.data))

    async def _process_message_patch(self, patch):
        is_add = patch.op == "add"
        is_replace = patch.op == "replace"
        data_node_type = -1
        obj_parent = None
        obj = None
        obj_is_central_data_list = False
        obj_is_central_data_node = False
        nodes = patch.path.split('/')
        curr_node = ""
        for node_index, node in enumerate(nodes):
            node_is_last = node_index == len(nodes) - 1
            if node == "":
                continue
            #per ora non è gestito bene notifications
            if node == 'notifications':
                return
            curr_node = curr_node + "/" + node
            if node == 'centrals' and obj == None:
                obj = self._raw_states
                continue
            if not obj:
                _LOGGER.warning("Can't process patch, obj is null: node: %s, data=%s" % (curr_node, patch))
                return

            if node_is_last and is_add:
                #arrivano le notifiche in add
                if len(obj) == 0:
                    return
                new_node = obj[0].copy()
                new_node = self._update_model_from_dict(new_node, patch.value, False, curr_node)
                obj.insert(0, new_node)
                return
            if node_is_last and not isinstance(patch.value, dict):
                #esempio senza dict: data/5/unvisited", "value": "1" }
                my_dictionary = {}
                my_dictionary[node] = patch.value
                self._update_model_from_dict(obj, my_dictionary, True, curr_node)
                return

            obj_parent = obj
            if isinstance(obj, list) and node.isnumeric():
                if data_node_type == -1 and obj_is_central_data_list:
                    data_node_type = int(node)
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
            obj_is_central_data_node = obj_is_central_data_list
            obj_is_central_data_list = node == "data" and isinstance(obj_parent, AmcCentralResponse)
        
        self._update_model_from_dict(obj, patch.value, True, curr_node)

    def _update_model_from_dict(self, obj, new_values, exec_parse_obj, curr_node):
        if isinstance(obj, dict):
            obj.update(new_values)
            return obj
        else:
            if exec_parse_obj:
                try:
                    new_obj = obj.parse_obj(new_values)
                except Exception as e:
                    new_obj = obj
                    exec_parse_obj = False
                    #_LOGGER.warning(
                    #    "Can't process patch from server: %s, data=%s" % (e, message.data)
                    #)                
            else:
                new_obj = obj
            #_LOGGER.debug("Changing values: node: %s, patch=%s" % (obj_parent, patch.value))
            #if patch.value["bit_opened"] != getattr(obj, "bit_opened", None):
            #    _LOGGER.debug("Changing bit_opened attribute 1: node: %s, data=%s" % (obj.bit_opened, patch.value["bit_opened"]))
                #obj1 = obj.parse_obj(patch.value)
            for key,value in new_values.items():
                if not hasattr(obj,key):
                    continue
                old_value = getattr(obj,key,None)
                if old_value == value:
                    continue
                new_value = getattr(new_obj,key,None)
                setattr(obj, key, new_value)
                if exec_parse_obj:
                    _LOGGER.debug("Changing %s %s attribute: old_value: %s, new_value=%s" % (curr_node, key, old_value, new_value))
            return new_obj
            #if hasattr(patch.value, "anomaly") and getattr(patch.value, "anomaly", None) != getattr(obj, "anomaly", None):
            #    _LOGGER.debug("Changing anomaly attribute: node: %s, data=%s" % (obj_parent, patch.value))
            #obj.parse_obj(patch.value)

    async def _login(self):
        self._last_login_date = datetime.now()
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

    async def command_set_states(self, group: int, index: int, state: int, userPIN: str):
        user=AmcStatesParser(self.raw_states()).user_by_pin(self._central_id, userPIN) if userPIN else None
        userIdx=user.index if user else None
        await self._send_message(
            AmcCommand(
                command="setStates",
                centralID=self._central_id,
                centralUsername=self._central_username,
                centralPassword=self._central_password,
                group=group,
                index=index,
                state=True if state == 1 else False,
                userPIN=userPIN,
                userIdx=userIdx
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
        
    def users(self, central_id: str) -> dict[str, AmcUserEntry]:
        section : AmcUsers = self._get_section(central_id, CentralDataSections.USERS)
        return section.users if section.index == CentralDataSections.USERS else None
    
    def user_by_pin(self, central_id: str, userPin: str) -> AmcUserEntry:        
        #_LOGGER.debug("user_by_pin: Pin '%s'" % userPin)
        if not userPin:
            return None
        users = self.users(central_id)
        user = users.get(userPin) if users else None        
        if not user:
            _LOGGER.warning("Cannot find User By PIN: Pin '%s'" % userPin)
        return user

