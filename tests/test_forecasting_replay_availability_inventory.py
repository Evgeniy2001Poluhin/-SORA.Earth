"""Synthetic byte accounting only; no weather values, HTTP or project conftest."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import pytest

from app.services.forecasting import replay_availability_inventory as m


def put(path, value):
    raw = json.dumps(value, sort_keys=True).encode()
    path.write_bytes(raw)
    return m.sha(raw)


@pytest.fixture
def cache(tmp_path):
    root = tmp_path / 'cache'
    root.mkdir()
    (root / 'raw').mkdir()
    days = ['2025-08-04', '2025-08-05', '2025-08-06']
    plan = {'start_issue_date': days[0], 'end_issue_date': days[-1], 'daily_calendar_runs': 3,
            'requests': [{'issue_date': d} for d in days], 'reuse_candidates': []}
    pp = put(root / 'plan.json', plan)
    raw = b'opaque synthetic bytes; deliberately not a weather grid'
    (root / 'raw' / 'ifs-2025-08-04.json').write_bytes(raw)
    policy = put(root / 'http-probe-policy.json', {'failed_day': days[1]})
    body = {'reason': 'The requested model run is not available. Model: ecmwf_ifs, run: 2025-08-05T00:00Z',
            'error': True}
    response = put(root / 'http-probe-response.bin', body)
    state = {'schema': 1, 'plan_sha256': pp, 'completed': {days[0]: {'bytes': len(raw), 'sha256': m.sha(raw),
             'historical_publication': 'not attested'}},
             'events': [{'kind': 'prior_allowance'}, {'kind': 'attempt', 'day': days[1],
                        'reserved_at': '2026-10-23T21:42:59+00:00', 'at': '2026-10-23T21:43:02+00:00',
                        'recovery_amendment_sha256': policy}],
             'inflight': {'day': days[1]}, 'paused': 'request_failed:TransportFailure:400',
             'http_probe': {'policy_sha256': policy, 'reserved_at': '2026-10-23T21:42:59+00:00',
                  'outcome': {'kind': 'transport_failure', 'http_status': 400, 'error_class': 'HTTPError',
                              'returncode': 2, 'body_truncated': False},
                  'response': {'file': 'http-probe-response.bin', 'bytes': (root/'http-probe-response.bin').stat().st_size,
                               'sha256': response}}}
    put(root / 'state.json', state)
    return root, plan, state


def run(cache):
    root, plan, state = cache
    pp = put(root / 'plan.json', plan)
    state['plan_sha256'] = pp
    sp = put(root / 'state.json', state)
    return m.inventory(root, state_sha256=sp, plan_sha256=pp)


def test_complete_partition_and_no_weather_validation(cache):
    result = run(cache)
    assert result['counts'] == {'receipted_hash_checked': 1, 'provider_unavailable_observed': 1, 'not_attempted': 1}
    assert [r['day'] for r in result['rows']] == ['2025-08-04', '2025-08-05', '2025-08-06']
    assert result['calendar_count'] == 3
    assert result['network_requests'] == 0
    for key in ('all_calendar_payloads_hash_checked', 'weather_grid_revalidated',
                'historical_availability_attested', 'scientific_acceptance_evaluated'):
        assert result[key] is False


@pytest.mark.parametrize('change', ['no_probe', 'no_response', 'truncated', 'status', 'different_day', 'plain_text'])
def test_uncertain_response_is_not_unavailable(cache, change):
    root, _, state = cache
    if change == 'no_probe':
        state.pop('http_probe')
    elif change == 'no_response':
        state['http_probe']['response'] = None
    elif change == 'truncated':
        state['http_probe']['outcome']['body_truncated'] = True
    elif change == 'status':
        state['http_probe']['outcome']['http_status'] = 503
    else:
        body = b'not JSON' if change == 'plain_text' else json.dumps({'error': True, 'reason':
            'The requested model run is not available. Model: ecmwf_ifs, run: 2025-08-06T00:00Z'}).encode()
        (root / 'http-probe-response.bin').write_bytes(body)
        state['http_probe']['response'].update(bytes=len(body), sha256=m.sha(body))
    assert run(cache)['rows'][1]['status'] == 'attempted_unresolved'


@pytest.mark.parametrize('change', ['raw_hash', 'raw_size', 'raw_missing', 'raw_symlink', 'extra_file',
    'unreceipted_file', 'probe_hash', 'probe_size', 'probe_path', 'policy_hash', 'reservation',
    'probe_other_day', 'foreign_completion', 'foreign_attempt', 'unknown_event', 'calendar_hole',
    'calendar_duplicate', 'calendar_count', 'noncanonical_date', 'schema', 'raw_directory_symlink'])
def test_corruption_refused(cache, change):
    root, plan, state = cache
    payload = root / 'raw' / 'ifs-2025-08-04.json'
    if change == 'raw_hash': payload.write_bytes(b'corrupted')
    elif change == 'raw_size': state['completed']['2025-08-04']['bytes'] += 1
    elif change == 'raw_missing': payload.unlink()
    elif change == 'raw_symlink':
        payload.unlink(); payload.symlink_to(root / 'plan.json')
    elif change == 'raw_directory_symlink':
        (root / 'raw').rename(root / 'elsewhere'); (root / 'raw').symlink_to(root / 'elsewhere', target_is_directory=True)
    elif change == 'extra_file': (root / 'raw' / 'unrelated.txt').write_bytes(b'x')
    elif change == 'unreceipted_file': (root / 'raw' / 'ifs-2025-08-06.json').write_bytes(b'x')
    elif change == 'probe_hash': (root / 'http-probe-response.bin').write_bytes(b'corrupted')
    elif change == 'probe_size': state['http_probe']['response']['bytes'] += 1
    elif change == 'probe_path': state['http_probe']['response']['file'] = '../outside'
    elif change == 'policy_hash': (root / 'http-probe-policy.json').write_bytes(b'{}')
    elif change == 'reservation': state['http_probe']['reserved_at'] = 'different'
    elif change == 'probe_other_day': state['inflight']['day'] = '2025-08-06'
    elif change == 'foreign_completion': state['completed']['2030-01-01'] = deepcopy(state['completed']['2025-08-04'])
    elif change == 'foreign_attempt': state['events'].append({'kind': 'attempt', 'day': '2030-01-01'})
    elif change == 'unknown_event': state['events'].append({'kind': 'something_new'})
    elif change == 'calendar_hole': plan['requests'].pop()
    elif change == 'calendar_duplicate': plan['requests'].append(plan['requests'][0])
    elif change == 'calendar_count': plan['daily_calendar_runs'] = 2
    elif change == 'noncanonical_date': plan['start_issue_date'] = '20250804'
    elif change == 'schema': state['schema'] = True
    with pytest.raises((m.InventoryRefusal, OSError)):
        run(cache)


def test_inputs_remain_byte_identical(cache):
    run(cache)  # Fixture writes happen before the baseline.
    root = cache[0]
    before = {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    m.inventory(root, state_sha256=m.sha(before['state.json']), plan_sha256=m.sha(before['plan.json']))
    assert {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before


def test_wrong_external_pin_and_duplicate_json(cache):
    root, _, _ = cache
    pp = m.sha((root / 'plan.json').read_bytes())
    with pytest.raises(m.InventoryRefusal, match='pin differs'):
        m.inventory(root, state_sha256='0'*64, plan_sha256=pp)
    raw = b'{"schema":1,"schema":1}'
    (root / 'state.json').write_bytes(raw)
    with pytest.raises(m.InventoryRefusal, match='JSON'):
        m.inventory(root, state_sha256=m.sha(raw), plan_sha256=pp)


def test_concurrent_state_change_refused(cache, monkeypatch):
    root = cache[0]
    run(cache)
    original = m._read
    count = 0
    def changed(path, limit):
        nonlocal count
        if path == root / 'state.json':
            count += 1
            if count == 2: return b'changed'
        return original(path, limit)
    monkeypatch.setattr(m, '_read', changed)
    with pytest.raises(m.InventoryRefusal, match='changed during'):
        m.inventory(root, state_sha256=m.sha((root/'state.json').read_bytes()),
                    plan_sha256=m.sha((root/'plan.json').read_bytes()))


def test_full_byte_coverage_still_not_scientific_acceptance(cache):
    root, _, state = cache
    state.pop('http_probe'); state['inflight'] = None
    for day in ['2025-08-05', '2025-08-06']:
        data = b'opaque'
        (root/'raw'/('ifs-'+day+'.json')).write_bytes(data)
        state['completed'][day] = {'bytes': len(data), 'sha256': m.sha(data)}
    result = run(cache)
    assert result['all_calendar_payloads_hash_checked'] is True
    assert result['scientific_acceptance_evaluated'] is False
    assert result['weather_grid_revalidated'] is False


def test_cli_outputs_same_report(cache):
    expected = run(cache)
    root = cache[0]
    script = Path(__file__).resolve().parents[1]/'scripts'/'forecast_replay_inventory.py'
    result = subprocess.run([sys.executable, '-B', str(script), '--cache', str(root), '--state-sha256',
        expected['state_sha256'], '--plan-sha256', expected['plan_sha256']], capture_output=True, check=True)
    assert json.loads(result.stdout) == expected


@pytest.mark.parametrize('value', [None, '', 'nonsense', '2026-10-23T21:42:59', '2026-10-23T21:42:59+04:00'])
def test_missing_or_non_utc_reservation_refused(cache, value):
    state = cache[2]
    state['http_probe']['reserved_at'] = value
    state['events'][-1]['reserved_at'] = value
    with pytest.raises(m.InventoryRefusal, match='UTC timestamp'):
        run(cache)


def test_both_reservations_absent_refused(cache):
    state = cache[2]
    state['http_probe'].pop('reserved_at')
    state['events'][-1].pop('reserved_at')
    with pytest.raises(m.InventoryRefusal, match='UTC timestamp'):
        run(cache)


@pytest.mark.parametrize('value', [None, '', '2026-10-23T21:42:58+00:00'])
def test_observation_timestamp_missing_or_before_reservation_refused(cache, value):
    cache[2]['events'][-1]['at'] = value
    with pytest.raises(m.InventoryRefusal):
        run(cache)


@pytest.mark.parametrize('field,value', [('body_truncated', 0), ('http_status', 400.0), ('returncode', 2.0)])
def test_exact_outcome_types_required(cache, field, value):
    cache[2]['http_probe']['outcome'][field] = value
    assert run(cache)['rows'][1]['status'] == 'attempted_unresolved'


def test_numeric_one_is_not_json_true(cache):
    root, _, state = cache
    body = json.loads((root/'http-probe-response.bin').read_bytes())
    body['error'] = 1
    raw = json.dumps(body).encode()
    (root/'http-probe-response.bin').write_bytes(raw)
    state['http_probe']['response'].update(bytes=len(raw), sha256=m.sha(raw))
    assert run(cache)['rows'][1]['status'] == 'attempted_unresolved'
