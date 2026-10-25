"""Census v2 leaf: one source status for an all-null answer, and the 110 dates still owed.

Pure functions, no disk and no network. The census v1 collector stopped C at its first
reservation, 2025-08-07: HTTP 200, a complete 21-point by 192-hour body in which every
temperature was JSON null. The original weather validator refused it, correctly, and
nothing here changes that validator. This module only names the outcome and derives the
calendar a successor namespace D may still request. It declares no acceptance.
"""
from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit
import math

from app.services.environmental.regions import OPENMETEO_POINTS_V1

from .replay_cache_plan import _request
from .replay_cache_worker import MAX_RAW, CacheRefusal, digest, need, stamp
from .replay_p0 import ReplayRefusal, canonical, parse_json
from .replay_source_census import (BASELINE, COMBINED_LIMIT, COMMIT, DAY, EVENT_FIELDS, EXCLUDED, SHA,
                                   STATE_FIELDS, UNITS, _load, _request_sha, source_calendar)

ALL_NULL = 'observed_all_null_response'
POINT_COUNT = 21
HOURS_PER_POINT = 192
TOLERANCE = 0.3  # The original parser's grid tolerance, degrees.
UTC_NAMES = ('GMT', 'UTC')
UNITS_DECLARED = {'time': 'iso8601', 'temperature_2m': '°C'}
HOURLY_KEYS = frozenset({'time', 'temperature_2m'})
REQUIRED_KEYS = frozenset({'latitude', 'longitude', 'timezone', 'timezone_abbreviation',
                           'utc_offset_seconds', 'hourly_units', 'hourly'})
OPTIONAL_KEYS = frozenset({'elevation', 'generationtime_ms'})
# Multi-location answers number every point after the first: absent on point 0, the
# integer index on points 1..20. Read off the retained 2025-08-07 body.
LOCATION_ID = 'location_id'

STOPPED_DAY = '2025-08-07'
STOPPED_REASON = 'response_not_accepted:ReplayRefusal'
STOPPED_RESPONSE_SHA256 = 'de80dd0ca7f5eb652a43fe3b2ebc2d3724e4cf65ab785a2d62280dc0ded3f291'
STOPPED_RESPONSE_BYTES = 102740
# The successor policy must pass exactly this pin; the parameter exists for synthetic tests.
APPROVED_STOPPED_STATE_SHA256 = 'cc33f004e2f5b34d71e13b3bb033ff78ff3ec99770ab9579424fb2ad6e779819'
STOPPED_INFLIGHT_FIELDS = frozenset({'day', 'request_sha256', 'response_sha256', 'response_bytes'})

INHERITED = {'attempts': BASELINE['attempts'] + 1, 'units': BASELINE['units'] + UNITS}
SUCCESSOR_LIMIT = {'attempts': 110, 'units': 110 * UNITS}
assert INHERITED == {'attempts': 487, 'units': 12699}
assert {k: INHERITED[k] + SUCCESSOR_LIMIT[k] for k in INHERITED} == COMBINED_LIMIT


def _number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # a JSON integer beyond float range, e.g. 10**400
        return False


def _expected_hours(day):
    first = date.fromisoformat(day)
    return ['%sT%02d:00' % ((first + timedelta(days=offset)).isoformat(), hour)
            for offset in range(8) for hour in range(24)]


def _coordinates(request):
    """Requested coordinates, read from the pinned URL and checked against the point registry."""
    query = parse_qs(urlsplit(request['url']).query, strict_parsing=True)
    need(set(query) == {'latitude', 'longitude', 'hourly', 'temperature_unit', 'timezone',
                        'timeformat', 'cell_selection', 'models', 'run', 'forecast_days'} and
         all(len(v) == 1 for v in query.values()), 'pinned request query differs')
    need(query['hourly'] == ['temperature_2m'] and query['models'] == ['ecmwf_ifs'] and
         query['timezone'] == ['UTC'] and query['timeformat'] == ['iso8601'] and
         query['forecast_days'] == ['8'], 'pinned request variables differ')
    lats = [float(v) for v in query['latitude'][0].split(',')]
    lons = [float(v) for v in query['longitude'][0].split(',')]
    need(len(lats) == len(lons) == POINT_COUNT == len(OPENMETEO_POINTS_V1) and
         lats == [p.lat for p in OPENMETEO_POINTS_V1] and lons == [p.lon for p in OPENMETEO_POINTS_V1],
         'pinned request coordinates differ')
    return list(zip(lats, lons))


def _point_is_all_null(response, index, lat, lon, hours):
    if type(response) is not dict:
        return False
    keys = set(response)
    required = REQUIRED_KEYS | {LOCATION_ID} if index else REQUIRED_KEYS
    if not required <= keys or not keys <= required | OPTIONAL_KEYS:
        return False
    if index and (type(response[LOCATION_ID]) is not int or response[LOCATION_ID] != index):
        return False
    for value, requested in ((response['latitude'], lat), (response['longitude'], lon)):
        if not _number(value) or abs(value - requested) > TOLERANCE:
            return False
    if response['timezone'] not in UTC_NAMES or response['timezone_abbreviation'] not in UTC_NAMES:
        return False
    if type(response['utc_offset_seconds']) is not int or response['utc_offset_seconds'] != 0:
        return False
    for name in OPTIONAL_KEYS & keys:
        if not _number(response[name]):
            return False
    units = response['hourly_units']
    if type(units) is not dict or units != UNITS_DECLARED:
        return False
    hourly = response['hourly']
    if type(hourly) is not dict or set(hourly) != HOURLY_KEYS:
        return False
    times, values = hourly['time'], hourly['temperature_2m']
    if type(times) is not list or type(values) is not list:
        return False
    if len(times) != HOURS_PER_POINT or len(values) != HOURS_PER_POINT or times != hours:
        return False
    return all(value is None for value in values)


def classify_all_null(raw: bytes, *, day: str, request: dict) -> dict | None:
    """Source status for a complete all-null answer to the pinned request, or None.

    The caller has already established HTTP 200 and that `request` is the reservation's.
    A request that is not exactly the reviewed one for `day` is refused (CacheRefusal),
    not classified. Any body that is not the full 21 x 192 null grid returns None:
    an empty list, a missing hour or point, one finite or bool value, unknown metadata.
    The original weather validator is not consulted and not changed.
    """
    need(isinstance(day, str) and DAY.match(day) is not None, 'issue date required')
    try:
        issue = date.fromisoformat(day)
    except ValueError as exc:
        raise CacheRefusal('issue date required') from exc
    expected = _request(issue)
    need(type(request) is dict and request == expected and canonical(request) == canonical(expected),
         'request differs from the reviewed request for this date')
    coordinates = _coordinates(request)
    if type(raw) is not bytes or len(raw) > MAX_RAW:
        return None
    try:
        payload = parse_json(raw)
    except ReplayRefusal:
        return None
    if type(payload) is not list or len(payload) != POINT_COUNT:
        return None
    hours = _expected_hours(day)
    for index, (response, (lat, lon)) in enumerate(zip(payload, coordinates)):
        if not _point_is_all_null(response, index, lat, lon, hours):
            return None
    return {'status': ALL_NULL, 'day': day, 'request_sha256': _request_sha(request),
            'sha256': digest(raw), 'bytes': len(raw),
            'point_count': POINT_COUNT, 'hours_per_point': HOURS_PER_POINT}


def remaining_census_dates(gap_contract_raw: bytes, stopped_census_state_raw: bytes, *,
                           expected_state_sha256: str) -> list[str]:
    """The 110 census dates never requested, in calendar order, from the pinned stopped C.

    C must be exactly the stopped v1 state: one reservation, for 2025-08-07, weight 21,
    no terminal receipt, paused on the refused response, the in-flight record holding the
    retained response digest. Anything else is refused rather than reinterpreted.
    """
    need(isinstance(gap_contract_raw, bytes) and isinstance(stopped_census_state_raw, bytes),
         'raw bytes required')
    need(isinstance(expected_state_sha256, str) and SHA.match(expected_state_sha256) is not None,
         'stopped state pin required')
    need(digest(stopped_census_state_raw) == expected_state_sha256, 'stopped census state pin differs')
    allowed = source_calendar(gap_contract_raw)
    need(allowed[0] == STOPPED_DAY, 'stopped day is not the first census date')

    state = _load(stopped_census_state_raw)
    need(type(state) is dict and set(state) == STATE_FIELDS, 'stopped state fields differ')
    need(type(state['schema']) is int and state['schema'] == 1, 'stopped state schema differs')
    need(isinstance(state['policy_sha256'], str) and SHA.match(state['policy_sha256']) is not None and
         isinstance(state['execution_commit'], str) and COMMIT.match(state['execution_commit']) is not None,
         'stopped state identity differs')
    enabled, last = stamp(state['enabled_at']), stamp(state['last_seen'])
    need(enabled <= last, 'stopped state clock differs')

    events = state['events']
    need(type(events) is list and len(events) == 1, 'stopped state must hold exactly one reservation')
    event = events[0]
    request_sha = _request_sha(_request(date.fromisoformat(STOPPED_DAY)))
    need(type(event) is dict and set(event) == EVENT_FIELDS and event['kind'] == 'attempt' and
         event['day'] == STOPPED_DAY and type(event['weight']) is int and event['weight'] == UNITS and
         event['request_sha256'] == request_sha, 'stopped reservation differs')
    need(enabled <= stamp(event['reserved_at']) <= stamp(event['at']) <= last,
         'stopped reservation chronology differs')

    need(type(state['terminal']) is dict and not state['terminal'], 'stopped state adopted a terminal receipt')
    need(state['paused'] == STOPPED_REASON, 'stopped state pause differs')
    inflight = state['inflight']
    need(type(inflight) is dict and set(inflight) == STOPPED_INFLIGHT_FIELDS and
         inflight['day'] == STOPPED_DAY and inflight['request_sha256'] == request_sha and
         inflight['response_sha256'] == STOPPED_RESPONSE_SHA256 and
         type(inflight['response_bytes']) is int and inflight['response_bytes'] == STOPPED_RESPONSE_BYTES,
         'stopped in-flight record differs')

    remaining = [day for day in allowed if day != STOPPED_DAY]
    need(len(remaining) == SUCCESSOR_LIMIT['attempts'] and len(set(remaining)) == len(remaining) and
         remaining == sorted(remaining) and not set(remaining) & {*EXCLUDED, STOPPED_DAY},
         'remaining census calendar differs')
    return remaining
