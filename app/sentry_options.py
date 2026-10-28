"""What the Sentry client may send, and what it may not.

A request body here is a project -- budget, CO2, social impact, duration, and
for a saved evaluation whose it is -- and the locals of a failing frame hold the
same values plus the probability computed from them. Neither belongs in a third
party's error store, least of all for a pilot participant whose project is
supposed to be visible to that participant only. So no body, no local
variables, no personal data; the stack trace, the route and the exception type
are what an error report needs.
"""
import os


def scrub_event(event, hint=None):
    """Keep the shape of a failure, drop every text that can carry a value.

    The options below stop the body and the locals, but values also travel in
    text: a pydantic ValidationError's message carries `input_value=...`, a
    log line formats its arguments in, and breadcrumbs are earlier log lines.
    What stays is the exception type, the stack and the route.
    """
    for exc in (event.get("exception") or {}).get("values") or []:
        if exc.get("value"):
            exc["value"] = "[redacted]"
    for key in ("logentry", "message", "extra"):
        event.pop(key, None)
    event["breadcrumbs"] = {"values": []}
    return event


SENTRY_OPTIONS = {
    "traces_sample_rate": 0.1,
    "send_default_pii": False,
    "include_local_variables": False,
    "max_request_body_size": "never",
    "before_send": scrub_event,
    "max_breadcrumbs": 0,
}


def sentry_init_kwargs():
    return {"dsn": os.getenv("SENTRY_DSN"),
            "environment": os.getenv("SORA_ENV", "development"),
            **SENTRY_OPTIONS}
