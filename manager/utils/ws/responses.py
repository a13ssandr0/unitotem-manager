import traceback
import logging

logger = logging.getLogger(__name__)


class WSResponse:
    target: str
    kwargs: dict

    def __init__(self, target, **kwargs):
        # noinspection PyUnresolvedReferences
        self.target = target.api_path if callable(target) else target
        self.kwargs = kwargs
        logger.debug(f'Response: {self.target} Called from: {traceback.extract_stack(limit=2)[-2][2]}')


class WSBroadcast(WSResponse):

    def __init__(self, target, **kwargs):
        super().__init__(target, **kwargs)
