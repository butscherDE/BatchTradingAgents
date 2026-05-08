"""Shared in-memory log ring buffer."""

import collections
import logging


class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 1000):
        super().__init__()
        self.buffer: collections.deque[str] = collections.deque(maxlen=capacity)

    def emit(self, record):
        self.buffer.append(self.format(record))

    def get_lines(self) -> list[str]:
        return list(self.buffer)


ring_handler = RingBufferHandler(capacity=1000)
ring_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s", datefmt="%H:%M:%S")
)
