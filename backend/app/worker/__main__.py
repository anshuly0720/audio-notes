import asyncio
import logging

from .runner import worker_loop

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
asyncio.run(worker_loop(asyncio.Event()))