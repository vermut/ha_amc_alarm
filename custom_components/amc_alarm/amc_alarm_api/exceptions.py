class AmcException(Exception):
    pass


class ConnectionFailed(AmcException):
    pass


class AuthenticationFailed(AmcException):
    pass
