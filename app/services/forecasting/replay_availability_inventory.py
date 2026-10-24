"""Offline byte/provenance inventory. Does not validate weather or accept gaps."""
from collections import Counter
from datetime import date, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import re
import stat


class InventoryRefusal(ValueError):
    pass


def require(ok, reason):
    if not ok:
        raise InventoryRefusal(reason)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _pairs(items):
    result = {}
    for key, value in items:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def _json(raw):
    try:
        return json.loads(raw, object_pairs_hook=_pairs)
    except (ValueError, UnicodeError) as exc:
        raise InventoryRefusal('invalid inventory input JSON') from exc


def _read(path, limit):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        require(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), 'regular file required')
        raw = stream.read(limit + 1)
        require(len(raw) <= limit, 'file size limit exceeded')
        return raw


def _pin(path, expected, limit):
    require(isinstance(expected, str) and re.fullmatch('[0-9a-f]{64}', expected), 'SHA256 pin required')
    raw = _read(path, limit)
    require(sha(raw) == expected, 'input pin differs')
    return raw


def _day(value):
    require(isinstance(value, str), 'ISO date required')
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise InventoryRefusal('ISO date required') from exc
    require(parsed.isoformat() == value, 'canonical ISO date required')
    return parsed


def _timestamp(value):
    require(isinstance(value, str) and bool(value), 'explicit UTC timestamp required')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise InventoryRefusal('explicit UTC timestamp required') from exc
    require(parsed.utcoffset() == timedelta(0), 'explicit UTC timestamp required')
    return parsed


def inventory(root, *, state_sha256, plan_sha256):
    """Read pinned local inputs only. Output is accounting, never an export gate.

    The caller must supply trusted pins independently. Same-user replacement of
    ancestor directories is outside this local inspection's threat model.
    """
    root = Path(root)
    require(not root.is_symlink() and root.is_dir(), 'regular cache directory required')
    raw_dir = root / 'raw'
    require(not raw_dir.is_symlink() and raw_dir.is_dir(), 'regular raw directory required')
    state_raw = _pin(root / 'state.json', state_sha256, 2_097_152)
    plan_raw = _pin(root / 'plan.json', plan_sha256, 2_097_152)
    state, plan = _json(state_raw), _json(plan_raw)
    require(isinstance(state, dict) and isinstance(plan, dict), 'object inputs required')
    require(type(state.get('schema')) is int and state['schema'] == 1, 'unsupported ledger schema')
    require(state.get('plan_sha256') == plan_sha256, 'ledger plan pin differs')
    first, last = _day(plan['start_issue_date']), _day(plan['end_issue_date'])
    n = (last - first).days + 1
    require(1 <= n <= 3660 and type(plan['daily_calendar_runs']) is int and
            plan['daily_calendar_runs'] == n, 'calendar count differs')
    days = [(first + timedelta(days=i)).isoformat() for i in range(n)]
    universe = set(days)
    declared = [r['issue_date'] for r in plan['requests']] + [r['issue_date'] for r in plan['reuse_candidates']]
    require(len(declared) == len(set(declared)) and set(declared) == universe, 'declared calendar differs')
    completed, events = state['completed'], state['events']
    require(isinstance(completed, dict) and set(completed) <= universe and isinstance(events, list),
            'ledger dates differ')
    attempts = Counter()
    for event in events:
        require(isinstance(event, dict) and event.get('kind') in ('attempt', 'prior_allowance'),
                'unknown event kind')
        if event['kind'] == 'attempt':
            require(event.get('day') in universe, 'attempt outside calendar')
            attempts[event['day']] += 1
    inflight = state.get('inflight')
    failed_day = None
    if inflight is not None:
        require(isinstance(inflight, dict) and inflight.get('day') in universe, 'invalid inflight day')
        failed_day = inflight['day']
    unavailable_day = None
    probe_evidence = None
    probe = state.get('http_probe')
    if probe is not None:
        require(isinstance(probe, dict), 'probe object required')
        policy_raw = _pin(root / 'http-probe-policy.json', probe['policy_sha256'], 65536)
        policy = _json(policy_raw)
        probe_day = policy['failed_day']
        require(probe_day in universe and failed_day == probe_day and probe_day not in completed,
                'diagnostic day differs')
        matching = [e for e in events if e.get('recovery_amendment_sha256') == probe['policy_sha256']]
        reserved = _timestamp(probe.get('reserved_at'))
        require(len(matching) == 1 and matching[0].get('kind') == 'attempt' and
                matching[0].get('day') == probe_day and
                matching[0].get('reserved_at') == probe.get('reserved_at'), 'diagnostic reservation differs')
        require(_timestamp(matching[0].get('at')) >= reserved, 'diagnostic precedes reservation')
        response = probe.get('response')
        outcome = probe.get('outcome')
        if response is not None:
            require(response.get('file') == 'http-probe-response.bin', 'diagnostic filename differs')
            body = _pin(root / response['file'], response['sha256'], 262144)
            require(type(response['bytes']) is int and response['bytes'] == len(body), 'diagnostic bytes differ')
            probe_evidence = {'day': probe_day, 'response_sha256': response['sha256'],
                              'policy_sha256': probe['policy_sha256'], 'observed_at': matching[0]['at']}
            # Exact known message, not substring matching arbitrary errors. This
            # proves only the recorded response, never permanent source absence.
            expected = {'reason': 'The requested model run is not available. Model: ecmwf_ifs, run: '
                        + probe_day + 'T00:00Z', 'error': True}
            try:
                message = _json(body)
            except InventoryRefusal:
                message = None  # Intact but non-JSON diagnostic is unresolved.
            if (isinstance(outcome, dict) and type(outcome.get('http_status')) is int and
                    type(outcome.get('returncode')) is int and outcome.get('body_truncated') is False and
                    outcome == {'kind': 'transport_failure', 'http_status': 400, 'error_class': 'HTTPError',
                                'returncode': 2, 'body_truncated': False} and
                    isinstance(message, dict) and message.get('error') is True and message == expected):
                unavailable_day = probe_day
    rows = []
    for day in days:
        filename = 'ifs-' + day + '.json'
        path = raw_dir / filename
        row = {'day': day, 'http_attempts_recorded': attempts[day]}
        if day in completed:
            receipt = completed[day]
            require(isinstance(receipt, dict), 'receipt object required')
            payload = _pin(path, receipt['sha256'], 262144)
            require(type(receipt['bytes']) is int and receipt['bytes'] == len(payload), 'receipt bytes differ')
            row.update(status='receipted_hash_checked', file=filename, bytes=len(payload),
                       sha256=receipt['sha256'], historical_publication=receipt.get('historical_publication', 'unknown'))
        else:
            require(not path.exists() and not path.is_symlink(), 'unreceipted raw file')
            if day == unavailable_day:
                row['status'] = 'provider_unavailable_observed'
            elif attempts[day] or day == failed_day:
                row['status'] = 'attempted_unresolved'
            else:
                row['status'] = 'not_attempted'
        rows.append(row)
    require({p.name for p in raw_dir.iterdir()} == {'ifs-' + d + '.json' for d in completed},
            'unexpected raw directory entry')
    # Catch ordinary concurrent ledger changes. This is not an acquisition lock
    # and never creates or opens worker/controller locks for writing.
    require(_read(root / 'state.json', 2_097_152) == state_raw and
            _read(root / 'plan.json', 2_097_152) == plan_raw, 'inputs changed during inspection')
    counts = dict(sorted(Counter(row['status'] for row in rows).items()))
    return {'schema': 1, 'role': 'offline acquisition accounting; no scientific acceptance',
            'state_sha256': state_sha256, 'plan_sha256': plan_sha256,
            'calendar_count': n, 'counts': counts, 'probe_evidence': probe_evidence,
            'ledger_paused': state.get('paused'), 'rows': rows,
            'all_calendar_payloads_hash_checked': len(completed) == n,
            'weather_grid_revalidated': False, 'historical_availability_attested': False,
            'scientific_acceptance_evaluated': False, 'network_requests': 0}
