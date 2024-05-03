from enum import StrEnum
from typing import Union, Optional, List, Dict, Literal, TypeAlias

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
    centrals: Optional[List[AmcCentral]] = None
    data: Optional[AmcLogin] = None
    token: Optional[str] = None


class AmcCommandResponse(BaseModel):
    command: str
    status: str
    centrals: Optional[dict[str, AmcCentralResponse]] = None
    user: Optional[AmcUser] = None
    token: Optional[str] = None


AmcStatesType: TypeAlias = Dict[
    str,
    Dict[
        Literal["ZONES", "AREAS", "GROUPS", "NOTIFICATIONS"],
        List[AmcEntry | AmcNotification],
    ],
]
