"""Pure metadata compiler for a separate exploratory single-gap contract.

No IO, collection, weather parsing, fitting or scientific acceptance. Independently
reviewed input pins are a caller prerequisite, not supplied by untrusted outputs.
"""
from collections import Counter
from datetime import date, timedelta
import hashlib
import json
import re

from .replay_availability_inventory import _json, _timestamp, require


FROZEN = ('claim', 'correction', 'horizon', 'methods', 'min_pairs', 'origins',
          'points', 'previous_outcome_exposure', 'promotion_allowed',
          'training_issue_start', 'training_issue_end', 'training_runs',
          'window_days', 'p0_manifest_sha256', 'p0_result_sha256')
CAPS = {'rolling60s': 400, 'rolling3600s': 4000,
        'rolling86400s': 7000, 'spacing_seconds_after_response': 4}


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n').encode()


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _pinned(raw, pin):
    require(isinstance(pin, str) and re.fullmatch('[0-9a-f]{64}', pin) and
            isinstance(raw, bytes) and digest(raw) == pin, 'independent metadata pin differs')
    return _json(raw)


def build_contract(parent_raw, inventory_raw, state_raw, *, parent_pin, inventory_pin,
                   state_pin, authorization_pin):
    parent = _pinned(parent_raw, parent_pin)
    inventory = _pinned(inventory_raw, inventory_pin)
    state = _pinned(state_raw, state_pin)
    require(isinstance(authorization_pin, str) and re.fullmatch('[0-9a-f]{64}', authorization_pin),
            'owner instruction context pin required')
    require(all(isinstance(x, dict) for x in (parent, inventory, state)), 'metadata objects required')
    require(parent['id'] == 'M3-P1-FULL-2026-10-22' and
            type(parent['window_days']) is int and parent['window_days'] == 365 and
            type(parent['min_pairs']) is int and parent['min_pairs'] == 180 and
            type(parent['points']) is int and parent['points'] == 21 and
            type(parent['horizon']) is int and parent['horizon'] == 7 and
            parent['correction'] == 'mean(actual-raw)' and len(parent['origins']) == 12 and
            len(set(parent['origins'])) == 12 and parent['previous_outcome_exposure'] is True and
            parent['promotion_allowed'] is False, 'frozen scientific design differs')
    for origin in parent['origins']:
        _timestamp(origin)
    require(parent['methods'] == ['API_IFS_uncalibrated', 'API_IFS_bias365_min180', 'Naive',
            'SeasonalNaive(m=7)', 'MovingAverage(k=7)', 'Climatology(±3d)'], 'methods differ')
    first, last = date.fromisoformat(parent['training_issue_start']), date.fromisoformat(parent['training_issue_end'])
    require(first.isoformat() == '2024-04-01' and last.isoformat() == '2025-11-28' and
            type(parent['training_runs']) is int and parent['training_runs'] == 607, 'calendar differs')
    days = [(first + timedelta(days=i)).isoformat() for i in range((last-first).days+1)]
    require(inventory['state_sha256'] == state_pin and
            inventory['plan_sha256'] == state['plan_sha256'] == parent['parent_plan_sha256'] and
            inventory['calendar_count'] == len(days) and
            [row['day'] for row in inventory['rows']] == days, 'inventory identity/calendar differs')
    rows = {row['day']: row for row in inventory['rows']}
    counts = dict(Counter(row['status'] for row in rows.values()))
    require(counts == inventory['counts'] == {'receipted_hash_checked': 494,
            'provider_unavailable_observed': 1, 'not_attempted': 112}, 'reviewed coverage differs')
    gap = '2025-08-05'
    require(rows[gap]['status'] == 'provider_unavailable_observed' and
            set(state['completed']) == {d for d in days if rows[d]['status'] == 'receipted_hash_checked'},
            'gap or parent receipts differ')
    require(inventory['probe_evidence']['day'] == gap and
            state['inflight']['day'] == gap and state['paused'] == 'request_failed:TransportFailure:400',
            'reviewed stop differs')
    probe = state['http_probe']
    require(inventory['probe_evidence']['response_sha256'] == probe['response']['sha256'] and
            inventory['probe_evidence']['policy_sha256'] == probe['policy_sha256'], 'probe evidence differs')
    for day, receipt in state['completed'].items():
        require(rows[day]['sha256'] == receipt['sha256'] and
                type(rows[day]['bytes']) is int and type(receipt['bytes']) is int and
                rows[day]['bytes'] == receipt['bytes'], 'inventory receipt differs')
    events = state['events']
    require(all(e['kind'] in ('attempt', 'prior_allowance') and type(e['weight']) is int and
                e['weight'] > 0 for e in events), 'expense history differs')
    attempts = [e for e in events if e['kind'] == 'attempt']
    allowed = [d for d in days if rows[d]['status'] == 'not_attempted']
    require(not (set(allowed) & {e['day'] for e in attempts}), 'new scope contains attempted dates')
    require(len(attempts) == 485 and sum(e['weight'] for e in events) == 12657,
            'reviewed expense baseline differs')
    return {
        'schema': 1, 'id': 'M3-P1-SINGLE-SOURCE-GAP-V1-2026-10-24',
        'stage': 'contract_only_not_execution', 'owner_instruction_context_sha256': authorization_pin,
        'parent_science_sha256': parent_pin, 'parent_inventory_sha256': inventory_pin,
        'parent_state_sha256': state_pin, 'frozen_science': {k: parent[k] for k in FROZEN},
        'gap': {'issue_date': gap, 'scope': 'entire run across all21points and7leads',
                'basis': 'pinned recorded unavailable response; not permanent global absence',
                'evidence': inventory['probe_evidence'], 'maximum_permitted_missing_runs': 1},
        'coverage': {'expected_calendar_dates': len(days), 'required_valid_weather_payloads': len(days)-1,
                     'calendar_accounted': 'every date has a receipt or this one pinned gap',
                     'strict607_weather_grid_complete': False,
                     'analysis_contract_satisfied': 'not evaluated by this compiler',
                     'additional_gap_or_unresolved_failure': 'STOP; no automatic new exclusion'},
        'acquisition': {'allowed_dates': allowed, 'max_new_requests': len(allowed),
                        'reserved_units_per_request': 21, 'max_new_reserved_units': len(allowed)*21,
                        'prior_attempts': len(attempts), 'prior_reserved_units': sum(e['weight'] for e in events),
                        'max_combined_attempts': len(attempts)+len(allowed),
                        'max_combined_reserved_units': sum(e['weight'] for e in events)+len(allowed)*21,
                        'caps': CAPS.copy(), 'automatic_retries': 0, 'retry_gap_allowed': False,
                        'execution_policy_and_clean_source_freeze_required': True,
                        'shared_expense_history_and_controller_lock_required': True,
                        'parent_ledger_pause_receipts_raw_and_protocol_immutable': True,
                        'new_receipts_and_bytes_in_separate_namespace': True},
        'analysis': {'training_gap_handling': 'omit only this issue run uniformly; never impute it',
                     'targets_and_all_methods': 'unchanged complete P0 targets; all21points and7leads',
                     'pair_selection': 'original mature event-time rule;365-day issuance window',
                     'insufficient_pairs': 'original raw_fallback_insufficient_history; never drop cell',
                     'report_required': ['per-cell pair counts', 'selected-key hashes', 'fallback flags',
                                         'calendar and weather coverage separately', 'all source and protocol pins'],
                     'comparability_gate': 'unchanged raw and four baseline P0 results must reproduce',
                     'claim': 'exposed exploratory/revised-vintage; missingness bias unknown',
                     'liveM3_or_holdout_or_promotion_claim_allowed': False,
                     'full_metrics_before_independent_review_allowed': False}}


def validate_contract(raw, *, contract_pin, **inputs):
    supplied = _pinned(raw, contract_pin)
    expected = build_contract(**inputs)
    # Canonical byte equality also rejects bool/int substitutions and extra keys.
    require(canonical(supplied) == canonical(expected), 'contract changed from reviewed inputs')
    return supplied
