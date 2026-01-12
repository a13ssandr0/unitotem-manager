################# IMPORT LOGGER #################

import sys
from importlib.abc import Loader, MetaPathFinder
from time import time

from loguru import logger


class LoggingImporter(MetaPathFinder, Loader):
    _last_time = 0

    def find_spec(self, fullname, path, target=None):
        l = logger.opt(depth=4)
        l.trace("Last import took {:1.6f} seconds", time()-self._last_time)

        l.trace(f"Importing {fullname}")

        for finder in sys.meta_path[1:]:
            if hasattr(finder, 'find_spec'):
                spec = finder.find_spec(fullname, path, target)
                if spec is not None:
                    self._last_time = time()
                    return spec

        l.error(f"No module named {fullname}")
        self._last_time = time()
        return None


# sys.meta_path.insert(0, LoggingImporter())

################# IMPORT LOGGER #################


import asyncio
import sys

import uvloop

import utils.constants as const
from utils._logging import install_logger

logger.info('Starting UniTotem Manager {} (Python {})', const.__version__, sys.version)

# install_logger()

logger.info('Creating event loop')
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
logger.success('Event loop (id: {}) ready', id(loop))

# Initialization of environment variables

# noinspection PyUnresolvedReferences
from utils import environment

# Migration of old configuration files to newer versions
from utils.migration import run_advancement
run_advancement()

try:
    # noinspection PyUnresolvedReferences
    import main
except KeyboardInterrupt:
    pass

logger.info('Terminating')
import os
import signal

# afer main exited all important threads already stopped running
# some hypercorn threads still keep running preventing the program from exiting
# it's safe to kill them since no one of them is any more needed for UniTotem
os.kill(os.getpid(), signal.SIGKILL)
