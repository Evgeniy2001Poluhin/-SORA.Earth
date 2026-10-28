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

SENTRY_OPTIONS = {
    "traces_sample_rate": 0.1,
    "send_default_pii": False,
    "include_local_variables": False,
    "max_request_body_size": "never",
}


def sentry_init_kwargs():
    return {"dsn": os.getenv("SENTRY_DSN"),
            "environment": os.getenv("SORA_ENV", "development"),
            **SENTRY_OPTIONS}
