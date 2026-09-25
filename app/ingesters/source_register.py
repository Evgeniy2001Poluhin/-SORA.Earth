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


#: An instrument reading. The only kind that can settle a question about the
#: physical world at a place and time.
MEASURED = "measured"
#: A model's estimate. Present where instruments are absent, which is exactly
#: why it must never stand in for one silently.
MODELLED = "modelled"
#: Official statistics compiled offline and shipped in the source tree. Neither
#: measured here nor modelled here: someone else measured, and this holds a
#: dated copy.
ADMINISTRATIVE_SNAPSHOT = "administrative_snapshot"
#: Official statistics fetched from the publisher at run time. Someone else
#: measured, as with `administrative_snapshot`; the difference is that this is
#: the publisher's current release fetched over the network, not a dated copy
#: shipped in the source tree.
ADMINISTRATIVE_FETCHED = "administrative_fetched"
#: A constant chosen by an author and committed as a literal. No measurement
#: and no model stands behind the number.
STATIC_BASELINE = "static_baseline"

STATUS_ACTIVE = "active"
STATUS_DISABLED_NO_CURRENT_STATIONS = "disabled_no_current_stations"


@dataclass(frozen=True)
class SourceFacts:
    """One source, as it actually is rather than as configured."""

    name: str
    measurement_kind: str
    #: The named model behind a `modelled` value.
    #:
    #: None where the source names none, which is not the same as "we did not
    #: bother". `openmeteo` calls /v1/forecast without a `models=` parameter, so
    #: Open-Meteo selects per location and there is no single name to record --
    #: writing one would be a claim the source never made. A reader that needs
    #: the distinction gets None and can tell it from "CAMS reanalysis".
    model: Optional[str]
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
        model=None,
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
        model="CAMS reanalysis via Open-Meteo",
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
        model=None,
        status=STATUS_ACTIVE,
        coverage="21 declared regions, hourly",
        last_verified_data=None,
        requires_api_key=False,
        notes="Weather reanalysis via Open-Meteo. Ten variables.",
    ),
    "rosstat": SourceFacts(
        name="rosstat",
        measurement_kind=ADMINISTRATIVE_SNAPSHOT,
        model=None,
        status=STATUS_ACTIVE,
        coverage="85 declared regions, 5 metrics",
        last_verified_data="2024",
        requires_api_key=False,
        notes=(
            "Rosstat/Minfin/Mincifry official statistics for 2024, compiled "
            "offline into the source tree (data.rosstat_snapshot_2024); no "
            "network call. Someone measured these; this holds a dated copy, "
            "which is why it is not `derived` -- that word would put it in the "
            "same box as a literal nobody measured. Re-emitted daily stamped "
            "with the run time, the provenance defect in #121."
        ),
    ),
    "sber_veb_baseline": SourceFacts(
        name="sber_veb_baseline",
        measurement_kind=STATIC_BASELINE,
        model=None,
        status=STATUS_ACTIVE,
        coverage="85 declared regions, 1 metric",
        last_verified_data=None,
        requires_api_key=False,
        notes=(
            "A hardcoded dict of 85 constants, no network call, no version "
            "identifier and no measurement or model behind the numbers -- an "
            "author chose them. Distinct from rosstat, which is a dated copy "
            "of statistics someone did measure. Same provenance defect (#121)."
        ),
    ),
    "world_bank": SourceFacts(
        name="world_bank",
        measurement_kind=ADMINISTRATIVE_FETCHED,
        model=None,
        status=STATUS_ACTIVE,
        coverage="30 countries, 5 indicators, annual series",
        last_verified_data=None,
        requires_api_key=False,
        notes=(
            "World Bank API (api.worldbank.org): 30 countries from COUNTRY_ISO3, "
            "5 indicators from INDICATORS. The scheduled job auto_refresh_external_data "
            "refreshes country profiles through the fallback chain "
            "(get_country_esg_realtime / _fetch_with_fallback_impl); profiles are "
            "also fetched on demand and cached with a TTL. The history pass "
            "refresh_indicator_history(), the only writer of country_indicator_history "
            "rows labelled source='world_bank', runs only when SORA_HISTORY_REFRESH "
            "is on (off by default). Each World Bank value "
            "carries the period the source states for it (indicator_periods / "
            "as_of_date). Fallback chain: World Bank → OECD (for indicators in "
            "OECD_FLOWS) → static benchmark → global average (only when "
            "SORA_OFFLINE=1). Each value's indicator_sources says which source answered."
        ),
    ),
    "oecd": SourceFacts(
        name="oecd",
        measurement_kind=ADMINISTRATIVE_FETCHED,
        model=None,
        status=STATUS_ACTIVE,
        coverage="2 indicators with OECD mappings",
        last_verified_data=None,
        requires_api_key=False,
        notes=(
            "OECD SDMX API (sdmx.oecd.org): 2 indicators from OECD_FLOWS "
            "(gdp_per_capita and gini_index). Fetched only when World Bank "
            "returns nothing for a mapped indicator (via _fetch_with_fallback_impl, "
            "called by the same auto_refresh_external_data job). OECD values are "
            "not written to country_indicator_history; they appear only in the live "
            "profile's indicator_sources and carry no period (the fallback returns "
            "None for it)."
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


def openaq_scheduling_refusal() -> Optional[str]:
    """Why OpenAQ must not be scheduled, or None if it may be.

    Two distinct refusals, and collapsing them would hide the one that is a
    mistake:

    `disabled_no_current_stations`
        The default. A decision in force, logged at info.

    `enabled_without_api_key`
        The operator asked for this source and cannot have it. Registering
        anyway would schedule a job whose `fetch()` returns an empty list
        before it calls anything -- the same empty hourly run standing the
        source down exists to stop, one layer further in. Logged at warning,
        because a configuration that cannot do what it says is a fault rather
        than a policy.
    """
    if not openaq_enabled():
        return STATUS_DISABLED_NO_CURRENT_STATIONS
    if not (os.getenv("OPENAQ_API_KEY") or "").strip():
        return "enabled_without_api_key"
    return None
