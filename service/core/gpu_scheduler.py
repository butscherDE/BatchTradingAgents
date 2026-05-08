import datetime
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional

import redis.asyncio as aioredis


QUICK_QUEUE = "gpu:queue:quick"
DEEP_QUEUE = "gpu:queue:deep"
RESULT_CHANNEL = "gpu:results"
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

    async def close(self):
        if self._redis:
            await self._redis.close()

    async def submit(self, spec: TaskSpec) -> str:
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

    async def publish_result(self, task_id: str, result: dict):
        await self._redis.publish(RESULT_CHANNEL, json.dumps({
            "task_id": task_id,
            **result,
        }))

    async def subscribe_results(self):
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(RESULT_CHANNEL)
        return pubsub

    async def subscribe_status(self):
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(STATUS_CHANNEL)
        return pubsub
