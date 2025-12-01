import inspect
import logging
from typing import Any

import hypercorn.logging
import loguru
from hypercorn.config import Config
from hypercorn.typing import ResponseSummary, WWWScope
from loguru import logger



# Intercepts standard Python logging and passes it to Loguru
class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        # Find caller from where originated the logged message.
        frame, depth = inspect.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == __file__ or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        # if frame.f_code.co_filename == logging.__file__:

        # Get corresponding Loguru level if it exists.
        level: str | int
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())




def get_patcher(logger_name: str):
    def patcher(record: 'loguru.Record'):
        # noinspection PyTypedDict
        record.update(name=logger_name, function='', line='')

    return patcher


# Customized implementation of Loguru logger used for Hypercorn
class Logger(hypercorn.logging.Logger):
    def __init__(self, config: "Config") -> None:
        self.access_log_format = '{h} {l} "{r}" {s} {b} "{f}" "{a}"'

        self.access_logger = logger.patch(get_patcher("hypercorn.access"))
        self.error_logger = logger.patch(get_patcher("hypercorn.error"))

    async def access(self, request: "WWWScope", response: "ResponseSummary", request_time: float) -> None:
        if self.access_logger is not None:
            self.access_logger.info(
                    self.access_log_format, **self.atoms(request, response, request_time)
            )

    async def info(self, message: str, *args: Any, **kwargs: Any) -> None:
        if self.error_logger is not None:
            if message.startswith('Running on'):
                self.error_logger.patch(lambda r: r.update(name='')).success(message, *args, **kwargs)
            else:
                self.error_logger.info(message, *args, **kwargs)


def install_logger():
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.basicConfig(handlers=[InterceptHandler()], level=0)
