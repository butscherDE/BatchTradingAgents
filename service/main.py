"""Service entry point — starts FastAPI + GPU worker subprocess."""

import logging
import multiprocessing
import signal
import sys
from pathlib import Path

import uvicorn

from service.config import load_config
from service.log_buffer import ring_handler


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(), ring_handler],
)
logger = logging.getLogger(__name__)


def _run_gpu_worker():
    from service.core.gpu_worker import main
    main()


def main():
    config = load_config()

    # Start GPU worker as subprocess
    worker_process = multiprocessing.Process(
        target=_run_gpu_worker,
        name="gpu-worker",
        daemon=True,
    )
    worker_process.start()
    logger.info(f"GPU worker started (PID {worker_process.pid})")

    def _shutdown(signum, frame):
        logger.info("Shutting down...")
        worker_process.terminate()
        worker_process.join(timeout=10)
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    # Start FastAPI
    uvicorn.run(
        "service.app:create_app",
        factory=True,
        host=config.host,
        port=config.port,
        log_level="info",
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )


if __name__ == "__main__":
    main()
