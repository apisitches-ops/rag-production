import hashlib
import json
import os
from typing import TYPE_CHECKING

import redis

if TYPE_CHECKING:
    from app.query import Answer

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
TTL_SECONDS = 60 * 60

# Short timeouts so an unreachable Redis (e.g. a dropped connection, not just
# a refused one) fails fast into the RedisError fallback below instead of
# blocking the request on the OS-level TCP timeout.
_client = redis.Redis.from_url(REDIS_URL, socket_connect_timeout=1, socket_timeout=1)


def _key(query: str, acting_role: str | None) -> str:
    raw = f"{query}\x00{acting_role or ''}"
    return "answer:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_answer(query: str, acting_role: str | None) -> "Answer | None":
    try:
        raw = _client.get(_key(query, acting_role))
        if raw is None:
            return None
        return json.loads(raw)
    except (redis.RedisError, json.JSONDecodeError):
        return None


def set_cached_answer(query: str, acting_role: str | None, answer: "Answer") -> None:
    try:
        _client.set(_key(query, acting_role), json.dumps(answer), ex=TTL_SECONDS)
    except redis.RedisError:
        pass


def clear() -> None:
    try:
        _client.flushdb()
    except redis.RedisError:
        pass
