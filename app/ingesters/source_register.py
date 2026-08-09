"""What each ingestion source is, stated once.

`app/ingesters/openmeteo_air_quality.py` already refers to "the source
register" and there was not one; the facts lived in docstrings, in an audit
markdown, and in whatever an operator remembered. This is that register.

The distinction it exists to keep visible is **measured against modelled**.
Open-Meteo Air Quality returns current values for coordinates where OpenAQ's
stations have been silent since 2017 -- but those values come from the CAMS
reanalysis. They are a model's estimate of an air quality, not an instrument's
reading of one. Treating the second as a replacement for the first is a worse
failure than having neither, because the substitution is invisible in the
number.

So the register never says a modelled source "replaces" a measured one. It
records what each is, what it currently covers, and -- for a source that has
been stood down -- what would have to be true to bring it back.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Dict, Optional


MEASURED = "measured"
MODELLED = "modelled"
DERIVED = "derived"

STATUS_ACTIVE = "active"
STATUS_DISABLED_NO_CURRENT_STATIONS = "disabled_no_current_stations"


@dataclass(frozen=True)
class SourceFacts:
    """One source, as it actually is rather than as configured."""

    name: str
    measurement_kind: str
    status: str
    coverage: str
    #: The most recent data this source is known to have published, from a
    #: measurement rather than from an assumption. None where it is current.
    last_verified_data: Optional[str]
    requires_api_key: bool
    notes: str
    #: What must hold before a stood-down source is scheduled again. Stated in
    #: advance so switching it on is a check rather than a judgement call.
    reenable_condition: Optional[str] = None


SOURCE_REGISTER: Dict[str, SourceFacts] = {
    "openaq": SourceFacts(
        name="openaq",
        measurement_kind=MEASURED,
        status=STATUS_DISABLED_NO_CURRENT_STATIONS,
        coverage="none for the declared regions",
        last_verified_data="2017-09-11",
        requires_api_key=False,  # the key is no longer needed to operate
        notes=(
            "Station measurements, and the only measured air-quality source "
            "the platform has. Every station within 25km of the 21 declared "
            "regions stopped reporting in September 2017 (#57), measured with "
            "a working key and HTTP 200 throughout. Stood down from scheduled "
            "ingestion rather than removed: the adapter, the v3 parser and its "
            "tests (#117, #119) are kept, and no historical rows are deleted."
        ),
        reenable_condition=(
            "at least one station in a declared region publishing data newer "
            "than SORA_OPENAQ_MIN_FRESHNESS_DAYS (default 30), confirmed by a "
            "read against the live API"
        ),
    ),
    "openmeteo_air_quality": SourceFacts(
        name="openmeteo_air_quality",
        measurement_kind=MODELLED,
        status=STATUS_ACTIVE,
        coverage="21 declared regions, hourly",
        last_verified_data=None,
        requires_api_key=False,
        notes=(
            "CAMS reanalysis via Open-Meteo. Current where the stations are "
            "dead, and that is exactly why it must not be recorded as a "
            "replacement for them: it is a model's estimate, carried on every "
            "row as measurement_kind=modelled. It covers the same coordinates "
            "as openaq; it does not cover the same question."
        ),
    ),
    "openmeteo": SourceFacts(
        name="openmeteo",
        measurement_kind=MODELLED,
        status=STATUS_ACTIVE,
        coverage="21 declared regions, hourly",
        last_verified_data=None,
        requires_api_key=False,
        notes="Weather reanalysis via Open-Meteo. Ten variables.",
    ),
    "rosstat": SourceFacts(
        name="rosstat",
        measurement_kind=DERIVED,
        status=STATUS_ACTIVE,
        coverage="85 declared regions, 5 metrics",
        last_verified_data="2024",
        requires_api_key=False,
        notes=(
            "An offline snapshot compiled into the source tree "
            "(data.rosstat_snapshot_2024); no network call. Its values are "
            "constant per region, and it is re-emitted daily stamped with the "
            "run time, which is the provenance defect in #121."
        ),
    ),
    "sber_veb_baseline": SourceFacts(
        name="sber_veb_baseline",
        measurement_kind=DERIVED,
        status=STATUS_ACTIVE,
        coverage="85 declared regions, 1 metric",
        last_verified_data=None,
        requires_api_key=False,
        notes=(
            "A hardcoded dict of 85 constants with no network call and no "
            "version identifier. Same provenance defect as rosstat (#121)."
        ),
    ),
}


_OPENAQ_ENABLED_ENV = "SORA_OPENAQ_ENABLED"


def openaq_enabled() -> bool:
    """Whether OpenAQ is scheduled. Off unless explicitly turned on.

    Default-off rather than default-on, following `history_refresh_enabled()`:
    a source with nothing current to give should not become an hourly job as a
    side effect of a deployment. Twenty-one requests an hour and a `degraded`
    line four times a day is not a signal -- a warning that is normal every
    hour is one nobody reads.

    Turning it on is a decision that also requires the condition in
    SOURCE_REGISTER["openaq"].reenable_condition to be true; this flag alone
    does not make the stations report.
    """
    return os.getenv(_OPENAQ_ENABLED_ENV, "off").strip().lower() in {
        "1", "true", "yes", "on", "enabled",
    }
