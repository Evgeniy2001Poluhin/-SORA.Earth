"""Full replay tested with deterministic synthetic series and tiny IO doubles."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import math
import pytest

from app.services.forecasting import replay_full as f


def protocol():
    return {'id':'M3-P1-FULL-2026-10-22','origins':[o.isoformat() for o in f.ORIGINS],
            'training_issue_start':str(f.ISSUES[0]),'training_issue_end':str(f.ISSUES[-1]),
            'training_runs':len(f.ISSUES),'points':len(f.POINTS),'horizon':7,'methods':list(f.METHODS),
            'window_days':365,'min_pairs':180,'correction':'mean(actual-raw)',
            'promotion_allowed':False,'previous_outcome_exposure':True,
            'p0_manifest_sha256':f.P0_MANIFEST_PIN,
            'parent_plan_sha256':'909ef2009f7f9fc6128365bad38e14d9ebee2a744861b7e43afd56eea67e9ffa',
            'recovery_amendment_sha256':'a'*64,'continuation_sha256':'b'*64,
            'worker_commit':'a'*40,'acquisition_execution_commit':'b'*40,'analysis_commit':'c'*40}


def test_default_contract_is_full_original_population():
    p=protocol();f.validate_protocol(p)
    assert len(f.ISSUES)==607 and len(f.ORIGINS)==12 and len(f.POINTS)==21
    assert len(f.expected_members())==620 and len(f.METHODS)==6


@pytest.mark.parametrize('key,value', [('origins',[]),('training_runs',606),('points',20),('horizon',6),
    ('methods',[]),('window_days',364),('min_pairs',179),('correction','median'),
    ('promotion_allowed',True),('previous_outcome_exposure',False),('continuation_sha256','bad'),
    ('analysis_commit','bad'),('p0_manifest_sha256','0'*64)])
def test_protocol_cannot_retune_contract(key,value):
    p=protocol();p[key]=value
    with pytest.raises(ValueError): f.validate_protocol(p)


@pytest.fixture
def series(monkeypatch):
    origins=(datetime(2024,12,31,23,59,59,tzinfo=timezone.utc),datetime(2025,1,31,23,59,59,tzinfo=timezone.utc))
    issues=tuple(date(2024,4,1)+timedelta(days=i) for i in range(304))
    monkeypatch.setattr(f,'ORIGINS',origins);monkeypatch.setattr(f,'ISSUES',issues)
    monkeypatch.setattr(f,'POINTS',('DEU',))
    start=date(2022,1,1);end=date(2025,2,7)
    days=[start+timedelta(days=i) for i in range((end-start).days+1)]
    actual={'DEU':{d:float(10+math.sin(i/9)+i*0.002) for i,d in enumerate(days)}}
    forecasts={issue:{'DEU':{issue+timedelta(days=h):actual['DEU'][issue+timedelta(days=h)]+2+h/10
                           for h in range(1,8)}} for issue in issues}
    tests={o:{'DEU':{o.date()+timedelta(days=h):actual['DEU'][o.date()+timedelta(days=h)]+2+h/10
                    for h in range(1,8)}} for o in origins}
    return actual,forecasts,tests,protocol()


def test_full_metrics_and_counts_from_vectors(series):
    actual,forecasts,tests,p=series;report=f.evaluate_series(actual,forecasts,tests,p)
    assert report['test_targets']==14 and report['calibrated_cells']==14 and report['raw_fallback_cells']==0
    assert report['methods'][f.RAW]['macro']['mae']==pytest.approx(2.4)
    assert report['methods'][f.CAL]['macro']['mae']<1e-12
    assert report['windows'][0]['corrections']['DEU'][0]['eligible_pairs']==273
    for name in f.METHODS:
        windows=report['windows']; errors=[r['actual_minus_predicted_c'] for w in windows for r in w['daily_predictions']['DEU'][name]]
        assert report['methods'][name]['macro']['mae']==pytest.approx(sum(abs(e) for e in errors)/14)
        assert report['methods'][name]['macro']['rmse']==pytest.approx(sum(w['methods'][name]['macro']['rmse'] for w in windows)/2)
        for h in range(1,8):
            values=[w['daily_predictions']['DEU'][name][h-1]['actual_minus_predicted_c'] for w in windows]
            assert report['methods'][name]['by_lead'][str(h)]['rmse']==pytest.approx(math.sqrt(sum(e*e for e in values)/2))


def test_future_labels_change_scores_but_not_first_origin_fits_or_predictions(series):
    a,forecasts,tests,p=series
    before=f.evaluate_series(a,forecasts,tests,p)
    actual=deepcopy(a)
    for day in actual['DEU']:
        if day>f.ORIGINS[0].date():actual['DEU'][day]+=100
    after=f.evaluate_series(actual,forecasts,tests,p)
    left,right=before['windows'][0],after['windows'][0]
    assert left['corrections']==right['corrections'] and left['mase_scales']==right['mase_scales']
    for name in f.METHODS:
        assert [r['prediction_c'] for r in left['daily_predictions']['DEU'][name]]==[
            r['prediction_c'] for r in right['daily_predictions']['DEU'][name]]
    assert right['methods'][f.CAL]['macro']['mae']==pytest.approx(100)


@pytest.mark.parametrize('defect',['point','issue','window','test_point','target','training_target','actual_gap','nan','bool'])
def test_incomplete_grid_refuses_without_dropping_population(series,defect):
    a,forecasts,tests,p=deepcopy(series)
    if defect=='point':a.pop('DEU')
    if defect=='issue':forecasts.pop(f.ISSUES[0])
    if defect=='window':tests.pop(f.ORIGINS[0])
    if defect=='test_point':tests[f.ORIGINS[0]].pop('DEU')
    if defect=='target':tests[f.ORIGINS[0]]['DEU'].pop(date(2025,1,1))
    if defect=='training_target':forecasts[f.ISSUES[-1]]['DEU'].pop(f.ISSUES[-1]+timedelta(days=7))
    if defect=='actual_gap':a['DEU'].pop(date(2024,3,1))
    if defect=='nan':a['DEU'][date(2024,3,1)]=float('nan')
    if defect=='bool':a['DEU'][date(2024,3,1)]=False
    with pytest.raises(ValueError):f.evaluate_series(a,forecasts,tests,p)


def test_manifest_pin_checked_before_payloads(tmp_path):
    (tmp_path/'manifest.json').write_bytes(b'{}')
    with pytest.raises(ValueError,match='external full manifest'):f.load_snapshot(tmp_path,protocol(),'0'*64)


def test_explicit_raw_fallback_keeps_full_scoring_population(series,monkeypatch):
    a,forecasts,tests,p=series
    issues=f.ISSUES[:100];monkeypatch.setattr(f,'ISSUES',issues)
    forecasts={d:forecasts[d] for d in issues};p=protocol()
    report=f.evaluate_series(a,forecasts,tests,p)
    assert report['test_targets']==14 and report['raw_fallback_cells']==14
    assert report['calibrated_cells']==0
    assert report['methods'][f.RAW]==report['methods'][f.CAL]


def reference_for(report):
    return {'points':len(f.POINTS),'windows_per_point':len(f.ORIGINS),
            'origins':[o.isoformat() for o in f.ORIGINS],
            'methods':{name:{'macro':deepcopy(report['methods'][name]['macro']),
                             'by_point':deepcopy(report['methods'][name]['by_point']),
                             'windows':{c:[{'window':i,**w['methods'][name]['by_point'][c]}
                                           for i,w in enumerate(report['windows'])] for c in f.POINTS}}
                       for name in f.METHODS if name!=f.CAL}}


@pytest.mark.parametrize('defect',[None,'wrong_metric','missing_point','window_order','wrong_pin'])
def test_reference_comparison_positive_and_negative_controls(series,monkeypatch,defect):
    a,forecasts,tests,p=series;report=f.evaluate_series(a,forecasts,tests,p)
    raw=f.canonical(reference_for(report));pin=f.digest(raw);monkeypatch.setattr(f,'P0_RESULT_PIN',pin)
    if defect=='wrong_metric':report['methods'][f.RAW]['macro']['mae']+=1
    if defect=='missing_point':report['methods'][f.RAW]['by_point'].pop('DEU')
    if defect=='window_order':report['windows'].reverse()
    if defect=='wrong_pin':pin='0'*64
    if defect:
        with pytest.raises(ValueError):f.compare_reference(report,raw,pin)
    else:
        assert f.compare_reference(report,raw,pin)['numeric_comparisons']==80
