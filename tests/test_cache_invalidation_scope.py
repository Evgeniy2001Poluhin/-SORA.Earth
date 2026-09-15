"""Cache invalidation deletes prediction cache and nothing else (Security C).

Two Redis namespaces were conflated. `sora:*` on this system holds distributed
locks (`sora:lock:*`) and the scheduler's status (`sora:scheduler:status`) — and
no prediction entry, which live under their own prefixes. So deleting `sora:*`
removed coordination state and no cache at all. Prediction cache moves under
`sora:cache:*`, invalidation targets only that, and nothing walks the whole
`sora:*` namespace with a delete behind it.
"""
import ast
import os

import pytest

APP_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app")


def test_prediction_cache_keys_are_namespaced_under_sora_cache():
    from app.api.predict import _cache_key

    for prefix in ("predict", "neural", "stacking:rf+xgb"):
        key = _cache_key(prefix, {"budget": 1, "co2_reduction": 2})
        assert key.startswith("sora:cache:"), (
            "%r is not under the invalidatable namespace" % key
        )


def _broad_sora_deletes():
    """Every place that resolves the whole `sora:*` namespace to delete it.

    A KEYS/scan over `sora:*` whose result reaches a delete in the same function.
    Reported as (file, line) so a survivor names itself.
    """
    hits = []
    for root, _dirs, files in os.walk(APP_DIR):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as handle:
                source = handle.read()
            tree = ast.parse(source, filename=path)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                if not (isinstance(func, ast.Attribute) and func.attr in ("keys", "scan_iter")):
                    continue
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and arg.value == "sora:*":
                        hits.append((os.path.relpath(path, os.path.dirname(APP_DIR)), node.lineno))
    return hits


def test_nothing_resolves_the_whole_sora_namespace_for_deletion():
    hits = _broad_sora_deletes()
    assert not hits, (
        "these resolve the whole sora:* namespace, which includes locks and "
        "scheduler state, not only cache: %s" % hits
    )


def test_invalidating_the_cache_spares_locks_and_coordination_state():
    """Plant one of each class, invalidate, and see what survives."""
    from app.redis_cache import redis_client, invalidate_prediction_cache

    planted = {
        "sora:cache:predict:abc": "cached-prediction",
        "sora:cache:neural:def": "cached-prediction",
        "sora:lock:model_retrain": "held",
        "sora:lock:closed_loop": "held",
        "sora:scheduler:status": "{}",
        "auth:refresh:revoked:xyz": "1",
        "drift:baseline": "{}",
    }
    for key, value in planted.items():
        redis_client.set(key, value)

    removed = invalidate_prediction_cache()

    survivors = {k for k in planted if redis_client.get(k) is not None}
    assert survivors == {
        "sora:lock:model_retrain", "sora:lock:closed_loop",
        "sora:scheduler:status", "auth:refresh:revoked:xyz", "drift:baseline",
    }, "invalidation removed coordination state; survivors were %s" % sorted(survivors)
    assert removed == 2, "expected two cache keys removed, got %s" % removed


def test_invalidating_a_prefix_stays_within_the_cache_namespace():
    from app.redis_cache import redis_client, invalidate_prediction_cache

    for key in ("sora:cache:predict:a", "sora:cache:neural:b", "sora:lock:x"):
        redis_client.set(key, "v")

    invalidate_prediction_cache("predict")

    assert redis_client.get("sora:cache:predict:a") is None
    assert redis_client.get("sora:cache:neural:b") is not None, "a sibling cache prefix was cleared"
    assert redis_client.get("sora:lock:x") is not None, "a lock was cleared by a prefix invalidation"
