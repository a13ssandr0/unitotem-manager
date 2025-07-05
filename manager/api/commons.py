__all__ = [
    # "cmdargs",
    "SHUTDOWN_EVENT"
]

import asyncio

# from utils.constants import Arguments

# noinspection PyTypeChecker
# cmdargs: Arguments = None

SHUTDOWN_EVENT = asyncio.Event()
