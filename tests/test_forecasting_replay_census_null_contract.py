"""Census v2 leaf: all-null classification and the remaining calendar. Synthetic bytes only.

No network, no disk state: every payload, gap contract and stopped state here is built
in the test. The real C state is pinned by digest in the module and never read.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
import copy
import json

import pytest

from app.services.environmental.regions import OPENMETEO_POINTS_V1
from app.services.forecasting import replay_census_null_contract as v2
from app.services.forecasting.replay_cache_plan import _request
from app.services.forecasting.replay_cache_worker import CacheRefusal, CacheWorker, digest
from app.services.forecasting.replay_p0 import ReplayRefusal, canonical
from app.services.forecasting.replay_source_census import BASELINE, COMBINED_LIMIT, EXCLUDED

DAY = v2.STOPPED_DAY
ENABLED = datetime(2026, 10, 25, 9, tzinfo=timezone.utc)
# Synthetic calendar only: the real one comes from the reviewed gap contract.
SYNTHETIC_TAIL = [(date(2025, 8, 8) + timedelta(days=i)).isoformat() for i in range(110)]
SOURCE = ['2025-08-06', DAY, *SYNTHETIC_TAIL]


def _payload(day=DAY, value=None):
    first = date.fromisoformat(day)
    times = ['%sT%02d:00' % ((first + timedelta(days=d)).isoformat(), h) for d in range(8) for h in range(24)]
    points = []
    for index, point in enumerate(OPENMETEO_POINTS_V1):
        temperatures = [value(index, i) if callable(value) else value for i in range(len(times))]
        entry = {'latitude': point.lat + 0.04, 'longitude': point.lon - 0.04,
                 'generationtime_ms': 0.25, 'utc_offset_seconds': 0, 'timezone': 'GMT',
                 'timezone_abbreviation': 'GMT', 'elevation': 120.0,
                 'hourly_units': {'time': 'iso8601', 'temperature_2m': '°C'},
                 'hourly': {'time': list(times), 'temperature_2m': temperatures}}
        if index:
            entry['location_id'] = index  # as in the retained multi-location body
        points.append(entry)
    return points


def _raw(payload):
    return json.dumps(payload).encode()


def _classify(payload, day=DAY, request=None):
    return v2.classify_all_null(_raw(payload) if not isinstance(payload, bytes) else payload,
                                day=day, request=request or _request(date.fromisoformat(day)))


def _weather(index, hour):
    return round(5.0 + index * 0.5 + (hour % 24) * 0.1, 1)


# ---- classify_all_null: positive controls -------------------------------------------------

def test_strict_all_null_grid_is_classified_without_weather_values():
    raw = _raw(_payload())
    record = v2.classify_all_null(raw, day=DAY, request=_request(date.fromisoformat(DAY)))
    assert record == {'status': 'observed_all_null_response', 'day': DAY,
                      'request_sha256': digest(_request(date.fromisoformat(DAY))['url'].encode()),
                      'sha256': digest(raw), 'bytes': len(raw), 'point_count': 21, 'hours_per_point': 192}


def test_all_null_grid_is_still_refused_by_the_original_weather_validator():
    request = _request(date.fromisoformat(DAY))
    with pytest.raises(ReplayRefusal):
        CacheWorker(Path('unused')).validate(_raw(_payload()), request)


def test_ordinary_weather_passes_the_original_validator_and_is_not_all_null():
    request = _request(date.fromisoformat(DAY))
    raw = _raw(_payload(value=_weather))
    CacheWorker(Path('unused')).validate(raw, request)  # meaningful positive control: no refusal
    assert v2.classify_all_null(raw, day=DAY, request=request) is None


def test_the_optional_metadata_may_be_absent_and_utc_spelling_is_accepted():
    payload = _payload()
    for point in payload:
        del point['elevation'], point['generationtime_ms']
        point['timezone'] = point['timezone_abbreviation'] = 'UTC'
    assert _classify(payload)['status'] == v2.ALL_NULL


def test_integer_coordinates_are_numbers_as_in_the_original_parser():
    near = [i for i, p in enumerate(OPENMETEO_POINTS_V1) if abs(round(p.lat) - p.lat) <= 0.25]
    assert near, 'registry has no point near an integer latitude'
    payload = _payload()
    payload[near[0]]['latitude'] = int(round(OPENMETEO_POINTS_V1[near[0]].lat))
    assert type(payload[near[0]]['latitude']) is int
    assert _classify(payload) is not None


# ---- classify_all_null: mutation negatives -------------------------------------------------

def _mutate(change):
    payload = _payload()
    change(payload)
    return payload


def _set_temp(index, value):
    return lambda p: p[3]['hourly']['temperature_2m'].__setitem__(index, value)


NEGATIVES = {
    'empty list': lambda p: p.clear(),
    'empty hourly arrays': lambda p: p[0]['hourly'].update(time=[], temperature_2m=[]),
    'one finite temperature': _set_temp(100, 12.5),
    'one zero temperature': _set_temp(0, 0),
    'one bool temperature': _set_temp(5, False),
    'one string temperature': _set_temp(5, 'null'),
    'partial null: last point weather': lambda p: p[-1]['hourly'].update(
        temperature_2m=[1.0] * 192),
    'missing point': lambda p: p.pop(),
    'extra point': lambda p: p.append(copy.deepcopy(p[0])),
    'point order swapped': lambda p: p.__setitem__(slice(0, 2), [p[1], p[0]]),
    'latitude beyond tolerance': lambda p: p[2].__setitem__('latitude', OPENMETEO_POINTS_V1[2].lat + 0.31),
    'longitude missing': lambda p: p[2].pop('longitude'),
    'bool latitude': lambda p: p[4].__setitem__('latitude', True),
    'string longitude': lambda p: p[4].__setitem__('longitude', str(OPENMETEO_POINTS_V1[4].lon)),
    'units Fahrenheit': lambda p: p[0]['hourly_units'].__setitem__('temperature_2m', '°F'),
    'units extra key': lambda p: p[0]['hourly_units'].__setitem__('precipitation', 'mm'),
    'units missing': lambda p: p[0].pop('hourly_units'),
    'timezone not UTC': lambda p: p[6].__setitem__('timezone', 'Europe/Moscow'),
    'abbreviation missing': lambda p: p[6].pop('timezone_abbreviation'),
    'abbreviation MSK': lambda p: p[6].__setitem__('timezone_abbreviation', 'MSK'),
    'offset nonzero': lambda p: p[6].__setitem__('utc_offset_seconds', 10800),
    'offset bool': lambda p: p[6].__setitem__('utc_offset_seconds', False),
    'offset float': lambda p: p[6].__setitem__('utc_offset_seconds', 0.0),
    'unknown point key': lambda p: p[7].__setitem__('daily', {}),
    'unknown hourly variable': lambda p: p[7]['hourly'].__setitem__('precipitation', [None] * 192),
    'bool elevation': lambda p: p[7].__setitem__('elevation', True),
    'location_id on point 0': lambda p: p[0].__setitem__('location_id', 0),
    'location_id missing on point 5': lambda p: p[5].pop('location_id'),
    'location_id missing everywhere': lambda p: [x.pop('location_id', None) for x in p],
    'location_id bool': lambda p: p[1].__setitem__('location_id', True),
    'location_id float': lambda p: p[3].__setitem__('location_id', 3.0),
    'location_id string': lambda p: p[3].__setitem__('location_id', '3'),
    'location_id wrong index': lambda p: p[3].__setitem__('location_id', 4),
    'location_id duplicate': lambda p: p[4].__setitem__('location_id', 3),
    'huge integer latitude': lambda p: p[7].__setitem__('latitude', 10 ** 400),
    'huge integer elevation': lambda p: p[7].__setitem__('elevation', 10 ** 400),
    'huge integer generationtime': lambda p: p[7].__setitem__('generationtime_ms', 10 ** 400),
    'missing hour': lambda p: (p[8]['hourly']['time'].pop(50), p[8]['hourly']['temperature_2m'].pop(50)),
    'duplicate hour': lambda p: p[8]['hourly']['time'].__setitem__(51, p[8]['hourly']['time'][50]),
    'hours out of order': lambda p: p[8]['hourly']['time'].__setitem__(
        slice(50, 52), [p[8]['hourly']['time'][51], p[8]['hourly']['time'][50]]),
    'wrong calendar: shifted a day': lambda p: p[9]['hourly'].__setitem__(
        'time', p[9]['hourly']['time'][24:] + ['2025-08-15T%02d:00' % h for h in range(24)]),
    'wrong hour format with zone': lambda p: p[9]['hourly']['time'].__setitem__(0, '2025-08-07T00:00Z'),
    'unequal lengths': lambda p: p[10]['hourly']['temperature_2m'].append(None),
    'time not a list': lambda p: p[10]['hourly'].__setitem__('time', 'none'),
    'point not an object': lambda p: p.__setitem__(11, None),
    'root is an object': lambda p: p.__setitem__(slice(None), [{'points': list(p)}]),
}


@pytest.mark.parametrize('name', sorted(NEGATIVES))
def test_mutation_is_not_classified_all_null(name):
    payload = _mutate(NEGATIVES[name])
    assert _raw(payload) != _raw(_payload()), 'mutation did not apply: ' + name  # bytes: True == 1 as dicts
    assert _classify(payload) is None, name


def test_location_id_numbers_every_point_after_the_first():
    payload = _payload()
    assert 'location_id' not in payload[0]
    assert [p['location_id'] for p in payload[1:]] == list(range(1, 21))
    assert _classify(payload) is not None


def test_root_object_instead_of_list_is_not_all_null():
    assert _classify(_payload()[0]) is None


@pytest.mark.parametrize('raw', [
    b'', b'[', b'not json', _raw(_payload())[:-1],
    _raw(_payload()).replace(b'"timezone": "GMT"', b'"timezone": "GMT", "timezone": "GMT"', 1),
    _raw(_payload()).replace(b'null', b'NaN', 1),
    _raw(_payload()).replace(b'null', b'Infinity', 1),
    b'\xff\xfe',
])
def test_malformed_or_ambiguous_json_is_not_all_null(raw):
    assert _classify(raw) is None


def test_duplicate_key_mutation_actually_applied():
    raw = _raw(_payload()).replace(b'"timezone": "GMT"', b'"timezone": "GMT", "timezone": "GMT"', 1)
    assert raw != _raw(_payload())


def test_oversized_body_is_not_all_null():
    assert _classify(b' ' * (v2.MAX_RAW + 1)) is None


def test_forged_request_is_refused_even_when_the_body_fits_it():
    request = _request(date.fromisoformat(DAY))
    forged = dict(request, url=request['url'].replace('ecmwf_ifs', 'icon_global'))
    with pytest.raises(CacheRefusal):
        _classify(_payload(), request=forged)
    with pytest.raises(CacheRefusal):
        _classify(_payload(), request=dict(request, reserved_weight=True))
    with pytest.raises(CacheRefusal):
        _classify(_payload(), request=dict(request, reserved_weight=float(request['reserved_weight'])))


def test_request_for_another_day_is_refused():
    other = _request(date(2025, 8, 8))
    with pytest.raises(CacheRefusal):
        _classify(_payload(), request=other)


def test_body_for_another_day_is_not_all_null_for_this_day():
    request = _request(date(2025, 8, 8))
    assert _classify(_payload(day=DAY), day='2025-08-08', request=request) is None
    assert _classify(_payload(day='2025-08-08'), day='2025-08-08', request=request) is not None


@pytest.mark.parametrize('day', ['2025-8-7', '2025-08-07T00:00', '2025-02-30', 20250807, None])
def test_malformed_day_is_refused(day):
    with pytest.raises(CacheRefusal):
        v2.classify_all_null(_raw(_payload()), day=day, request=_request(date.fromisoformat(DAY)))


# ---- remaining_census_dates -----------------------------------------------------------------

def _gap(dates=None):
    return canonical({'acquisition': {'allowed_dates': list(SOURCE if dates is None else dates)}})


def _state(**changes):
    request_sha = digest(_request(date.fromisoformat(DAY))['url'].encode())
    reserved = ENABLED + timedelta(minutes=5)
    finished = reserved + timedelta(seconds=3)
    state = {'schema': 1, 'policy_sha256': '1' * 64, 'execution_commit': '2' * 40,
             'enabled_at': ENABLED.isoformat(), 'last_seen': finished.isoformat(),
             'events': [{'kind': 'attempt', 'day': DAY, 'weight': 21, 'reserved_at': reserved.isoformat(),
                         'at': finished.isoformat(), 'request_sha256': request_sha}],
             'terminal': {}, 'paused': v2.STOPPED_REASON,
             'inflight': {'day': DAY, 'request_sha256': request_sha,
                          'response_sha256': v2.STOPPED_RESPONSE_SHA256,
                          'response_bytes': v2.STOPPED_RESPONSE_BYTES}}
    for change in changes.values():
        change(state)
    return canonical(state)


def _remaining(state_raw, gap_raw=None):
    return v2.remaining_census_dates(_gap() if gap_raw is None else gap_raw, state_raw,
                                     expected_state_sha256=digest(state_raw))


def test_remaining_is_the_110_never_requested_dates_in_order():
    remaining = _remaining(_state())
    assert remaining == SYNTHETIC_TAIL
    assert len(remaining) == 110 == len(set(remaining))
    assert not set(remaining) & {*EXCLUDED, DAY}


def test_accounting_inherits_one_attempt_and_keeps_the_combined_ceiling():
    assert v2.INHERITED == {'attempts': BASELINE['attempts'] + 1, 'units': BASELINE['units'] + 21}
    assert v2.INHERITED == {'attempts': 487, 'units': 12699}
    assert v2.SUCCESSOR_LIMIT == {'attempts': 110, 'units': 2310}
    assert {k: v2.INHERITED[k] + v2.SUCCESSOR_LIMIT[k] for k in v2.INHERITED} == COMBINED_LIMIT


def test_real_pins_are_the_reviewed_values():
    assert v2.APPROVED_STOPPED_STATE_SHA256 == 'cc33f004e2f5b34d71e13b3bb033ff78ff3ec99770ab9579424fb2ad6e779819'
    assert v2.STOPPED_RESPONSE_SHA256 == 'de80dd0ca7f5eb652a43fe3b2ebc2d3724e4cf65ab785a2d62280dc0ded3f291'
    assert v2.STOPPED_RESPONSE_BYTES == 102740


def test_a_synthetic_state_does_not_match_the_approved_pin():
    raw = _state()
    with pytest.raises(CacheRefusal):
        v2.remaining_census_dates(_gap(), raw, expected_state_sha256=v2.APPROVED_STOPPED_STATE_SHA256)


def test_wrong_state_digest_is_refused():
    raw = _state()
    with pytest.raises(CacheRefusal):
        v2.remaining_census_dates(_gap(), raw, expected_state_sha256='0' * 64)
    with pytest.raises(CacheRefusal):
        v2.remaining_census_dates(_gap(), raw, expected_state_sha256=digest(raw).upper())


def _event(key, value):
    return lambda s: s['events'][0].__setitem__(key, value)


def _inflight(key, value):
    return lambda s: s['inflight'].__setitem__(key, value)


STATE_NEGATIVES = {
    'extra event': lambda s: s['events'].append(dict(s['events'][0], day=SYNTHETIC_TAIL[0])),
    'repeated event': lambda s: s['events'].append(dict(s['events'][0])),
    'no event': lambda s: s['events'].clear(),
    'event day Aug8': _event('day', SYNTHETIC_TAIL[0]),
    'event weight 20': _event('weight', 20),
    'event weight bool': _event('weight', True),
    'event kind prior_allowance': _event('kind', 'prior_allowance'),
    'event request digest forged': _event('request_sha256', '3' * 64),
    'event extra field': _event('note', 'x'),
    'reserved after finish': _event('reserved_at', (ENABLED + timedelta(hours=1)).isoformat()),
    'reserved before enabled': _event('reserved_at', (ENABLED - timedelta(seconds=1)).isoformat()),
    'finish after last_seen': _event('at', (ENABLED + timedelta(hours=2)).isoformat()),
    'naive timestamp': _event('at', '2026-10-25T09:05:03'),
    'cleared pause': lambda s: s.__setitem__('paused', None),
    'other pause': lambda s: s.__setitem__('paused', 'response_not_accepted:CacheRefusal'),
    'terminal adoption fetched': lambda s: s['terminal'].__setitem__(DAY, {'status': 'fetched_ok'}),
    'terminal adoption all-null': lambda s: s['terminal'].__setitem__(DAY, {'status': v2.ALL_NULL}),
    'terminal not a dict': lambda s: s.__setitem__('terminal', []),
    'inflight cleared': lambda s: s.__setitem__('inflight', None),
    'inflight other day': _inflight('day', SYNTHETIC_TAIL[0]),
    'inflight request forged': _inflight('request_sha256', '4' * 64),
    'inflight response digest': _inflight('response_sha256', '5' * 64),
    'inflight response bytes': _inflight('response_bytes', 102741),
    'inflight response bytes string': _inflight('response_bytes', '102740'),
    'inflight body fields': _inflight('body_sha256', '6' * 64),
    'inflight without response': lambda s: s['inflight'].pop('response_sha256'),
    'schema 2': lambda s: s.__setitem__('schema', 2),
    'schema bool': lambda s: s.__setitem__('schema', True),
    'missing field': lambda s: s.pop('last_seen'),
    'extra field': lambda s: s.__setitem__('note', 'x'),
    'policy pin malformed': lambda s: s.__setitem__('policy_sha256', 'x'),
    'commit malformed': lambda s: s.__setitem__('execution_commit', '2' * 39),
    'last_seen before enabled': lambda s: s.__setitem__('last_seen', (ENABLED - timedelta(1)).isoformat()),
}


@pytest.mark.parametrize('name', sorted(STATE_NEGATIVES))
def test_stopped_state_mutation_is_refused(name):
    raw = _state(change=STATE_NEGATIVES[name])
    assert raw != _state(), 'mutation did not apply: ' + name
    with pytest.raises(CacheRefusal):
        _remaining(raw)


def test_state_with_duplicate_keys_is_refused():
    raw = _state().replace(b'"schema": 1', b'"schema": 1,\n  "schema": 1', 1)
    assert raw != _state()
    with pytest.raises(CacheRefusal):
        _remaining(raw)


CALENDAR_NEGATIVES = {
    'duplicate date': [*SOURCE[:-1], SOURCE[-2]],
    'removed date': SOURCE[:-1],
    'Aug5 present': ['2025-08-05', *SOURCE[:-1]],
    'Aug6 absent': [DAY, *SYNTHETIC_TAIL, '2025-11-26'],
    'unsorted': [SOURCE[0], SOURCE[1], SOURCE[3], SOURCE[2], *SOURCE[4:]],
    'Aug7 not first after Aug6': ['2025-08-01', '2025-08-06', DAY, *SYNTHETIC_TAIL[:-1]],
    'Aug7 absent': ['2025-08-06', *SYNTHETIC_TAIL, '2025-11-26'],
}


@pytest.mark.parametrize('name', sorted(CALENDAR_NEGATIVES))
def test_calendar_mutation_is_refused(name):
    with pytest.raises(CacheRefusal):
        _remaining(_state(), _gap(CALENDAR_NEGATIVES[name]))


def test_calendar_negatives_differ_from_the_source():
    for name, dates in CALENDAR_NEGATIVES.items():
        assert dates != SOURCE, name


def test_result_is_a_fresh_list_and_declares_nothing():
    first, second = _remaining(_state()), _remaining(_state())
    assert type(first) is list and first == second and first is not second
    assert all(type(day) is str for day in first)
