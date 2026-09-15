"""The prediction cache key is stable across processes and dict order (#325).

`_cache_key` built the key from `hash(str(payload))`. Python salts str hashing
per process and `PYTHONHASHSEED` is set nowhere, so each gunicorn worker computed
a different key for the same request and every restart orphaned the cache: a
cached prediction was only ever found by the worker that wrote it. A stable
digest (sha256 over canonical JSON) fixes both, and does not depend on the
insertion order of the payload dict.
"""
import os
import subprocess
import sys

from app.api.predict import _cache_key
from app.redis_cache import CACHE_NAMESPACE


def _key_in_subprocess(hashseed: str) -> str:
    """Compute the key in a fresh interpreter with a given PYTHONHASHSEED."""
    env = dict(
        os.environ,
        PYTHONHASHSEED=hashseed,
        SORA_OFFLINE="1", RUN_SCHEDULER="false", REDIS_URL="",
        DATABASE_URL="sqlite:///./_cachekey_test.db",
        SECRET_KEY="ci", SORA_ADMIN_TOKEN="ci",
    )
    code = (
        "from app.api.predict import _cache_key;"
        "print(_cache_key('predict', {'budget': 1, 'co2_reduction': 2, 'social_impact': 3}))"
    )
    out = subprocess.run([sys.executable, "-c", code], env=env,
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_the_key_does_not_depend_on_the_process_hash_seed():
    a = _key_in_subprocess("0")
    b = _key_in_subprocess("12345")
    assert a == b, (
        "the cache key changed with PYTHONHASHSEED (%r vs %r); workers cannot "
        "share a cached result" % (a, b)
    )


def test_the_key_ignores_payload_dict_order():
    one = _cache_key("predict", {"budget": 1, "co2_reduction": 2, "social_impact": 3})
    two = _cache_key("predict", {"social_impact": 3, "budget": 1, "co2_reduction": 2})
    assert one == two, "the same payload in a different key order produced a different cache key"


def test_the_key_is_under_the_cache_namespace_and_varies_with_content():
    k = _cache_key("predict", {"budget": 1})
    assert k.startswith(f"{CACHE_NAMESPACE}predict:")
    assert _cache_key("predict", {"budget": 1}) != _cache_key("predict", {"budget": 2}), (
        "different payloads collided to one key"
    )
    assert _cache_key("neural", {"budget": 1}) != _cache_key("predict", {"budget": 1}), (
        "the prefix must be part of the key"
    )
