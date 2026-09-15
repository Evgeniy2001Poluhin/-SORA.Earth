"""How the dispatcher actually makes the request.

The destination policy is tested elsewhere. These are about the transport
carrying it out, because a correct policy reached through a careless request is
not a control -- the settings that matter here are ones a refactor removes
without any test noticing.
"""
import pytest


class Recorder:
    """Stands in for requests.Session and remembers how it was used."""
    instances = []

    def __init__(self):
        self.trust_env = True          # the requests default, so a test that
        self.verify = True             # never sets these can be told from one
        self.calls = []                # that does
        Recorder.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def post(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        class Response:
            status_code = 200
        return Response()


@pytest.fixture
def dispatched(monkeypatch):
    """Run one dispatch against a recording session and a stub database."""
    import app.services.alerts as alerts
    import app.services.outbound as outbound

    Recorder.instances = []
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "hooks.partner.example")
    monkeypatch.setattr(outbound, "_addresses", lambda h: [__import__("ipaddress").ip_address("93.184.216.34")])

    class Sub:
        id, url, secret = 1, "https://hooks.partner.example/hook", "s3cret"
    class Rec:
        subscription_id = event_type = status_code = error = ok = None
        def __init__(self, **kw): [setattr(self, k, v) for k, v in kw.items()]
    class DB:
        added = []
        def query(self, *a): return self
        def filter_by(self, **kw): return self
        def all(self): return [Sub()]
        def add(self, r): DB.added.append(r)
        def commit(self): pass
        def close(self): pass

    import sys, types
    fake_requests = types.ModuleType("requests")
    fake_requests.Session = Recorder
    monkeypatch.setitem(sys.modules, "requests", fake_requests)
    monkeypatch.setattr("app.database.SessionLocal", lambda: DB(), raising=False)
    monkeypatch.setattr("app.database.WebhookSubscription", Sub, raising=False)
    monkeypatch.setattr("app.database.WebhookDelivery", Rec, raising=False)
    monkeypatch.setattr(alerts, "_log_jsonl", lambda *a, **kw: None, raising=False)

    alerts.dispatch_webhooks("drift", {"x": 1})
    assert Recorder.instances, "no session was created"
    return Recorder.instances[0]


def test_the_session_does_not_trust_the_environment(dispatched):
    """Left on, HTTP_PROXY from the environment decides where the connection
    goes, which puts the destination back outside the check."""
    assert dispatched.trust_env is False


def test_redirects_are_not_followed(dispatched):
    """A permitted destination answering with a redirect to a forbidden one would
    otherwise reach it."""
    assert dispatched.calls
    assert dispatched.calls[0]["allow_redirects"] is False


def test_certificate_verification_is_not_disabled(dispatched):
    """`verify=False` would make HTTPS-only pointless.

    Set on the session rather than passed per call, because trust_env=False
    switched off REQUESTS_CA_BUNDLE with the proxy variables -- so the CA has to
    be named rather than exported. True is the system trust store.
    """
    assert getattr(dispatched, "verify", True) is not False
    assert dispatched.calls[0].get("verify") is not False


def test_a_timeout_is_set(dispatched):
    """Without one a hung destination holds the dispatcher indefinitely."""
    assert dispatched.calls[0].get("timeout")


@pytest.fixture
def dispatch_with(monkeypatch):
    """Dispatch to a stored subscription whose host and allowlist we choose, and
    report what was sent and what was recorded."""
    def _run(stored_url, allowlist):
        import sys, types, ipaddress
        import app.services.alerts as alerts
        import app.services.outbound as outbound

        Recorder.instances = []
        if allowlist is None:
            monkeypatch.delenv("SORA_WEBHOOK_ALLOWED_HOSTS", raising=False)
        else:
            monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", allowlist)
        monkeypatch.setattr(outbound, "_addresses",
                            lambda h: [ipaddress.ip_address("93.184.216.34")])

        class Sub:
            id, url, secret = 1, stored_url, "s3cret"
        class Rec:
            subscription_id = event_type = status_code = error = ok = None
            def __init__(self, **kw): [setattr(self, k, v) for k, v in kw.items()]
        recorded = []
        class DB:
            def query(self, *a): return self
            def filter_by(self, **kw): return self
            def all(self): return [Sub()]
            def add(self, r): recorded.append(r)
            def commit(self): pass
            def close(self): pass

        fake_requests = types.ModuleType("requests")
        fake_requests.Session = Recorder
        monkeypatch.setitem(sys.modules, "requests", fake_requests)
        monkeypatch.setattr("app.database.SessionLocal", lambda: DB(), raising=False)
        monkeypatch.setattr("app.database.WebhookSubscription", Sub, raising=False)
        monkeypatch.setattr("app.database.WebhookDelivery", Rec, raising=False)
        monkeypatch.setattr(alerts, "_log_jsonl", lambda *a, **kw: None, raising=False)

        alerts.dispatch_webhooks("drift", {"x": 1})
        sent = [c for s in Recorder.instances for c in s.calls]
        return sent, recorded
    return _run


def test_a_stored_subscription_outside_the_allowlist_is_not_called(dispatch_with):
    """Gating registration does not help against a row that already exists --
    written before the gate, or written when the allowlist was wider. The
    destination is decided at dispatch, not at storage."""
    sent, recorded = dispatch_with("https://evil.example/hook", "hooks.partner.example")
    assert sent == [], "an unpermitted stored destination was contacted"
    assert recorded and "allowlist" in (recorded[0].error or "")


def test_an_empty_allowlist_stops_dispatch_and_not_only_registration(dispatch_with):
    """Fail closed has to mean closed at both ends. Refusing new subscriptions
    while continuing to call the old ones would be a control that reads as on."""
    sent, recorded = dispatch_with("https://hooks.partner.example/hook", None)
    assert sent == [], "dispatch continued with no allowlist configured"
    assert recorded and "no destinations are permitted" in (recorded[0].error or "")


def test_a_permitted_stored_subscription_is_still_called(dispatch_with):
    """The row that keeps the two above from passing by breaking everything."""
    sent, _ = dispatch_with("https://hooks.partner.example/hook", "hooks.partner.example")
    assert len(sent) == 1


# ---------------------------------------------------------- startup validation

def test_importing_the_module_does_not_read_the_configuration(monkeypatch):
    """No import-time side effect.

    An earlier version validated at import, which made every importer -- alembic,
    the CLI scripts, the backup tooling, test collection -- fail on a webhook
    setting none of them use. Coupling unrelated tooling to this configuration is
    worse than the late failure it was trying to prevent, and a test asserting
    the old behaviour would have locked it in.
    """
    import importlib, sys
    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "*.this-is-invalid.example")
    sys.modules.pop("app.api.webhooks", None)
    assert importlib.import_module("app.api.webhooks") is not None


def test_startup_refuses_a_malformed_allowlist(monkeypatch):
    """The lifecycle event, not the import, is what stops a bad deployment.

    Built on a throwaway app registering the same handler: standing up the real
    one would drag in torch and the models, which is a different question from
    whether a startup handler refuses bad configuration.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.services.outbound import AllowlistMisconfigured, validate_configuration

    app = FastAPI()

    @app.on_event("startup")
    async def _check():
        validate_configuration()

    monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", "*.partner.example")
    with pytest.raises(AllowlistMisconfigured):
        with TestClient(app):
            pass


@pytest.mark.parametrize("value,label", [
    ("hooks.partner.example", "a valid list"),
    (None, "no list at all -- the safe default, not a fault"),
])
def test_startup_proceeds_when_the_configuration_is_usable(monkeypatch, value, label):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.services.outbound import validate_configuration

    app = FastAPI()

    @app.on_event("startup")
    async def _check():
        validate_configuration()

    @app.get("/ping")
    def ping():
        return {"ok": True}

    if value is None:
        monkeypatch.delenv("SORA_WEBHOOK_ALLOWED_HOSTS", raising=False)
    else:
        monkeypatch.setenv("SORA_WEBHOOK_ALLOWED_HOSTS", value)
    with TestClient(app) as c:
        assert c.get("/ping").status_code == 200, label


def test_the_application_actually_registers_that_check():
    """The throwaway app above proves the mechanism. This proves it is wired to
    the real one, which is the part a refactor silently drops."""
    import re
    from pathlib import Path
    main = Path(__file__).resolve().parents[1] / "app" / "main.py"
    src = main.read_text()
    startup = re.search(r'@app\.on_event\("startup"\)\n(async )?def \w+\(\):\n(.*?)(?=\n@|\nasync def |\ndef )',
                        src, re.S)
    assert startup, "no startup handler found in app/main.py"
    assert "validate_configuration" in startup.group(2), \
        "the startup handler does not validate the webhook allowlist"
