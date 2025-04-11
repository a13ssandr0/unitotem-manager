import asyncio

import uvloop

import api.constants as const
from utils.logging import logger

# loguru is imported for the first time from
# utils.logging to initialize the log interceptor

logger.info('Starting UniTotem Manager {}', const.__version__)

logger.info('Creating event loop')
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
loop = asyncio.get_event_loop()
asyncio.set_event_loop(loop)
logger.success('Event loop (id: {}) ready', id(loop))

# Initialization of environment variables

# noinspection PyUnresolvedReferences
from utils import environment

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
