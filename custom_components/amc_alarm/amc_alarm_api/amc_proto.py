from enum import StrEnum
from typing import Union, Optional, List

from pydantic import BaseModel


class AmcCommands(StrEnum):
    LOGIN_USER = "loginUser"
    GET_STATES = "getStates"
    STATUS_OK = "ok"
    STATUS_LOGGED_IN = "Logged"
    STATUS_LOGIN_NOT_FOUND = "User not found"


class AmcState(BaseModel):
    redalert: int
    bit_showHide: int
    bit_on: int
    bit_exludable: int
    bit_armed: int
    anomaly: int
    bit_opened: int
    bit_notReady: int
    remote: bool
    progress: Optional[int]


class AmcEntry(BaseModel):
    index: int
    name: str
    Id: int
    states: AmcState

    def __str__(self) -> str:
        return f"({self.index}){self.name} [{'ARMED' if self.states.bit_armed else 'Disarm'} {'Open' if self.states.bit_opened else 'Closed'}]"


class AmcData(BaseModel):
    index: int
    name: str
    list: list[AmcEntry]


class AmcNotificationEntry(BaseModel):
    name: str
    category: int
    serverDate: str


class AmcNotification(BaseModel):
    index: int
    name: str
    list: list[AmcNotificationEntry]


class AmcCentral(BaseModel):
    centralID: str
    centralUsername: str
    centralPassword: str


class AmcCentralResponse(BaseModel):
    status: str
    realName: Optional[str] = None
    data: Optional[list[Union[AmcData, AmcNotification]]] = None


class AmcUser(BaseModel):
    email: str
    password: str
    regUrl: Optional[str] = None
    surname: Optional[str] = None
    name: Optional[str] = None
    regCode: Optional[str] = None
    random: Optional[str] = None
    userState: str
    token: str


class AmcLogin(BaseModel):
    email: str
    password: str


class AmcCommand(BaseModel):
    command: str
    data: Optional[AmcLogin] = None
    token: Optional[str] = None

    centrals: Optional[List[AmcCentral]] = None
    centralID: Optional[str] = None
    centralUsername: Optional[str] = None
    centralPassword: Optional[str] = None

    group: Optional[int] = None
    index: Optional[int] = None
    state: Optional[bool] = None


class AmcCommandResponse(BaseModel):
    command: str
    status: Optional[str] = None
    centrals: Optional[dict[str, AmcCentralResponse]] = None
    user: Optional[AmcUser] = None
    token: Optional[str] = None


class CentralDataSections:
    GROUPS = 0
    AREAS = 1
    ZONES = 2
    OUTPUTS = 3
    SYSTEM_STATUS = 4
    NOTIFICATIONS = 5

    __all__ = [GROUPS, AREAS, OUTPUTS, SYSTEM_STATUS, NOTIFICATIONS]


class SystemStatusDataSections:
    GSM_SIGNAL = 0  # _(index=, entity_prefix="GSM Signal")
    BATTERY_STATUS = 1  # _(index=, entity_prefix="Battery Status")
    POWER = 2  # _(index=, entity_prefix="Power")
    PHONE_LINE = 3  # _(index=, entity_prefix="Phone Line")
    PANEL_MANIPULATION = 4  # _(index=, entity_prefix="Panel Manipulation")
    LINE_MANIPULATION = 5  # _(index=, entity_prefix="Line Manipulation")
    PERIPHERALS = 6  # _(index=, entity_prefix="Peripherals")
    CONNECTIONS = 7  # _(index=, entity_prefix="Connections")
    WIRELESS = 8  # _(index=, entity_prefix="Wireless")

    __all__ = [
        GSM_SIGNAL,
        BATTERY_STATUS,
        POWER,
        PHONE_LINE,
        PANEL_MANIPULATION,
        LINE_MANIPULATION,
        PERIPHERALS,
        CONNECTIONS,
        WIRELESS,
    ]
