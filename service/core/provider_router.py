"""Routes tasks to providers based on priority, queue capacity, and idle state."""

import json

import redis.asyncio as aioredis

from service.config import ProviderConfig


class ProviderRouter:
    def __init__(self, redis: aioredis.Redis, providers: dict[str, ProviderConfig]):
        self._redis = redis
        self._providers = sorted(providers.items(), key=lambda x: x[1].priority)

    async def route(self, task_data: dict) -> str:
        priority = task_data.get("priority", 1)

        for name, config in self._providers:
            if await self._is_paused(name):
                continue

            queue_key = f"gpu:provider:{name}:queue"
            current_depth = await self._redis.llen(queue_key)

            if config.max_queue == 0:
                active = int(await self._redis.get(f"gpu:provider:{name}:active") or 0)
                if active >= config.max_concurrent:
                    continue
            elif config.max_queue > 0:
                if current_depth >= config.max_queue:
                    continue

            if priority == 0:
                await self._redis.lpush(queue_key, json.dumps(task_data))
            else:
                await self._redis.rpush(queue_key, json.dumps(task_data))
            return name

        # Fallback: force into lowest-priority provider
        fallback_name = self._providers[-1][0]
        queue_key = f"gpu:provider:{fallback_name}:queue"
        if priority == 0:
            await self._redis.lpush(queue_key, json.dumps(task_data))
        else:
            await self._redis.rpush(queue_key, json.dumps(task_data))
        return fallback_name

    async def _is_paused(self, name: str) -> bool:
        return bool(await self._redis.get(f"gpu:provider:{name}:paused"))
