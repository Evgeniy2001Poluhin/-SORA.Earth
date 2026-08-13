"""No source declares `max_vintage_hours`, and that is the state to defend.

#74 introduced the field. The temptation it exists to resist is filling it in
from a number that is already lying around, and there are two such numbers:

    default_ttl_hours       when to poll. For rosstat, a 180-day poll of an
                            annual statistic -- reusing it made every clean run
                            escalate over a 590-day vintage, measured on
                            production.

    reenable_condition      openaq's "data newer than
                            SORA_OPENAQ_MIN_FRESHNESS_DAYS (default 30),
                            confirmed by a read against the live API" (#57). A
                            precondition for putting the source back, scoped to
                            one station, checked against the live API, and
                            configurable -- four ways it is not a running
                            staleness tolerance for stored rows.

Both readings are category errors of the same shape. Until someone agrees an
actual tolerance per source, every source is `not_configured`: freshness is
measured and recorded, and takes no part in the verdict.

This file fails when that changes, which is the point -- adding one has to be a
decision with a number somebody chose, not a value inherited from a field that
meant something else.
"""
import pytest

from app.ingesters.base import BaseIngester
from app.ingesters.openaq import OpenAQIngester
from app.ingesters.openmeteo import OpenMeteoIngester
from app.ingesters.openmeteo_air_quality import OpenMeteoAirQualityIngester
from app.ingesters.rosstat import RosstatIngester
from app.ingesters.sber_veb_baseline import SberVebBaselineIngester

INGESTERS = [
    OpenAQIngester,
    OpenMeteoIngester,
    OpenMeteoAirQualityIngester,
    RosstatIngester,
    SberVebBaselineIngester,
]


def test_the_default_is_absent_not_a_number():
    assert BaseIngester.max_vintage_hours is None


@pytest.mark.parametrize("cls", INGESTERS, ids=lambda c: c.__name__)
def test_no_source_declares_one(cls):
    declared = getattr(cls, "max_vintage_hours", None)

    assert declared is None, (
        f"{cls.__name__} declares max_vintage_hours={declared}. If that is a "
        f"tolerance somebody agreed, say where it comes from and update this "
        f"test. If it was taken from default_ttl_hours or from a reenable "
        f"condition, it is not a vintage tolerance -- see #74."
    )


@pytest.mark.parametrize("cls", INGESTERS, ids=lambda c: c.__name__)
def test_the_polling_interval_is_untouched(cls):
    """The other half of the separation: removing the conflation must not have
    removed the polling contract, which is real and used for caching."""
    assert getattr(cls, "default_ttl_hours", None), (
        f"{cls.__name__} lost its polling interval"
    )


def test_the_reenable_condition_stays_where_it_belongs():
    """openaq's 30 days is a precondition, and it is still recorded as one.

    Pinned because the tempting move is to promote it into the ingester as a
    tolerance -- which was done, and reverted, in the course of this change.
    """
    from app.ingesters.source_register import SOURCE_REGISTER

    openaq = SOURCE_REGISTER["openaq"]

    assert "SORA_OPENAQ_MIN_FRESHNESS_DAYS" in openaq.reenable_condition
    assert "live API" in openaq.reenable_condition
    assert getattr(OpenAQIngester, "max_vintage_hours", None) is None, (
        "the reenable condition has been promoted into a running tolerance"
    )
