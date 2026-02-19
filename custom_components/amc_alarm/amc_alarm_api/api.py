import asyncio
import logging
import json 
import time
from enum import Enum
from datetime import datetime, timedelta

import aiohttp
from aiohttp import WSMessage

from .amc_proto import *
from .exceptions import * #AmcException, ConnectionFailed, AuthenticationFailed, AmcCentralNotFoundException, AmcCentralStatusErrorException

_LOGGER = logging.getLogger(__name__)

# https://github.com/home-assistant-libs/zwave-js-server-python/blob/main/zwave_js_server/client.py
# https://github.com/Kane610/deconz/blob/master/pydeconz/websocket.py


class ConnectionState(Enum):
    STARTING = 0
    CONNECTED = 1
    AUTHENTICATED = 2
    DISCONNECTED = 3
    STOPPED = 4
    CENTRAL_OK = 5
    CENTRAL_KO = 6

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
    last_message_data : str = None 
    msg: AmcCommand = None
    request_time = None
    response_time = None

    def set_ok(self, res = None):
        self.result = res
        self.state = CommandState.OK
    def set_ko(self, err : Exception | str = None):
        if isinstance(err, str):
            err = Exception(err)
        self.error = err if err else self.error
        self.state = CommandState.KO

    def dict(self):
        return {
            "key": self.key,
            "state": getattr(self.state, "name", str(self.state)),  # se enum → name
            "request": self.msg,
            "last_message_data": safe_json_loads(self.last_message_data),
            "error": str(self.error) if self.error else None,
            "request_time": loop_time_to_datetime(self.request_time),
            "response_time": loop_time_to_datetime(self.response_time),
        }

class SimplifiedAmcApi:
    MAX_RETRY_DELAY = 600  # 10 min
    DEVICE_OFFLINE_DELAY = 5 # set as offline only after 5 seconds, many time request to relogin

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
        self._raw_states_centralstatus_valid : bool = False        
        self.raw_entities: dict[str, AmcEntry] = {}
        self._send_message_retrying : bool = False

        self._ws_url = "wss://service.amc-cloud.com/ws/client"
        self._login_email = login_email
        self._password = password
        self._central_id = central_id
        self._central_username = central_username
        self._central_password = central_password
        self.pin_required = False
        self.amcProtoVer = None

        self._listen_task = None
        self._checks_task = None
        self._checks_paused_to_date = None
        self._device_online_to_date = None
        self._device_available = False
        self._ws_state = ConnectionState.DISCONNECTED
        self._ws_state_detail = None
        self._aiohttp_session = None
        self._websocket = None
        self._sessionToken = None
        self._ws_state_disconnecting : bool = False
        self._ws_state_stop_exeception : Exception = None
        
        self._callback = async_state_updated_callback
        self._callback_get_states_disabled : bool = False
        self._last_login_date = None

        self.raw_states_json_model = None
        self.armed_any = False

        self._msg_quee_login : bool = False
        self._msg_quee_get_states : bool = False

        self._failed_attempts = 0
        self._failed_attempts_last_msg = None
        self._retry_delay = 0
        self._retry_from_date = None

        # default asyncio puro
        self._create_task = asyncio.create_task
        self._event_loop = asyncio.get_event_loop()
        self._create_future = self._event_loop.create_future

    def set_task_factory(self, create_task, event_loop):
        """Override quando sei dentro Home Assistant"""
        #client.set_task_factory(
        #    create_task=hass.async_create_task,
        #    event_loop=hass.loop
        #)
        self._create_task = create_task
        self._event_loop = event_loop
        self._create_future = self._event_loop.create_future


    async def connect(self):
        await self.ensure_logged()
        if not self._raw_states_central_valid:
            await self.command_get_states_and_return()

    async def _login_if_required(self) -> CommandMessageInfo:
        message = self._get_message_info(AmcCommands.LOGIN_USER)
        if self._ws_state != ConnectionState.STOPPED:
            #se sto nella pausa della connessione, devo aspettare
            if self._ws_state_disconnecting and self._ws_state != ConnectionState.STOPPED and self._listen_task and not self._listen_task.done():
                await asyncio.sleep(1)
                if self._ws_state_disconnecting and self._ws_state != ConnectionState.STOPPED and self._listen_task and not self._listen_task.done():
                    raise asyncio.TimeoutError("Waiting timeout for server connection")
                    
            if not self._listen_task or self._listen_task.done():
                await self._change_state(ConnectionState.STARTING)
                self._listen_task = self._create_task(self._listen())
            elif self._ws_state == ConnectionState.CONNECTED:
                message = await self._login()

            await self._listen_start()
        return message

    async def ensure_logged(self):
        message = await self._login_if_required()
        await self._get_message_info_result(message)

    async def _ensure_central_ok(self, timeout=5):
        if (self._ws_state == ConnectionState.CENTRAL_OK):
            return
        end_time = self._event_loop.time() + timeout
        while True:
            if (self._ws_state == ConnectionState.CENTRAL_OK):
                return
            if (self._ws_state == ConnectionState.STOPPED):
                break
            if self._event_loop.time() >= end_time:
                break # timeout scaduto
            await asyncio.sleep(0.1)
        if self._ws_state_stop_exeception:
            raise self._ws_state_stop_exeception
            
        # Error in task listener
        if self._listen_task.done():
            exc = self._listen_task.exception()
            if exc:
                raise exc

        raise asyncio.TimeoutError(f"Error waiting state {state}. Current state {self._ws_state} {self._ws_state_detail}")
            

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
        await self._change_state(ConnectionState.STOPPED)
        if self._listen_task and not self._listen_task.done():
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None
        if self._checks_task and not self._checks_task.done():
            self._checks_task.cancel()
            try:
                await self._checks_task
            except asyncio.CancelledError:
                pass
            self._checks_task = None
        if self._websocket:
            await self._websocket.close()
            self._websocket = None
        if self._aiohttp_session:
            await self._aiohttp_session.close()
            self._aiohttp_session = None
        #await self._change_state(ConnectionState.DISCONNECTED)

    async def _listen_start(self) -> None:
        if self._ws_state != ConnectionState.STOPPED:
            #se sto nella pausa della connessione, devo aspettare
            #if self._ws_state_disconnecting and self._ws_state != ConnectionState.STOPPED and self._listen_task and not self._listen_task.done():
            #    await asyncio.sleep(1)
            #    if self._ws_state_disconnecting and self._ws_state != ConnectionState.STOPPED and self._listen_task and not self._listen_task.done():
            #        raise asyncio.TimeoutError("Waiting timeout for server connection")
                    
            if not self._listen_task or self._listen_task.done():
                await self._change_state(ConnectionState.STARTING)
                self._listen_task = self._create_task(self._listen())
                
            if not self._checks_task or self._checks_task.done():
                self._checks_task = self._create_task(self._checks())


    async def _listen(self) -> None:
        """Listen to messages"""
        # Infinite loop to listen to messages on the websocket and manage retries.
        self._failed_attempts = 0
        while self._ws_state != ConnectionState.STOPPED:
            await self._running()

    async def _running(self) -> None:
        self._ws_state_disconnecting = False
        await self._change_state(ConnectionState.STARTING)
        if not self._aiohttp_session:
            self._aiohttp_session = aiohttp.ClientSession()
        try:
            #_LOGGER.debug("Logging into %s" % self._ws_url)
            async with self._aiohttp_session.ws_connect(
                self._ws_url, heartbeat=30, autoping=True
            ) as ws_client:
                self._websocket = ws_client
                _LOGGER.debug("Connected to websocket %s" % self._ws_url)
                self._checks_pause()
                await self._change_state(ConnectionState.CONNECTED)

                self._sessionToken = None  #can't reuse the last login, need to relogin after disconnection
                self._msg_quee_login = True
                self._msg_quee_get_states = True
                await self._send_msg_quee()

                message: WSMessage
                async for message in ws_client:
                    if self._ws_state == ConnectionState.STOPPED:
                        break

                    self._checks_pause()

                    if message.type == aiohttp.WSMsgType.ERROR:
                        #self._sessionToken = None
                        await self._manage_running_error("Error received from WS server", Exception(f"WSMessageError: {message}"))
                        break

                    if message.type == aiohttp.WSMsgType.CLOSED:
                        await self._manage_running_error("AIOHTTP websocket connection closed", Exception("AIOHTTP websocket connection closed"))
                        break

                    if message.type == aiohttp.WSMsgType.TEXT:
                        _LOGGER.debug("Websocket received data: %s", message.data)
                        try:
                            await self._process_message(message)
                        except Exception as error:
                            _LOGGER.exception("Error processing message data: %s, data=%s" % (error, message.data))

                    if self._ws_state == ConnectionState.STOPPED or self._ws_state == ConnectionState.DISCONNECTED:
                        break
                    
                    await self._send_msg_quee()
                        
        except asyncio.CancelledError:
            pass
        except aiohttp.ClientResponseError as error:
            await self._manage_running_error("Unexpected response received from server", error)
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError, aiohttp.client_exceptions.ClientConnectionResetError) as error:
            await self._manage_running_error("Websocket connection failed", error)
        except Exception as error:
            await self._manage_running_error("Unexpected exception occurred", error)
        finally:
            if self._websocket:
                await self._websocket.close()
                self._websocket = None
            if self._ws_state != ConnectionState.STOPPED and self._ws_state != ConnectionState.DISCONNECTED:
                await self._change_state(ConnectionState.DISCONNECTED)
            self._ws_state_disconnecting = False

    
    async def _manage_running_error(self, msg, error) -> None:
        err_type = type(error).__name__
        err_msg = f"{err_type}: {error}"
        self._cancel_pending_messages(error)
        if self._ws_state in (ConnectionState.STOPPED, ConnectionState.DISCONNECTED):
            return
        #if the error is different, restart immediatly and log, ignoring multiple logs
        if self._failed_attempts_last_msg != err_msg or self._failed_attempts == 0:
            self._failed_attempts = 0
            self._failed_attempts_last_msg = err_msg
            _LOGGER.exception("%s: %s", msg, error)
        self._failed_attempts += 1
        self._retry_delay = self.get_retry_delay()
        self._retry_from_date = datetime.now() + timedelta(seconds=self._retry_delay)
        _LOGGER.debug("%s, retrying in %ss: %s", msg, self._retry_delay, error)
        await self._change_state(ConnectionState.DISCONNECTED, err_msg)
        await asyncio.sleep(self._retry_delay)
        self._retry_from_date = None

        

    def get_retry_delay(self) -> int:
        """
        Compute the retry delay based on the number of failed attempts.
        Rules:
        - First 10 attempts: retry every 1 second.
        - Next 10 minutes (20 attempts): retry every 30 seconds.
        - After that: exponential backoff starting from 60 seconds,
        doubling each time until reaching max_backoff.

        Args:
            failed_attempts (int): Number of consecutive failed attempts.
            max_backoff (int): Maximum allowed delay in seconds (default: 3600 = 1h).

        Returns:
            int: Delay in seconds before the next retry.
        """
        if self._failed_attempts <= 2:
            return 0
        if self._failed_attempts <= 10:
            return 1
        if self._failed_attempts <= 10 + (10 * 60) // 30:
            return 30
        backoff_attempts = self._failed_attempts - (10 + 20)
        return min(2**backoff_attempts * 60, self.MAX_RETRY_DELAY)

    def _checks_pause(self):
        self._checks_paused_to_date = self._event_loop.time() + 1

    async def _checks(self) -> None:
        """Execute background checks"""
        while self._ws_state != ConnectionState.STOPPED:
            p = self._checks_paused_to_date
            await asyncio.sleep(0.05) #50ms
            if p and self._checks_paused_to_date == p and p < self._event_loop.time():
                try:
                    if self._device_online_to_date:
                        await self._set_device_available(True)
                except asyncio.CancelledError:
                    pass
                except Exception as e:
                    _LOGGER.error("Error in task checks: %s" % (e))
            await asyncio.sleep(1)

    
    def _cancel_pending_messages(self, error : Exception):
        self._ws_state_disconnecting = True
        for id in self._messages:
            message = self._messages[id]
            if message.state == CommandState.STARTED:
                message.set_ko(error)


    async def _set_device_available(self, call_callback):
        avaiable = self._ws_state == ConnectionState.CENTRAL_OK
        if self._device_online_to_date:
            if not avaiable and self._device_online_to_date >= self._event_loop.time():
                avaiable = True
            elif self._device_online_to_date < self._event_loop.time():
                self._device_online_to_date = None

        if avaiable != self._device_available:
            self._device_available = avaiable
            if self._callback and not self._callback_get_states_disabled and call_callback:
                await self._callback()


    async def _send_msg_quee(self):

        if self._msg_quee_login or not self._sessionToken or self._ws_state == ConnectionState.CONNECTED:
            if self._ws_state in (ConnectionState.CONNECTED, ConnectionState.AUTHENTICATED, ConnectionState.CENTRAL_OK, ConnectionState.CENTRAL_KO):
                await self._login()
                self._msg_quee_login = False
            return
            
        if self._msg_quee_get_states:
            if self._ws_state in (ConnectionState.AUTHENTICATED, ConnectionState.CENTRAL_OK, ConnectionState.CENTRAL_KO):
                await self.command_get_states()
                self._msg_quee_get_states = False
                return


    async def _change_state(self, wsstate, detailmsg = None):
        if not detailmsg and self._ws_state == wsstate:
            detailmsg = self._ws_state_detail
        if self._ws_state != wsstate or self._ws_state_detail != detailmsg:
            if wsstate in (ConnectionState.CENTRAL_OK, ConnectionState.STOPPED):
                self._device_online_to_date = None
            elif self._ws_state == ConnectionState.CENTRAL_OK:
                self._device_online_to_date = self._event_loop.time() + self.DEVICE_OFFLINE_DELAY
            self._ws_state = wsstate
            self._ws_state_detail = detailmsg
            await self._set_device_available(False)
            if self._callback and not self._callback_get_states_disabled:
                await self._callback()

    async def _data_changed(self):
        if self._callback and not self._callback_get_states_disabled:
            if self._central_id in self._raw_states:
                self._raw_states[self._central_id].returned = 0            
            await self._set_device_available(False)
            await self._callback()




    async def _process_message(self, message):
        try:
            #parse_raw and parse_file are now deprecated. In Pydantic V2
            data = AmcCommandResponse.model_validate_json(message.data, strict=False)
        except ValueError as e:
            failed = True
            try:                
                #same times arrive a wrong GET_STATES response message with only centrals!
                if message.data and message.data.startswith(f'{{"{self._central_id}":') and "statusID" in message.data:
                    new_data = '{"command": "getStates","status": "ok","layout": null,"centrals": ' + message.data + '}'
                    data = AmcCommandResponse.model_validate_json(new_data, strict=False)
                    failed = False
                    message = WSMessage(message.type, new_data, message.extra)
                    _LOGGER.warning("Fixed getStates message with only centrals received. data=%s" % message.data)
            except Exception as error_fix:
                _LOGGER.exception("Error fixing message data: %s, data=%s" % (error_fix, message.data))
            if failed:
                _LOGGER.warning(
                    "Can't process data from server: %s, data=%s" % (e, message.data)
                )
                return

        status = self._get_message_info(data.command)
        status.last_message_data = message.data
        status.response_time = self._event_loop.time()

        match data.command:
            case AmcCommands.CHECK_CENTRALS:
                _LOGGER.debug("Received message %s" % data.command)
                self._msg_quee_get_states = True
                status.set_ok(data.command)
            case "updateVideoList":
                _LOGGER.debug("Received message %s" % data.command)
                status.set_ok(data.command)
            case "visitedOK":
                _LOGGER.debug("Received message %s" % data.command)
                status.set_ok(data.command)
            case AmcCommands.LOGIN_USER:
                if data.status == AmcCommands.STATUS_LOGGED_IN:
                    _LOGGER.debug("Authorized")
                    self._sessionToken = data.user.token
                    await self._change_state(ConnectionState.AUTHENTICATED)
                    status.set_ok(data.user.token)
                    self._msg_quee_get_states = True
                else:
                    _LOGGER.warning("Authorization failure: %s, data=%s" % (data.status, message.data))
                    self._ws_state_stop_exeception = AuthenticationFailed(data.status or message.data)
                    await self._change_state(ConnectionState.STOPPED, f"Authorization failure: : {data.status}")
                    status.set_ko(self._ws_state_stop_exeception)
            case AmcCommands.GET_STATES:
                
                #Websocket received data: {"command":"getStates","status":"error","message":"not logged, please login"}
                if data.status == AmcCommands.STATUS_ERROR and data.message == AmcCommands.MESSAGE_PLEASE_LOGIN:
                    if self._last_login_date + timedelta(seconds=15) < datetime.now():
                        _LOGGER.debug("Logging after received request to relogin: %s" % (message.data))
                        await self._change_state(ConnectionState.CONNECTED, "Received request to relogin")
                        self._msg_quee_login = True
                        self._msg_quee_get_states = True
                    else:
                        await self._change_state(ConnectionState.DISCONNECTED, "Received many request to relogin")
                    return

                if not self._central_id in data.centrals:
                    _LOGGER.warning("GetStates failure, central not found: %s, data=%s" % (data.status, message.data))
                    self._ws_state_stop_exeception = AmcCentralNotFoundException("User login is fine but can't find AMC Central.")
                    await self._change_state(ConnectionState.STOPPED, "Central not found")
                    status.set_ko(self._ws_state_stop_exeception)
                    return
                
                #if self._central_pin and data.status == AmcCommands.STATUS_OK:  # only for amcProtoVer >= 2
                #    #_LOGGER.debug("User pin: %s - %s" % (userPin, str(len(userPin))))
                #    states = AmcStatesParser(data.centrals)
                #    err = None
                #    if states.users(centralId) is None:
                #        err = AuthenticationFailed("User PIN not allowed, users not received")
                #    elif not states.user_by_pin(centralId, userPin):
                #        err = AuthenticationFailed("User PIN not valid")
                #    if err:
                #        self._ws_state_stop_exeception = err
                #        await self._change_state(ConnectionState.STOPPED)
                #        status.set_ko(self._ws_state_stop_exeception)
                #        return
                
                #in wizard if use a wrong centralid, receive a response but with not avaiable
                if data.status == AmcCommands.STATUS_OK and data.centrals[self._central_id].statusID <= 0:
                    data.status = AmcCommands.STATUS_KO

                if data.status == AmcCommands.STATUS_OK:
                    if not self._raw_states_central_valid:
                        #lo controllo solo la prima volta
                        states = AmcStatesParser(data.centrals)
                        self.amcProtoVer = data.centrals[self._central_id].amcProtoVer or 1
                        if states.users(self._central_id) or self.amcProtoVer >= 2:
                            self.pin_required = True

                    self.raw_states_json_model = json.loads(message.data)
                    self._raw_states = data.centrals
                    self._raw_states_central_valid = True
                    self._raw_states_centralstatus_valid = True
                    self._failed_attempts = 0
                    await self._set_calculated_states()
                    status.set_ok(data.centrals)
                    await self._change_state(ConnectionState.CENTRAL_OK)
                    await self._data_changed()
                elif data.status == AmcCommands.STATUS_KO:
                    #{"command":"getStates","status":"ko","layout":null,"centrals":{"10EF60834A5436323003323338310000":{"statusID":-1,"status":"not available"}}}
                    statusID_new = data.centrals[self._central_id].statusID if self._central_id in data.centrals else None
                    status_new = data.centrals[self._central_id].status if self._central_id in data.centrals else None
                    status_old = self._raw_states[self._central_id].status if self._central_id in self._raw_states else None                    
                    if status_new:
                        self._raw_states_centralstatus_valid = True
                    if status_new != status_old:
                        _LOGGER.debug("Error getting states (%s): %s" % (status_new, message.data))
                        if not self._central_id in self._raw_states:
                            self._raw_states = data.centrals
                            self.raw_states_json_model = json.loads(message.data)
                        self.raw_states_json_model["centrals"][self._central_id]["statusID"] = statusID_new
                        self.raw_states_json_model["centrals"][self._central_id]["status"] = status_new
                        self._raw_states[self._central_id].statusID = statusID_new
                        self._raw_states[self._central_id].status = status_new
                    # {"command":"getStates","status":"ok","layout":null,"centrals":{"XXX":{"amcProtoVer":2,"realName":"X864V","statusID":0,"status":"wrong login X864V/4.10"}}}
                    if status_new and status_new.startswith("wrong login"):                        
                        _LOGGER.warning("Central Authorization failure: %s, data=%s" % (data.status, message.data))
                        self._ws_state_stop_exeception = AuthenticationFailed(f"Central Authorization failure: {status_new}")
                        await self._change_state(ConnectionState.STOPPED, f"Central Authorization failure: {status_new}")
                        status.set_ko(self._ws_state_stop_exeception)
                    else:
                        await self._change_state(ConnectionState.CENTRAL_KO, f"Central {status_new}")
                        status.set_ko(AmcCentralStatusErrorException("Central " + status_new) if status_new else AmcException(message.data))
                else:
                    _LOGGER.warning("Error getting states: %s, data=%s" % (data.centrals, message.data))
                    status.set_ko(AmcException(message.data))
                    await self._change_state(ConnectionState.DISCONNECTED, f"Central States Status {data.status}")
            case AmcCommands.APPLY_PATCH:
                if self._ws_state != ConnectionState.CENTRAL_OK:
                    self._msg_quee_get_states = True
                    return
                try:
                    path_json_model = json.loads(message.data)
                    for patch in path_json_model["patch"]:
                        try:
                            self.raw_states_json_model = await self._process_json_patch(self.raw_states_json_model, patch)
                        except Exception as e:
                            _LOGGER.warning("Can't process patch from server: %s, patch=%s, data=%s" % (e, patch, message.data))
                            self._msg_quee_get_states = True
                    states_json = json.dumps(self.raw_states_json_model)
                    states_data = AmcCommandResponse.model_validate_json(states_json, strict=False)
                    #_LOGGER.warning("Applied patch: patch=%s, data=%s, old_data=%s" % (message.data, states_json, self.message_getstates_ok_data))
                    self._raw_states = states_data.centrals
                    await self._set_calculated_states()
                    await self._data_changed()
                except Exception as ee:
                    _LOGGER.warning("Can't process patch from server: %s, data=%s" % (ee, message.data))
                    self._msg_quee_get_states = True                
            case _:
                _LOGGER.warning("Unknown command received from server : %s, data=%s" % (data, message.data))
                status.set_ko(AmcException(f"Unknown command received from server : {data.command} - {message.data}"))


    def _get_entity_state(self, group: int, index: int) -> bool:
        filter_id = f"{group}.{index}"
        item = self.raw_entities[filter_id]
        return item.states.bit_on == 1
        

    async def _set_calculated_states(self):
        state = AmcStatesParser(self.raw_states())
        groups = state.groups(self._central_id).list
        areas = state.areas(self._central_id).list
        zones = state.zones(self._central_id).list
        outputs = state.outputs(self._central_id).list
        all_entries = [*zones, *areas, *groups]
        for item in [*all_entries, *outputs]:
            item.filter_id = f"{item.group}.{item.index}"
            self.raw_entities[item.filter_id] = item
        self.armed_any = False
        for item in [*groups, *areas]:
            item.arm_state = AmcAlarmState.Armed if item.states.bit_on == 1 else AmcAlarmState.Disarmed
            self.armed_any = self.armed_any or item.arm_state == AmcAlarmState.Armed
        for item in zones:
            item.arm_state = AmcAlarmState.Armed if item.states.bit_armed == 1 and item.states.bit_on == 1 else AmcAlarmState.Disarmed
        if self.armed_any:
            any_arming = False
            for item in areas:
                #notification is only for area, then search parents group and childs zones
                if item.arm_state == AmcAlarmState.Armed and self._is_state_arming(item):
                    item.arm_state = AmcAlarmState.Arming
                    any_arming = True
                    id_str = item.filter_id
                    filtered_entries = [ e for e in zones if e.filters and id_str in e.filters and e.arm_state == AmcAlarmState.Armed ] 
                    for e in filtered_entries: 
                        e.arm_state = AmcAlarmState.Arming

            if any_arming:
                for item in groups:
                    if item.arm_state == AmcAlarmState.Armed:
                        id_str = item.filter_id                
                        if any(e.filters and id_str in e.filters and e.arm_state == AmcAlarmState.Arming for e in areas):
                            item.arm_state = AmcAlarmState.Arming            

            for item in all_entries:
                if item.arm_state == AmcAlarmState.Arming and item.states.anomaly == 1:
                    item.arm_state = AmcAlarmState.ArmingWithProblem
                if item.arm_state == AmcAlarmState.Armed and item.states.anomaly == 1:
                    item.arm_state = AmcAlarmState.Triggered

        for id in self._messages:
            message = self._messages[id]
            if message.state == CommandState.STARTED and message.msg and message.msg.command=="setStates":
                new_state = self._get_entity_state(message.msg.group, message.msg.index)
                if new_state == message.msg.state:
                    message.response_time = self._event_loop.time()
                    message.set_ok(new_state)


        
                
    def _is_state_arming(self, entry):
        if entry.notifications and len(entry.notifications) > 0:
            msg = entry.notifications[0].name.strip()
            name = entry.name.strip()
            if msg in (f"Inserimento {name}", f"Inserimento Forzato {name}", f"Arming {name}"):
                return True
            if msg in (f"Inserimento Concluso {name}", f"Arming Finished {name}"):
                return False
        return False


    async def _process_json_patch(self, data, p):
        """Apply a list of JSON patches to a dict without using jsonpatch."""

        # Examples:
        # { "op": "replace", "path": "/centrals/10EF60834A5436323003323338310000/data/2/list/19/states", "value": {"redalert":0,"progress":0,"bit_showHide":1,"bit_on":1,"bit_exludable":1,"bit_armed":0,"anomaly":1,"bit_opened":1,"bit_notReady":0,"video":0} }
        # { "op": "add", "path": "/centrals/10EF60834A5436323003323338310000/data/2/list/23/notifications/0", "value": {"command":"notification","name":"Inibizione balcone tamper","category":4,"serverDate":"Thu, 18 Sep 2025 10:10:33 +0200","centralDate":"2025-09-17 10:13:43 +0000","centralGroup":2,"centralIndex":23,"states":{"anomaly":1,"bit_showHide":1,"redalert":1}} }
        # { "op": "add", "path": "/centrals/10EF60834A5436323003323338310000/data/5/list/0", "value": {"command":"notification","name":"Inibizione balcone tamper","category":4,"serverDate":"Thu, 18 Sep 2025 10:10:33 +0200","centralDate":"2025-09-17 10:13:43 +0000","centralGroup":2,"centralIndex":23,"states":{"anomaly":1,"bit_showHide":1,"redalert":1}} }
        # { "op": "replace", "path": "/centrals/10EF60834A5436323003323338310000/data/5/unvisited", "value": "8" }

        #for p in patch:
        op = p["op"]
        path = p["path"].strip("/").split("/")
        value = p.get("value")

        # naviga nell'albero fino al penultimo nodo
        target = data
        for key in path[:-1]:
            if key.isdigit():
                key = int(key)
                if isinstance(target, list):
                    key = _find_pos_by_item_index(target, key)
            target = target[key]

        last_key = path[-1]
        if last_key.isdigit():
            last_key = int(last_key)
            if op in ("replace", "remove") and isinstance(target, list):
                key = _find_pos_by_item_index(target, key)

        # operazioni base
        if op == "add" and isinstance(target, list) and isinstance(last_key, int):
            target.insert(last_key, value)
        elif op == "add" or op == "replace":
            if op == "replace" and isinstance(target[last_key], dict):
                new_value = target[last_key]                    
                new_value.update(value)
                value = new_value
            target[last_key] = value
        elif op == "remove":
            if isinstance(target, list) and isinstance(last_key, int):
                target.pop(last_key)
            else:
                target.pop(last_key, None)

        else:
            raise ValueError(f"Operazione non supportata: {op}")
        
        return data

    async def _login(self) -> CommandMessageInfo:
        self._sessionToken = None
        await self._change_state(ConnectionState.CONNECTED)
        self._last_login_date = datetime.now()
        _LOGGER.info("Logging in with email: %s", self._login_email)
        login_message = AmcCommand(
            command=AmcCommands.LOGIN_USER,
            data=AmcLogin(email=self._login_email, password=self._password),
        )
        return await self._send_message(login_message)

    async def _get_message_info_result(self, message: CommandMessageInfo, timeout : int = 30):        
        for _ in range(timeout):  # Wait 30 secs
            if self._ws_state in [
                ConnectionState.STOPPED,
            ]:
                break
            if message.state == CommandState.OK:
                return message.result
            if message.state == CommandState.KO:
                break
            await asyncio.sleep(1)
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
        status.request_time = self._event_loop.time()
        status.error = None
        status.msg = msg

        payload = ""
        try:
            if self._sessionToken:
                msg.token = self._sessionToken
            payload = msg.json(exclude_none=True, exclude_unset=True)
            _LOGGER.debug("Websocket sending data: %s", payload)
            await self._websocket.send_str(payload)
        except (aiohttp.ClientConnectionError, asyncio.TimeoutError, aiohttp.client_exceptions.ClientConnectionResetError) as error:
            if not self._send_message_retrying:
                try:
                    self._send_message_retrying = True
                    # for handle raise ClientConnectionResetError("Cannot write to closing transport")                    
                    _LOGGER.info("Websocket send failed. Retry to send data: %s - Error: %s", payload, error)
                    await asyncio.sleep(0.5)
                    await self.ensure_logged()
                    await self._send_message(msg, status)
                    return status
                except Exception as error1:
                    _LOGGER.info("Websocket connection failed in _send_message. Resend failed...: %s", error1)
                    pass
                finally:
                    await asyncio.sleep(0.2)
                    self._send_message_retrying = False
                    
            status.state = CommandState.KO
            status.error = error
            status.response_time = self._event_loop.time()
            raise error
        except Exception as error:
            status.state = CommandState.KO
            status.error = error
            status.response_time = self._event_loop.time()
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
        userIdx=None
        if self.pin_required:
            if not userPIN:
                raise Exception("PIN not specified.")
            user=AmcStatesParser(self.raw_states()).user_by_pin(self._central_id, userPIN)
            if not user:
                raise Exception("PIN not valid.")
            userIdx=user.index
        elif userPIN:
            raise Exception("PIN not allowed.")
        
        #waiting for state, if is in reconnecting state, device is avaiable but is reconnecting
        await self._ensure_central_ok()
        status = self._get_message_info(f"setStates_{group}_{index}")

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
            ),
            status
        )
    
    def raw_states(self) -> dict[str, AmcCentralResponse]:
        return self._raw_states

    def _get_status_info_dict(self):
        central_data = self._raw_states[self._central_id] if self._raw_states and self._central_id in self._raw_states else None
        return {
            "ws_state": getattr(self._ws_state, "name", "unknown"),
            "ws_state_detail": self._ws_state_detail,
            "central_status": getattr(central_data, "status", None),
            "central_statusID": getattr(central_data, "statusID", None),
            "failed_attempts": self._failed_attempts,
            "retry_delay_seconds": self._retry_delay,
            "retry_from_date": self._retry_from_date
        }

    


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

    def status_is_error(self, central_id: str) -> bool:
        status = self._raw_states[central_id].statusID
        if not status:
            return True
        if status == 1:
            return False
        return True

    def model(self, central_id: str) -> str:
        # Assuming from status
        return self._raw_states[central_id].status.split(" ")[-1]

    def version(self, central_id: str) -> str:
        # Assuming from status
        return self._raw_states[central_id].status.split("/")[-1]
        
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
        else:
            user.pin = userPin
        return user
        
    def user_pin_by_index(self, central_id: str, userIndex: int) -> AmcUserEntry:        
        #_LOGGER.debug("user_by_pin: Pin '%s'" % userPin)
        if userIndex is None or userIndex < 0:
            return None        
        users = self.users(central_id)        
        if users:
            for key, value in users.items():
                if value.index == userIndex:
                    return key
        _LOGGER.warning("Cannot find User By Index: Index '%s'" % userIndex)
        return None


def safe_json_loads(value: str):
    """Try to convert the JSON string to dict,
    otherwise return the original string."""
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value

def _find_pos_by_item_index(lst, index_value) -> int | None:
    """Search in lst for the element with field 'index' == index_value and return the position, otherwise None."""
    for i, item in enumerate(lst):
        if isinstance(item, dict):
            # confronto flessibile su stringa/int
            if item.get("index") is not None and str(item.get("index")) == str(index_value):
                return i
    return None


def loop_time_to_datetime(loop_time: float) -> datetime:
    if not loop_time:
        return loop_time
    """
    Convert an asyncio event loop time (loop.time()) to a real datetime.
    
    Args:
        loop_time (float): The timestamp from loop.time()
    
    Returns:
        datetime: Corresponding datetime in local time
    """
    # Calcola l'offset tra monotonic e tempo reale
    offset = time.time() - time.monotonic()
    
    # Trasforma loop_time in timestamp UNIX
    timestamp = loop_time + offset
    
    # Restituisce il datetime leggibile
    return datetime.fromtimestamp(timestamp)