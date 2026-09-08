from fastapi.testclient import TestClient
from app.main import app
client = TestClient(app)
PAYLOAD = {"name":"Solar","budget":300000,"co2_reduction":500,"social_impact":8,
           "duration_months":24,"category":"Solar","region":"ES"}
def test_pdf_en():
    r = client.post("/api/v1/reports/compliance.pdf", json=PAYLOAD)
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")
def test_pdf_ru():
    r = client.post("/api/v1/reports/compliance.pdf?lang=ru", json=PAYLOAD)
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")
def test_validation():
    r = client.post("/api/v1/reports/compliance.pdf", json={**PAYLOAD, "social_impact": 99})
    assert r.status_code == 422

def test_batch_pdf():
    payload = [
        {"name":"Solar A","budget":100000,"co2_reduction":300,"social_impact":7,
         "duration_months":12,"category":"Solar","region":"ES"},
        {"name":"Wind B","budget":500000,"co2_reduction":800,"social_impact":9,
         "duration_months":36,"category":"Wind","region":"DE"},
    ]
    r = client.post("/api/v1/reports/compliance-batch.pdf", json=payload)
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF-")
    assert len(r.content) > 3000

def test_metrics_exposed():
    from app.api.reports import PDF_GENERATED
    if PDF_GENERATED is None:
        return
    before = PDF_GENERATED.labels(endpoint="compliance", lang="en")._value.get()
    client.post("/api/v1/reports/compliance.pdf", json=PAYLOAD)
    after = PDF_GENERATED.labels(endpoint="compliance", lang="en")._value.get()
    assert after == before + 1

def test_metrics_increment():
    from app.api.reports import PDF_GENERATED
    if PDF_GENERATED is None:
        return
    payload = {"name":"M","budget":100000,"co2_reduction":300,"social_impact":7,
               "duration_months":12,"category":"Solar","region":"ES"}
    before = PDF_GENERATED.labels(endpoint="compliance", lang="en")._value.get()
    r = client.post("/api/v1/reports/compliance.pdf?lang=en", json=payload)
    assert r.status_code == 200
    after = PDF_GENERATED.labels(endpoint="compliance", lang="en")._value.get()
    assert after == before + 1


def test_the_latency_histogram_gets_an_observation():
    """The panel needs a series, and a histogram with no observations has none.

    `sora_pdf_latency_seconds` was declared beside the counter and never
    observed, so `histogram_quantile(...sora_pdf_latency_seconds_bucket...)`
    in grafana/provisioning/dashboards/pdf_reports.json returned nothing at
    all — which reads exactly like "no reports are being generated" (#299).

    Asserted on `_count`, not on the value: how long a PDF takes is not a
    claim this test can make, and a latency assertion would be flaky by
    construction. That one observation happened is the whole property.
    """
    from prometheus_client import REGISTRY

    from app.api.reports import PDF_LATENCY

    if PDF_LATENCY is None:
        return

    def observations(endpoint):
        return REGISTRY.get_sample_value(
            "sora_pdf_latency_seconds_count", {"endpoint": endpoint}) or 0.0

    before = observations("compliance")
    r = client.post("/api/v1/reports/compliance.pdf", json=PAYLOAD)
    assert r.status_code == 200

    assert observations("compliance") - before == 1.0, (
        "no observation reached sora_pdf_latency_seconds; the Grafana panel "
        "drawing a P95 from it has no series to draw"
    )


def test_the_batch_endpoint_is_timed_under_its_own_label():
    """Both endpoints, because the panel groups by `endpoint`.

    A writer on one of them leaves the other's line permanently absent, and a
    missing line in a `by (endpoint)` panel is not visibly different from an
    endpoint nobody calls.
    """
    from prometheus_client import REGISTRY

    from app.api.reports import PDF_LATENCY

    if PDF_LATENCY is None:
        return

    def observations(endpoint):
        return REGISTRY.get_sample_value(
            "sora_pdf_latency_seconds_count", {"endpoint": endpoint}) or 0.0

    before = observations("compliance_batch")
    r = client.post("/api/v1/reports/compliance-batch.pdf", json=[PAYLOAD, PAYLOAD])
    assert r.status_code == 200

    assert observations("compliance_batch") - before == 1.0, (
        "the batch endpoint records a count but no duration"
    )
