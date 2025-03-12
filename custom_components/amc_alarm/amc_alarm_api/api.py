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
from .exceptions import * #AmcException, ConnectionFailed, AuthenticationFailed, AmcCentralNotFoundException, AmcCentralStatusErrorException

_LOGGER = logging.getLogger(__name__)


class ConnectionState(Enum):
    STARTING = 0
    CONNECTED = 1
    AUTHENTICATED = 2
    DISCONNECTED = 3
    STOPPED = 4

class CommandState(Enum):
    NONE = 0
    STARTED = 1
    OK = 2
    KO = 3

class CommandMessageInfo():
    state: int = CommandState.NONE
    key : str = None 
    error: Exception = None
    result = None

    def set_ok(self, res = None):
        self.result = res
        self.state = CommandState.OK
    def set_ko(self, err : Exception = None):
        self.error = err if err else self.error
        self.state = CommandState.KO


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
        self._messages: dict[str, CommandMessageInfo] = {}
        self._raw_states: dict[str, AmcCentralResponse] = {}
        self._raw_states_central_valid : bool = False

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
        self._ws_state_disconnecting : bool = False
        self._ws_state_stop_exeception : Exception = None
        
        self._callback = async_state_updated_callback
        self._callback_get_states_disabled : bool = False
        self._last_login_date = None

    async def connect(self):
        await self.ensure_logged()
        if not self._raw_states_central_valid:
            await self.command_get_states_and_return()
            
    async def ensure_logged(self):
        message = self._get_message_info(AmcCommands.LOGIN_USER)
        if self._ws_state != ConnectionState.STOPPED:

            #se sto nella pausa della connessione, devo aspettare
            if self._ws_state_disconnecting and self._ws_state != ConnectionState.STOPPED and self._listen_task and not self._listen_task.done():
                await asyncio.sleep(1)
                if self._ws_state_disconnecting and self._ws_state != ConnectionState.STOPPED and self._listen_task and not self._listen_task.done():
                    raise asyncio.TimeoutError("Waiting timeout for server connection")
                    
            if not self._listen_task or self._listen_task.done():
                self._ws_state = ConnectionState.STARTING
                self._listen_task = asyncio.create_task(self._listen())
            elif self._ws_state == ConnectionState.CONNECTED:
                message = await self._login()
            elif self._last_login_date + timedelta(minutes=30) < datetime.now():
                _LOGGER.debug("Executing new login after 30 minutes")
                message = await self._login()
        await self._get_message_info_result(message)


    async def command_get_states_and_return(self) -> dict[str, AmcCentralResponse]:
        await self.ensure_logged()
        try:
            self._callback_get_states_disabled = True
            message = await self.command_get_states()
            result = await self._get_message_info_result(message)
            return result
        finally:
            await asyncio.sleep(0.2) # wait for execute _callback_get_states_disabled
            self._callback_get_states_disabled = False


    async def disconnect(self):
        _LOGGER.debug("Disconnecting")
        self._ws_state = ConnectionState.STOPPED
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        if self._websocket:
            await self._websocket.close()
            self._websocket = None
        if self._aiohttp_session:
            await self._aiohttp_session.close()
            self._aiohttp_session = None
        self._ws_state = ConnectionState.DISCONNECTED

    async def _listen(self) -> None:
        """Listen to messages"""
        # Infinite loop to listen to messages on the websocket and manage retries.
        self._failed_attempts = 0
        while self._ws_state != ConnectionState.STOPPED:
            await self._running()

    async def _running(self) -> None:
        self._ws_state_disconnecting = False
        self._ws_state = ConnectionState.STARTING        
        try:
            async with aiohttp.ClientSession() as session:
                self._aiohttp_session = session
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
                            _LOGGER.error(f"Error received from WS server: {message}")
                            self._cancel_pending_messages(Exception(f"Error received from WS server: {message}"))
                            break

                        if message.type == aiohttp.WSMsgType.CLOSED:
                            _LOGGER.warning("AIOHTTP websocket connection closed")
                            self._cancel_pending_messages(Exception("AIOHTTP websocket connection closed"))
                            break

                        if message.type == aiohttp.WSMsgType.TEXT:
                            _LOGGER.debug("Websocket received data: %s", message.data)
                            try:
                                await self._process_message(message)
                            except Exception as error:                                
                                _LOGGER.exception("Error processing message data: %s, data=%s" % (error, message.data))

                        if self._ws_state == ConnectionState.STOPPED:
                            break

        except asyncio.CancelledError:
            pass
        except aiohttp.ClientResponseError as error:
            self._cancel_pending_messages(error)
            _LOGGER.error("Unexpected response received from server : %s", error)
            self._ws_state = ConnectionState.DISCONNECTED
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError, aiohttp.client_exceptions.ClientConnectionResetError) as error:
            self._cancel_pending_messages(error)
            retry_delay = min(2**self._failed_attempts * 30, self.MAX_RETRY_DELAY)
            self._failed_attempts += 1
            _LOGGER.error(
                "Websocket connection failed, retrying in %ds: %s",
                retry_delay,
                error,
            )
            self._ws_state = ConnectionState.DISCONNECTED
            await asyncio.sleep(retry_delay)
        except Exception as error:
            self._cancel_pending_messages(error)
            #exstr = str(error)
            #if "'NoneType' object has no attribute 'connect'" in exstr:
            #    _LOGGER.debug("Unexpected exception occurred: %s", error)
            #    self._ws_state = ConnectionState.DISCONNECTED
            if self._ws_state != ConnectionState.STOPPED and self._ws_state != ConnectionState.DISCONNECTED:
                retry_delay = min(2**self._failed_attempts * 30, self.MAX_RETRY_DELAY)
                self._failed_attempts += 1
                _LOGGER.exception("Unexpected exception occurred, retrying in %ds: %s", retry_delay, error)
                self._ws_state = ConnectionState.DISCONNECTED
                await asyncio.sleep(retry_delay)
                
        finally:
            if self._websocket:
                await self._websocket.close()
            if self._ws_state != ConnectionState.STOPPED and self._ws_state != ConnectionState.DISCONNECTED:
                self._ws_state = ConnectionState.DISCONNECTED
            self._ws_state_disconnecting = False

    def _cancel_pending_messages(self, error : Exception):
        self._ws_state_disconnecting = True
        for id in self._messages:
            message = self._messages[id]
            if message.state == CommandState.STARTED:
                message.set_ko(error)

    async def _process_message(self, message):
        try:
            #parse_raw and parse_file are now deprecated. In Pydantic V2
            data = AmcCommandResponse.model_validate_json(message.data, strict=False)
        except ValueError as e:
            _LOGGER.warning(
                "Can't process data from server: %s, data=%s" % (e, message.data)
            )
            return

        status = self._get_message_info(data.command)

        match data.command:
            case AmcCommands.CHECK_CENTRALS:
                _LOGGER.debug("Received message %s" % data.command)        
                self._ws_state = ConnectionState.CONNECTED
                await self.ensure_logged()
                await self.command_get_states()
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
                    status.set_ok(data.user.token)
                else:
                    _LOGGER.warning("Authorization failure: %s, data=%s" % (data.status, message.data))
                    status.set_ko(AuthenticationFailed(data.status or message.data))
                    self._ws_state_stop_exeception = status.error
                    self._ws_state = ConnectionState.STOPPED


            case AmcCommands.GET_STATES:
                
                #Websocket received data: {"command":"getStates","status":"error","message":"not logged, please login"}
                if data.status == AmcCommands.STATUS_ERROR and data.message == AmcCommands.MESSAGE_PLEASE_LOGIN:
                    if self._last_login_date + timedelta(seconds=15) < datetime.now():
                        _LOGGER.debug("Logging after received request to relogin: %s" % (message.data))
                        self._ws_state = ConnectionState.CONNECTED
                        await self.ensure_logged()
                        await self.command_get_states()
                    return

                if not self._central_id in data.centrals:
                    _LOGGER.warning("GetStates failure, central not found: %s, data=%s" % (data.status, message.data))
                    status.set_ko(AmcCentralNotFoundException())
                    self._ws_state_stop_exeception = status.error
                    self._ws_state = ConnectionState.STOPPED
                    return

                if data.status == AmcCommands.STATUS_OK:
                    self._raw_states = data.centrals
                    self._raw_states_central_valid = True
                    status.set_ok(data.centrals)
                    if self._callback and not self._callback_get_states_disabled:
                        self._raw_states[self._central_id].returned = 0
                        await self._callback()
                elif data.status == AmcCommands.STATUS_KO:
                    #{"command":"getStates","status":"ko","layout":null,"centrals":{"10EF60834A5436323003323338310000":{"statusID":-1,"status":"not available"}}}
                    status_new = data.centrals[self._central_id].status if self._central_id in data.centrals else None
                    status_old = self._raw_states[self._central_id].status if self._central_id in self._raw_states else None
                    status.set_ko(AmcCentralStatusErrorException("Central " + status_new) if status_new else AmcException(message.data))
                    if status_new != status_old:
                        _LOGGER.debug("Error getting states (%s): %s" % (status_new, message.data))
                        if not self._central_id in self._raw_states:
                            self._raw_states = data.centrals
                        self._raw_states[self._central_id].status = status_new
                        if self._callback and not self._callback_get_states_disabled:
                            self._raw_states[self._central_id].returned = 0
                            await self._callback()
                    
                #    _LOGGER.debug("Error getting states (not available): %s" % data.centrals)
                    #await self.disconnect()
                else:
                    status.set_ko(AmcException(message.data))
                    _LOGGER.warning("Error getting states: %s, data=%s" % (data.centrals, message.data))
                    raise AmcException(data.centrals or message.data)
            case AmcCommands.APPLY_PATCH:
                if not self._raw_states_central_valid:
                    await self.command_get_states()
                    return
                try:
                    for patch in data.patch:
                        await self._process_message_patch(patch)
                except Exception as e:
                    _LOGGER.warning("Can't process patch from server: %s, patch=%s, data=%s" % (e, patch, message.data))
                if self._callback:
                    self._raw_states[self._central_id].returned = 0
                    await self._callback()
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
                #if exec_parse_obj:
                #    _LOGGER.debug("Changing %s %s attribute: old_value: %s, new_value=%s" % (curr_node, key, old_value, new_value))
            return new_obj
            #if hasattr(patch.value, "anomaly") and getattr(patch.value, "anomaly", None) != getattr(obj, "anomaly", None):
            #    _LOGGER.debug("Changing anomaly attribute: node: %s, data=%s" % (obj_parent, patch.value))
            #obj.parse_obj(patch.value)

    async def _login(self) -> CommandMessageInfo:
        self._sessionToken = None
        self._ws_state = ConnectionState.CONNECTED
        self._last_login_date = datetime.now()
        _LOGGER.info("Logging in with email: %s", self._login_email)
        login_message = AmcCommand(
            command=AmcCommands.LOGIN_USER,
            data=AmcLogin(email=self._login_email, password=self._password),
        )
        return await self._send_message(login_message)

    async def _get_message_info_result(self, message: CommandMessageInfo, timeout : int = 30):        
        for _ in range(timeout * 10):  # Wait 30 secs
            if self._ws_state in [
                ConnectionState.STOPPED,
            ]:
                break
            if message.state == CommandState.OK:
                return message.result
            if message.state == CommandState.KO:
                break
            await asyncio.sleep(0.1)
        if message.error:
            raise message.error        
        if self._ws_state_stop_exeception:
            raise self._ws_state_stop_exeception
        if self._listen_task.done() and issubclass(
            self._listen_task.exception().__class__, AmcException
        ):
            raise self._listen_task.exception()  # Something known happened in the listener

        raise asyncio.TimeoutError("Error processing command %s, response non received after timeout" % message.key)


    def _get_message_info(self, key: str) -> CommandMessageInfo:
        if not key in self._messages:
            status = CommandMessageInfo()
            status.key = key
            self._messages[key] = status
        return self._messages[key]

    async def _send_message(self, msg: AmcCommand, status: CommandMessageInfo = None) -> CommandMessageInfo:
        if not status:
            status = self._get_message_info(msg.command)
        status.state = CommandState.STARTED
        status.error = None
        try:
            if self._sessionToken:
                msg.token = self._sessionToken
            payload = msg.json(exclude_none=True, exclude_unset=True)
            _LOGGER.debug("Websocket sending data: %s", payload)
            await self._websocket.send_str(payload)
        except Exception as error:
            status.state = CommandState.KO
            status.error = error
            raise error
        return status

    async def command_get_states(self) -> CommandMessageInfo:
        return await self._send_message(
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

