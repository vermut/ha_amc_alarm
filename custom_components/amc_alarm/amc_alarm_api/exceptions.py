class AmcException(Exception):
    pass


class ConnectionFailed(AmcException):
    pass


class LoginThrottled(ConnectionFailed):
    """The cloud rejected a login because the account is rate limited."""


class AuthenticationFailed(AmcException):
    pass


class AmcCentralNotFoundException(AmcException):
    pass

class AmcCentralStatusErrorException(AmcException):
    pass
