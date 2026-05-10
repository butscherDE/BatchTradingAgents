import datetime
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional

import redis.asyncio as aioredis


QUICK_QUEUE = "gpu:queue:quick"
DEEP_QUEUE = "gpu:queue:deep"
RESULT_QUEUE = "gpu:results:queue"
STATUS_CHANNEL = "gpu:status"


@dataclass
class TaskSpec:
    model_tier: str  # "quick" or "deep"
    task_type: str
    payload: dict = field(default_factory=dict)
    ticker: Optional[str] = None
    priority: int = 1  # 0=emergency, 1=normal


class GpuScheduler:
    def __init__(self, redis_url: str):
        self._redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self):
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)

    async def flush_queues(self):
        """Clear task queues. Called on startup before re-submitting from DB."""
        await self._redis.delete(QUICK_QUEUE, DEEP_QUEUE)

    async def close(self):
        if self._redis:
            await self._redis.close()

    async def submit(self, spec: TaskSpec, task_id: Optional[str] = None) -> str:
        if task_id is None:
            task_id = str(uuid.uuid4())
        task_data = {
            "task_id": task_id,
            "model_tier": spec.model_tier,
            "task_type": spec.task_type,
            "payload": spec.payload,
            "ticker": spec.ticker,
            "priority": spec.priority,
            "created_at": datetime.datetime.utcnow().isoformat(),
        }

        queue = QUICK_QUEUE if spec.model_tier == "quick" else DEEP_QUEUE

        if spec.priority == 0:
            await self._redis.lpush(queue, json.dumps(task_data))
        else:
            await self._redis.rpush(queue, json.dumps(task_data))

        return task_id

    async def get_queue_depths(self) -> dict[str, int]:
        quick = await self._redis.llen(QUICK_QUEUE)
        deep = await self._redis.llen(DEEP_QUEUE)
        return {"quick": quick, "deep": deep}

    async def get_worker_status(self) -> Optional[dict]:
        raw = await self._redis.get("gpu:worker:status")
        if raw:
            return json.loads(raw)
        return None

    async def remove_task(self, task_id: str, model_tier: str):
        """Remove a specific task from its Redis queue (best-effort)."""
        queue = QUICK_QUEUE if model_tier == "quick" else DEEP_QUEUE
        items = await self._redis.lrange(queue, 0, -1)
        for item in items:
            try:
                data = json.loads(item)
                if data.get("task_id") == task_id:
                    await self._redis.lrem(queue, 1, item)
                    return
            except (json.JSONDecodeError, TypeError):
                continue

    async def publish_cancel(self, task_id: str):
        """Publish a cancel signal for a running task."""
        await self._redis.set(f"gpu:cancel:{task_id}", "1", ex=300)

    async def clear_queues(self):
        """Delete all tasks from both queues."""
        await self._redis.delete(QUICK_QUEUE)
        await self._redis.delete(DEEP_QUEUE)

    async def pause(self):
        """Pause the GPU worker (finishes current task, then waits)."""
        await self._redis.set("gpu:paused", "1")

    async def resume(self):
        """Resume the GPU worker."""
        await self._redis.delete("gpu:paused")

    async def is_paused(self) -> bool:
        return bool(await self._redis.get("gpu:paused"))

    async def pop_result(self, timeout: float = 1.0) -> dict | None:
        """Pop a result from the results queue. Returns None if empty after timeout."""
        result = await self._redis.blpop(RESULT_QUEUE, timeout=timeout)
        if result:
            return json.loads(result[1])
        return None

    async def subscribe_status(self):
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(STATUS_CHANNEL)
        return pubsub
