import datetime
import json
import uuid
from dataclasses import dataclass, field
from typing import Optional

import redis.asyncio as aioredis

from service import clock
from service.config import ProviderConfig
from service.core.provider_router import ProviderRouter


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
    def __init__(self, redis_url: str, providers: dict[str, ProviderConfig]):
        self._redis_url = redis_url
        self._redis: Optional[aioredis.Redis] = None
        self._providers = providers
        self._router: Optional[ProviderRouter] = None

    async def connect(self):
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
        self._router = ProviderRouter(self._redis, self._providers)

    async def flush_queues(self):
        """Clear all provider queues. Called on startup before re-submitting from DB."""
        keys = [f"gpu:provider:{name}:queue" for name in self._providers]
        if keys:
            await self._redis.delete(*keys)

    async def close(self):
        if self._redis:
            await self._redis.close()

    async def submit(self, spec: TaskSpec, task_id: Optional[str] = None, provider: Optional[str] = None) -> str:
        if task_id is None:
            task_id = str(uuid.uuid4())
        task_data = {
            "task_id": task_id,
            "model_tier": spec.model_tier,
            "task_type": spec.task_type,
            "payload": spec.payload,
            "ticker": spec.ticker,
            "priority": spec.priority,
            "created_at": clock.now().isoformat(),
        }

        if provider and provider in self._providers:
            queue_key = f"gpu:provider:{provider}:queue"
            if spec.priority == 0:
                await self._redis.lpush(queue_key, json.dumps(task_data))
            else:
                await self._redis.rpush(queue_key, json.dumps(task_data))
            provider_name = provider
        else:
            provider_name = await self._router.route(task_data)

        task_data["routed_to"] = provider_name
        self._last_routed_to = provider_name
        return task_id

    @property
    def last_routed_to(self) -> str:
        return getattr(self, "_last_routed_to", "unknown")

    async def get_queue_depths(self) -> dict[str, int]:
        total = 0
        depths = {}
        for name in self._providers:
            depth = await self._redis.llen(f"gpu:provider:{name}:queue")
            depths[name] = depth
            total += depth
        depths["total"] = total
        return depths

    async def get_provider_queue_detail(self) -> list[dict]:
        """Per-provider breakdown including deep/quick counts and worker state."""
        detail = []
        for name, config in self._providers.items():
            queue_key = f"gpu:provider:{name}:queue"
            items = await self._redis.lrange(queue_key, 0, -1)
            deep_count = 0
            quick_count = 0
            for item in items:
                try:
                    data = json.loads(item)
                    if data.get("model_tier") == "deep":
                        deep_count += 1
                    else:
                        quick_count += 1
                except (json.JSONDecodeError, TypeError):
                    pass

            status_raw = await self._redis.get(f"gpu:provider:{name}:status")
            status = json.loads(status_raw) if status_raw else None
            active = int(await self._redis.get(f"gpu:provider:{name}:active") or 0)
            paused = bool(await self._redis.get(f"gpu:provider:{name}:paused"))

            state = status.get("state") if status else "offline"
            if paused and state in ("executing", "switching_model"):
                state = "pausing"
            elif paused:
                state = "paused"

            detail.append({
                "name": name,
                "depth": len(items),
                "quick_count": quick_count,
                "deep_count": deep_count,
                "state": state,
                "active_tasks": active,
                "max_queue": config.max_queue,
                "max_concurrent": config.max_concurrent,
                "current_model": status.get("current_model") if status else None,
            })
        return detail

    async def get_worker_status(self) -> Optional[dict]:
        """Legacy: return status from highest-priority provider."""
        for name in sorted(self._providers, key=lambda n: self._providers[n].priority):
            raw = await self._redis.get(f"gpu:provider:{name}:status")
            if raw:
                return json.loads(raw)
        return None

    async def remove_task(self, task_id: str, model_tier: str):
        """Remove a specific task from any provider queue (best-effort)."""
        for name in self._providers:
            queue_key = f"gpu:provider:{name}:queue"
            items = await self._redis.lrange(queue_key, 0, -1)
            for item in items:
                try:
                    data = json.loads(item)
                    if data.get("task_id") == task_id:
                        await self._redis.lrem(queue_key, 1, item)
                        return
                except (json.JSONDecodeError, TypeError):
                    continue

    async def publish_cancel(self, task_id: str):
        await self._redis.set(f"gpu:cancel:{task_id}", "1", ex=300)

    async def clear_queues(self):
        """Delete all tasks from all provider queues."""
        keys = [f"gpu:provider:{name}:queue" for name in self._providers]
        if keys:
            await self._redis.delete(*keys)

    async def pause_provider(self, name: str):
        await self._redis.set(f"gpu:provider:{name}:paused", "1")
        # Drain queued tasks to other available providers
        queue_key = f"gpu:provider:{name}:queue"
        moved = 0
        while True:
            raw = await self._redis.lpop(queue_key)
            if raw is None:
                break
            task_data = json.loads(raw)
            # Re-route via the router (will skip this paused provider)
            await self._router.route(task_data)
            moved += 1
        return moved

    async def resume_provider(self, name: str):
        await self._redis.delete(f"gpu:provider:{name}:paused")

    async def pause(self):
        for name in self._providers:
            await self.pause_provider(name)

    async def resume(self):
        for name in self._providers:
            await self.resume_provider(name)

    async def is_paused(self) -> bool:
        for name in self._providers:
            if not await self._redis.get(f"gpu:provider:{name}:paused"):
                return False
        return True

    async def pop_result(self, timeout: float = 1.0) -> dict | None:
        result = await self._redis.blpop(RESULT_QUEUE, timeout=timeout)
        if result:
            return json.loads(result[1])
        return None

    async def subscribe_status(self):
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(STATUS_CHANNEL)
        return pubsub

    async def migrate_legacy_queue(self):
        """Move tasks from old single queue to first provider's queue."""
        old_key = "gpu:queue:tasks"
        exists = await self._redis.exists(old_key)
        if not exists:
            return
        first_provider = sorted(self._providers, key=lambda n: self._providers[n].priority)[0]
        target_key = f"gpu:provider:{first_provider}:queue"
        while True:
            item = await self._redis.lpop(old_key)
            if item is None:
                break
            await self._redis.rpush(target_key, item)
