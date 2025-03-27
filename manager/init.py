import asyncio

import uvloop
from loguru import logger
import utils.constants as const

logger.info('Starting UniTotem Manager {}', const.__version__)


logger.info('Creating event loop')
asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
loop = asyncio.get_event_loop()
asyncio.set_event_loop(loop)
logger.info('Event loop ready')

# noinspection PyUnresolvedReferences
import main
