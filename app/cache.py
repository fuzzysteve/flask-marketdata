import json
import os

import redis

# Prefix for keys this app writes, so it never collides with the PHP site's
# serialize()d lookups. Market data keys written by the loaders are read as-is.
KEY_PREFIX = 'py:'

_client = None


def client():
    global _client
    if _client is None:
        _client = redis.Redis(host=os.environ.get('REDIS_HOST', 'localhost'),
                              port=int(os.environ.get('REDIS_PORT', 6379)),
                              decode_responses=True)
    return _client


def cached_lookup(key, build, ttl=3600):
    """Whole-table SDE lookups (they only change when the SDE is updated) are
    cached in Redis for an hour, so appraisal requests don't rebuild them."""
    full_key = KEY_PREFIX + key
    cached = client().get(full_key)
    if cached is not None:
        try:
            value = json.loads(cached)
            if isinstance(value, dict):
                return value
        except ValueError:
            pass
    value = build()
    client().setex(full_key, ttl, json.dumps(value))
    return value


def mget_chunked(keys, chunk=1000):
    values = []
    for i in range(0, len(keys), chunk):
        values.extend(client().mget(keys[i:i + chunk]))
    return values
