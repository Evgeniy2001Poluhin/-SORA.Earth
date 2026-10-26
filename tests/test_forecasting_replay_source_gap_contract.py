"""Synthetic metadata only; no weather values or real acquisition."""
from copy import deepcopy
from datetime import date, timedelta
import json

import pytest

from app.services.forecasting import replay_source_gap_contract as m
from app.services.forecasting.replay_availability_inventory import InventoryRefusal


@pytest.fixture
def inputs():
    first = date(2024, 4, 1)
    days = [(first+timedelta(days=i)).isoformat() for i in range(607)]
    gap = '2025-08-05'
    complete = [d for d in days if d != gap][:494]
    parent = dict(id='M3-P1-FULL-2026-10-22', claim='exposed development', correction='mean(actual-raw)',
        horizon=7, methods=['API_IFS_uncalibrated','API_IFS_bias365_min180','Naive',
        'SeasonalNaive(m=7)','MovingAverage(k=7)','Climatology(±3d)'], min_pairs=180,
        origins=['2024-12-31T23:59:59+00:00','2025-01-31T23:59:59+00:00',
        '2025-02-28T23:59:59+00:00','2025-03-31T23:59:59+00:00',
        '2025-04-30T23:59:59+00:00','2025-05-31T23:59:59+00:00',
        '2025-06-30T23:59:59+00:00','2025-07-31T23:59:59+00:00',
        '2025-08-31T23:59:59+00:00','2025-09-30T23:59:59+00:00',
        '2025-10-31T23:59:59+00:00','2025-11-30T23:59:59+00:00'],points=21,
        previous_outcome_exposure=True,promotion_allowed=False,training_issue_start=days[0],
        training_issue_end=days[-1],training_runs=607,window_days=365,
        p0_manifest_sha256='a'*64,p0_result_sha256='b'*64,parent_plan_sha256='c'*64)
    receipt = {'sha256':'d'*64,'bytes':100}
    evidence = {'day':gap,'response_sha256':'e'*64,'policy_sha256':'f'*64,
                'observed_at':'2026-10-23T21:43:02+00:00'}
    state = {'completed':{d:deepcopy(receipt) for d in complete},'plan_sha256':'c'*64,
             'paused':'request_failed:TransportFailure:400','inflight':{'day':gap},
             'http_probe':{'policy_sha256':'f'*64,'response':{'sha256':'e'*64}},
             'events':[{'kind':'prior_allowance','weight':2472}]+
             [{'kind':'attempt','day':d,'weight':21} for d in complete[:484]]+
             [{'kind':'attempt','day':gap,'weight':21}]}
    inv = {'plan_sha256':'c'*64,'calendar_count':607,'probe_evidence':evidence,
           'counts':{'receipted_hash_checked':494,'provider_unavailable_observed':1,'not_attempted':112},
           'rows':[dict(day=d,status='provider_unavailable_observed' if d==gap else
                   'receipted_hash_checked' if d in complete else 'not_attempted',
                   **(receipt if d in complete else {})) for d in days]}
    return parent, inv, state


def pack(inputs):
    parent, inv, state = deepcopy(inputs)
    sr=m.canonical(state);inv['state_sha256']=m.digest(sr)
    pr=m.canonical(parent);ir=m.canonical(inv)
    return dict(parent_raw=pr,inventory_raw=ir,state_raw=sr,parent_pin=m.digest(pr),
                inventory_pin=m.digest(ir),state_pin=m.digest(sr),authorization_pin='1'*64)


def test_fixed_contract_and_readonly_pure_inputs(inputs):
    args=pack(inputs);before=deepcopy(args);c=m.build_contract(**args)
    assert args==before
    assert c['frozen_science']=={k:inputs[0][k] for k in m.FROZEN}
    a=c['acquisition'];assert len(a['allowed_dates'])==112
    assert '2025-08-05' not in a['allowed_dates']
    assert a['max_new_requests']==112 and a['max_new_reserved_units']==2352
    assert a['max_combined_attempts']==597 and a['max_combined_reserved_units']==15009
    assert a['caps']==m.CAPS and a['automatic_retries']==0
    assert c['coverage']['required_valid_weather_payloads']==606
    assert c['coverage']['strict607_weather_grid_complete'] is False
    assert c['stage']=='contract_only_not_execution'
    raw=m.canonical(c);assert m.validate_contract(raw,contract_pin=m.digest(raw),**args)==c


@pytest.mark.parametrize('field,value', [('min_pairs',179),('min_pairs',180.0),('window_days',364),
    ('points',20),('horizon',30),('correction','median(actual-raw)'),('promotion_allowed',True),
    ('previous_outcome_exposure',False),('training_runs',606),('training_issue_end','2025-11-27')])
def test_changed_science_refused(inputs,field,value):
    inputs[0][field]=value
    with pytest.raises(InventoryRefusal):m.build_contract(**pack(inputs))


@pytest.mark.parametrize('change',['second_gap','skip_row','fake_count','attempted_new_date',
    'budget_reset','wrong_probe','wrong_receipt','unpaused','wrong_plan','reordered_calendar'])
def test_new_scope_or_provenance_refused(inputs,change):
    parent,inv,state=inputs
    if change=='second_gap':inv['rows'][-1]['status']='provider_unavailable_observed'
    elif change=='skip_row':inv['rows'].pop()
    elif change=='fake_count':inv['counts']['not_attempted']=0
    elif change=='attempted_new_date':state['events'][1]['day']=inv['rows'][-1]['day']
    elif change=='budget_reset':state['events'].pop(0)
    elif change=='wrong_probe':state['http_probe']['response']['sha256']='0'*64
    elif change=='wrong_receipt':next(iter(state['completed'].values()))['sha256']='0'*64
    elif change=='unpaused':state['paused']=None
    elif change=='wrong_plan':state['plan_sha256']='0'*64
    elif change=='reordered_calendar':inv['rows'].reverse()
    with pytest.raises(InventoryRefusal):m.build_contract(**pack(inputs))


@pytest.mark.parametrize('change',['extra_field','retry','units','new_gap','changed_origins','bool_as_int'])
def test_pinned_contract_tampering_refused(inputs,change):
    args=pack(inputs);c=m.build_contract(**args)
    if change=='extra_field':c['ready']=True
    elif change=='retry':c['acquisition']['retry_gap_allowed']=True
    elif change=='units':c['acquisition']['max_new_reserved_units']+=21
    elif change=='new_gap':c['gap']['issue_date']='2025-08-06'
    elif change=='changed_origins':c['frozen_science']['origins'].pop()
    elif change=='bool_as_int':c['acquisition']['automatic_retries']=False
    raw=m.canonical(c)
    with pytest.raises(InventoryRefusal):m.validate_contract(raw,contract_pin=m.digest(raw),**args)


def test_mismatched_pin_and_duplicate_keys(inputs):
    args=pack(inputs);args['parent_pin']='0'*64
    with pytest.raises(InventoryRefusal):m.build_contract(**args)
    args=pack(inputs);raw=b'{"id":1,"id":1}';args.update(parent_raw=raw,parent_pin=m.digest(raw))
    with pytest.raises(InventoryRefusal):m.build_contract(**args)
