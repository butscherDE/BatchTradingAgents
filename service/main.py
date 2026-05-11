"""Service entry point — starts FastAPI + GPU worker subprocesses."""

import logging
import multiprocessing
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


def _run_ollama_worker(provider_name: str):
    from service.core.gpu_worker import main
    main(provider_name)


def _run_remote_worker(provider_name: str):
    from service.core.remote_worker import main
    main(provider_name)


def main():
    config = load_config()

    worker_processes = []
    for name, provider_conf in config.providers.items():
        if provider_conf.type == "ollama":
            target = _run_ollama_worker
        else:
            target = _run_remote_worker

        p = multiprocessing.Process(
            target=target,
            args=(name,),
            name=f"gpu-worker-{name}",
            daemon=True,
        )
        p.start()
        worker_processes.append(p)
        logger.info(f"Worker '{name}' started (PID {p.pid}, type={provider_conf.type})")

    import atexit

    def _shutdown():
        logger.info("Shutdown signal received. Stopping workers...")
        for p in worker_processes:
            p.terminate()
        for p in worker_processes:
            p.join(timeout=5)
        logger.info("Shutdown complete.")

    atexit.register(_shutdown)

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
