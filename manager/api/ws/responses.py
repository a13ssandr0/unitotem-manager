import sys

from loguru import logger


class WSResponse:
    _depth = 0 #classes subclassing WSResponse should increase this counter if calling super().__init__(target, **kwargs)
               # otherwise frame detection will fail, and it will be impossible to detect which function called the response
    target: str
    kwargs: dict

    def __init__(self, target = None, **kwargs):
        self.target = target
        self.kwargs = kwargs

        try:
            if callable(target):
                # noinspection PyUnresolvedReferences
                self.target = target.api_path
            elif target is None:
                # noinspection PyUnresolvedReferences
                frame = sys._getframe(1 + self._depth)
                try:
                    x = frame.f_globals[frame.f_code.co_name]
                except (AttributeError, KeyError):
                    # Sometimes (in UniTotem this "sometimes" may actually be "always") functions originating api responses
                    # are methods of a class; this means the function cannot be found in global variables of the frame, but
                    # we have to dig into the classes of the frame and eventually into its subclasses
                    root, *path = frame.f_code.co_qualname.split('.')
                    x = frame.f_globals[root]
                    for p in path:
                        x = getattr(x, p)
                self.target = x.api_path

            logger.trace(f'Response type {self.__class__.__name__} called from: {self.target}')

        except AttributeError:
            logger.error(f'Response type {self.__class__.__name__} has no API path attribute, assuming from request')


class WSMulticast(WSResponse):
    _depth = 1
    users: str|list[str]

    def __init__(self, users:str|list[str], target = None, **kwargs):
        super().__init__(target, **kwargs)
        self.users = users

class WSBroadcast(WSResponse):
    pass