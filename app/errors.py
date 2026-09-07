class AutomationError(Exception):
    """A recoverable or reportable automation failure."""


class UnsafeState(AutomationError):
    """Sending would risk duplication or crossing an unfinished turn."""


class ResponseTimeout(AutomationError):
    pass


class PageUnavailable(AutomationError):
    pass
