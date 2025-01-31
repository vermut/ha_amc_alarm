from enum import StrEnum
from typing import Optional, List, Literal

from pydantic import BaseModel


class AmcCommands(StrEnum):
    LOGIN_USER = "loginUser"
    GET_STATES = "getStates"
    APPLY_PATCH = "applyPatch"
    CHECK_CENTRALS = "checkCentrals"
    STATUS_OK = "ok"
    STATUS_KO = "ko"
    STATUS_ERROR = "error"
    STATUS_LOGGED_IN = "Logged"
    STATUS_LOGIN_NOT_FOUND = "User not found"
    STATUS_NOT_AVAILABLE = "not available"
    MESSAGE_PLEASE_LOGIN = "not logged, please login"


class AmcState(BaseModel):
    redalert: Optional[int]
    bit_showHide: int
    bit_on: int
    bit_exludable: int
    bit_armed: int
    anomaly: int
    bit_opened: int
    bit_notReady: int
    remote: Optional[bool]
    progress: Optional[int]


class AmcEntry(BaseModel):
    index: int
    name: str
    Id: int
    states: AmcState

    def __str__(self) -> str:
        return f"({self.index}){self.name} [{'ARMED' if self.states.bit_armed else 'Disarm'} {'Open' if self.states.bit_opened else 'Closed'}]"


class AmcData(BaseModel):
    index: Literal[0, 1, 2, 3]
    name: str
    list: list[AmcEntry]


class AmcSystemStateEntry(BaseModel):
    index: int
    name: str
    Id: Optional[int]
    states: AmcState


class AmcSystemState(BaseModel):
    index: Literal[4]
    name: str
    list: list[AmcSystemStateEntry]


class AmcNotificationEntry(BaseModel):
    name: str
    category: int
    serverDate: str


class AmcNotification(BaseModel):
    index: Literal[5]
    name: str
    list: list[AmcNotificationEntry]


class AmcStatusEntry(BaseModel):
    index: Literal[6]
    name: str
    model: int
    firmwareVersion: str


class AmcUserEntry(BaseModel):
    index: Optional[int]
    name: Optional[str]


class AmcUsers(BaseModel):
    index: Literal[7]
    users: dict[str, AmcUserEntry]


class AmcCentral(BaseModel):
    centralID: str
    centralUsername: str
    centralPassword: str


class AmcCentralResponse(BaseModel):
    status: str
    realName: Optional[str] = None
    generalStates: Optional[dict] = None
    data: Optional[
        list[AmcData | AmcSystemState | AmcNotification | AmcStatusEntry | AmcUsers]
    ] = None
    returned: Optional[int] = None


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

class AmcPatch(BaseModel):
    op: str
    path: str
    value: dict | str | int

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

    userPIN: Optional[str] = None
    userIdx: Optional[int] = None


class AmcCommandResponse(BaseModel):
    command: str
    status: Optional[str] = None
    message: Optional[str] = None
    centrals: Optional[dict[str, AmcCentralResponse]] = None
    user: Optional[AmcUser] = None
    token: Optional[str] = None
    patch: Optional[List[AmcPatch]] = None


class CentralDataSections:
    GROUPS = 0
    AREAS = 1
    ZONES = 2
    OUTPUTS = 3
    SYSTEM_STATUS = 4
    NOTIFICATIONS = 5
    USERS = 7

    __all__ = [GROUPS, AREAS, OUTPUTS, SYSTEM_STATUS, NOTIFICATIONS]


class SystemStatusDataSections:
    GSM_SIGNAL = 0
    BATTERY_STATUS = 1
    POWER = 2
    PHONE_LINE = 3
    PANEL_MANIPULATION = 4
    LINE_MANIPULATION = 5
    PERIPHERALS = 6
    CONNECTIONS = 7
    WIRELESS = 8
    MOBILE_NETWORK = 10

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
        MOBILE_NETWORK,
    ]
