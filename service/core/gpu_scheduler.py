import datetime
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional

import redis.asyncio as aioredis


TASK_QUEUE = "gpu:queue:tasks"
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
        """Clear task queue. Called on startup before re-submitting from DB."""
        await self._redis.delete(TASK_QUEUE)

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

        if spec.priority == 0:
            await self._redis.lpush(TASK_QUEUE, json.dumps(task_data))
        else:
            await self._redis.rpush(TASK_QUEUE, json.dumps(task_data))

        return task_id

    async def get_queue_depths(self) -> dict[str, int]:
        total = await self._redis.llen(TASK_QUEUE)
        return {"quick": 0, "deep": 0, "total": total}

    async def get_worker_status(self) -> Optional[dict]:
        raw = await self._redis.get("gpu:worker:status")
        if raw:
            return json.loads(raw)
        return None

    async def remove_task(self, task_id: str, model_tier: str):
        """Remove a specific task from the queue (best-effort)."""
        items = await self._redis.lrange(TASK_QUEUE, 0, -1)
        for item in items:
            try:
                data = json.loads(item)
                if data.get("task_id") == task_id:
                    await self._redis.lrem(TASK_QUEUE, 1, item)
                    return
            except (json.JSONDecodeError, TypeError):
                continue

    async def publish_cancel(self, task_id: str):
        """Publish a cancel signal for a running task."""
        await self._redis.set(f"gpu:cancel:{task_id}", "1", ex=300)

    async def clear_queues(self):
        """Delete all tasks from the queue."""
        await self._redis.delete(TASK_QUEUE)

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
