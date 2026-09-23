"""A health ping may only claim what the process writing it can observe.

`app/services/status_service.py` → `_component_ok()` returned

```python
out = {"api": True}
```

as a literal, then checked models, database and external data. The `health_ping`
job in `app/scheduler.py` calls `record_health()` every five minutes, and that
job runs in the **scheduler** container -- a separate process that serves no
HTTP and never contacts the API. So every five minutes the scheduler wrote a row
saying the API was healthy, without looking.

Measured 2026-09-21 on the local `health_pings` table:

```
component       total   healthy   percent
api             2653    2653      100.00
database        2653    2653      100.00
external_data   2653    2653      100.00
models          2653      17        0.64
```

`models` is the control: the same pipeline recorded `false` 2636 times, so it
can. `api` is 100.00% across 2653 pings because it is a constant. The public
status page (`app/api/status_page.py`) derives `uptime_24h` and `uptime_7d` from
these rows, so API uptime could not drop below 100% whatever happened.

## What this fixes, and what it deliberately does not

The API process may still claim `api: True`: it is answering the call, which is
the evidence. The scheduler process no longer claims it, because it has none.

Not fixed here, and said so:

- `models` at 0.64% is **observed, not explained.** The obvious hypothesis --
  the scheduler has no models loaded -- is contradicted by its mounts:
  `models/` is mounted into the scheduler container. The local stack is also
  266 commits behind `origin/main`. A mechanism asserted from that would be a
  guess, and this file does not make it.
- `status_summary()` records a ping on every page view. The status page is
  served by the API, so it can only be viewed while the API is up: those pings
  sample uptime only when it is up, and bias the figure upward. That is a
  design question -- whether reading status should write it at all -- not a
  literal with one right answer.
"""
import pytest


@pytest.fixture()
def component_ok(monkeypatch):
    """`_component_ok` with the three real checks stubbed to "healthy".

    The real checks import `app.main`, which loads models and SHAP; that is
    minutes of work and nothing to do with the question here, which is only
    whether `api` is asserted.
    """
    import app.services.status_service as status_service

    healthy = lambda: {"status": "healthy"}  # noqa: E731
    monkeypatch.setattr(status_service, "_check_models", healthy)
    monkeypatch.setattr(status_service, "_check_db", healthy)
    monkeypatch.setattr(status_service, "_check_external_data", healthy)
    return status_service._component_ok


def test_the_scheduler_does_not_claim_the_api_is_up(component_ok, monkeypatch):
    """The process that runs `health_ping` never contacts the API."""
    monkeypatch.setenv("RUN_SCHEDULER", "true")
    out = component_ok()
    assert "api" not in out, (
        f"the scheduler process reported api={out.get('api')!r}. It serves no "
        "HTTP and never contacts the API, so it has no evidence either way; "
        "writing True every five minutes is what made API uptime a constant "
        "100% on the status page."
    )


def test_the_api_process_may_vouch_for_itself(component_ok, monkeypatch):
    """Answering the call is the evidence, so here the claim is earned."""
    monkeypatch.setenv("RUN_SCHEDULER", "false")
    out = component_ok()
    assert out.get("api") is True


def test_the_other_components_are_still_checked(component_ok, monkeypatch):
    """The fix removes one assertion; it must not remove the real checks."""
    for flag in ("true", "false"):
        monkeypatch.setenv("RUN_SCHEDULER", flag)
        out = component_ok()
        for comp in ("models", "database", "external_data"):
            assert out.get(comp) is True, (
                f"with RUN_SCHEDULER={flag}, {comp} is {out.get(comp)!r}; the "
                "stubbed check says healthy, so it was dropped rather than read"
            )


def test_a_failing_check_is_still_recorded_as_a_failure(monkeypatch):
    """Control: the pipeline must be able to say false, or the above is hollow."""
    import app.services.status_service as status_service

    monkeypatch.setattr(status_service, "_check_models", lambda: {"status": "degraded"})
    monkeypatch.setattr(status_service, "_check_db", lambda: {"status": "healthy"})
    monkeypatch.setattr(status_service, "_check_external_data", lambda: {"status": "healthy"})
    monkeypatch.setenv("RUN_SCHEDULER", "true")
    assert status_service._component_ok()["models"] is False
