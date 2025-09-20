class AmcException(Exception):
    pass


class ConnectionFailed(AmcException):
    pass


class AuthenticationFailed(AmcException):
    pass


class AmcCentralNotFoundException(AmcException):
    pass

class AmcCentralStatusErrorException(AmcException):
    pass
