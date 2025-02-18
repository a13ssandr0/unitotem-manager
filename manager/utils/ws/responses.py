import traceback
import logging
import sys

logger = logging.getLogger(__name__)


class WSResponse:
    target: str
    kwargs: dict

    def __init__(self, target, **kwargs):
        # noinspection PyUnresolvedReferences
        self.target = target.api_path if callable(target) else target
        self.kwargs = kwargs
        # frame = sys._getframe(1)
        # try:
        #     x = frame.f_globals[frame.f_code.co_name]
        # except (AttributeError, KeyError):
        #     x = frame.f_globals[frame.f_code.co_qualname.split('.')[0]].__getattribute__(frame.f_code.co_name)
        logger.debug(f'Response: {self.target} Called from: {traceback.extract_stack(limit=2)[-2][2]}')


class WSBroadcast(WSResponse):
    pass