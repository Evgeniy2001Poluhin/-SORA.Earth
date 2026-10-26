"""Full-calendar exploratory replay, offline only; never a live-M3 claim."""
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import math
import os
from pathlib import Path
import re

import pandas as pd

from .baselines import default_baselines, ClimatologyForecast
from .evaluation import evaluate_rolling_origin
from .replay_calibration import CalibrationPair, fit_correction
from .replay_cache_plan import START, cutoff, _request, origins, eligible, P0_MANIFEST_PIN, P0_PROTOCOL_PIN
from .replay_cache_worker import CacheWorker, MAX_RAW, read_regular, write_new, sync_dir
from .replay_p0 import canonical, digest, need, parse_json, parse_payload, read_snapshot
from .replay_stage1 import POINTS, RAW, CAL, METHODS, METRICS

ORIGINS = origins()
ISSUES = tuple(START + timedelta(days=i) for i in range((ORIGINS[-1].date()-timedelta(days=2)-START).days+1))
TRAIN_START = date(2022, 1, 1)
LABEL = 'exploratory twelve2025windows; revised-vintage/event-time only; not untouched holdout or live M3'
SEMANTIC_P0 = '05030934f241cf42ce78197066130c5cfdea31f2365c25cc963f96cd932c075f'
P0_RESULT_PIN = '49c3a8eb2072b42561526d410f1bee1ef35f975ecc7124263d4e8df1c82be5fe'


def validate_protocol(p):
    need(p['id'] == 'M3-P1-FULL-2026-10-22' and p['origins'] == [o.isoformat() for o in ORIGINS] and
         p['training_issue_start'] == str(ISSUES[0]) and p['training_issue_end'] == str(ISSUES[-1]) and
         p['training_runs'] == len(ISSUES), 'full calendar differs')
    need(type(p['points']) is int and p['points'] == len(POINTS) and
         type(p['horizon']) is int and p['horizon'] == 7 and p['methods'] == list(METHODS), 'full comparison differs')
    need(p['window_days'] == 365 and p['min_pairs'] == 180 and p['correction'] == 'mean(actual-raw)' and
         p['promotion_allowed'] is False and p['previous_outcome_exposure'] is True, 'science/claim differs')
    need(p['p0_manifest_sha256'] == P0_MANIFEST_PIN and
         p['parent_plan_sha256'] == '909ef2009f7f9fc6128365bad38e14d9ebee2a744861b7e43afd56eea67e9ffa', 'source/plan pin differs')
    for field in ('recovery_amendment_sha256', 'continuation_sha256'):
        need(isinstance(p[field], str) and re.fullmatch(r'[0-9a-f]{64}', p[field]), 'external source pin required')
    for field in ('worker_commit', 'acquisition_execution_commit', 'analysis_commit'):
        need(isinstance(p[field], str) and re.fullmatch(r'[0-9a-f]{40}', p[field]), 'execution source required')
    retry_fields = ('calendar_retry_sha256', 'calendar_retry_execution_commit')
    if any(field in p for field in retry_fields):
        need(all(field in p for field in retry_fields) and
             isinstance(p['calendar_retry_sha256'], str) and
             re.fullmatch(r'[0-9a-f]{64}', p['calendar_retry_sha256']) and
             isinstance(p['calendar_retry_execution_commit'], str) and
             re.fullmatch(r'[0-9a-f]{40}', p['calendar_retry_execution_commit']),
             'explicit calendar retry pin/source required')


def expected_members():
    return ([(f'ifs-{d}.json', 'training_forecast', d) for d in ISSUES] +
            [('verification.json', 'verification', None)] +
            [(f'forecast-{i:02d}.json', 'test_forecast', o.date()) for i, o in enumerate(ORIGINS)])


def export_snapshot(cache: Path, p0: Path, p0_protocol: Path, protocol: dict, output: Path):
    validate_protocol(protocol)
    need(str(cache.resolve()) == protocol['canonical_ledger'], 'noncanonical cache path')
    raw_protocol = read_regular(p0_protocol, 100000)
    need(digest(raw_protocol) == P0_PROTOCOL_PIN, 'P0 protocol changed')
    original = parse_json(raw_protocol)
    manifest, _, _ = read_snapshot(p0, original, P0_MANIFEST_PIN)
    partial = output.with_name(output.name + '.partial')
    need(output.parent.is_dir() and not output.exists() and not output.is_symlink() and
         not partial.exists() and not partial.is_symlink(), 'new full export directory required')
    partial.mkdir(mode=0o700)
    entries = []
    if 'calendar_retry_sha256' in protocol:
        from .replay_calendar_recovery import CalendarRecoveryWorker
        worker = CalendarRecoveryWorker(cache, protocol['recovery_amendment_sha256'],
                                        protocol['continuation_sha256'], protocol['calendar_retry_sha256'])
    else:
        worker = CacheWorker(cache, recovery_pin=protocol['recovery_amendment_sha256'],
                             continuation_pin=protocol['continuation_sha256'])
    with worker.locked(datetime.now(timezone.utc)) as (state, _, requests):
        need(state['paused'] is None and state['inflight'] is None and
             set(state['completed']) == set(requests) == {str(d) for d in ISSUES}, 'full cache not settled/complete')
        need(state['code_commit'] == protocol['worker_commit'], 'original worker source differs')
        policy = read_regular(cache/'calendar-continuation.json', 100000)
        need(digest(policy) == protocol['continuation_sha256'] and
             parse_json(policy)['execution_commit'] == protocol['acquisition_execution_commit'], 'acquisition source differs')
        need(bool(state.get('calendar_retry')) == ('calendar_retry_sha256' in protocol),
             'calendar retry must be disclosed in protocol')
        if state.get('calendar_retry'):
            retry = parse_json(read_regular(cache/'calendar-retry.json', 100000))
            need(retry['execution_commit'] == protocol['calendar_retry_execution_commit'],
                 'calendar retry execution source differs')
        for day in ISSUES:
            request = _request(day)
            raw = read_regular(cache/'raw'/request['file'], MAX_RAW)
            receipt = state['completed'][str(day)]
            need(digest(raw) == receipt['sha256'] and len(raw) == receipt['bytes'], 'cache response differs')
            worker.validate(raw, request)
            write_new(partial/request['file'], raw)
            entries.append({'file':request['file'], 'kind':'training_forecast', 'issue_date':str(day),
                            'origin':request['origin'], 'bytes':len(raw), 'sha256':digest(raw), 'source':receipt})
        ledger_pin = digest(read_regular(cache/'state.json', 2097152))
    for name, kind, _ in expected_members()[len(ISSUES):]:
        entry = next(e for e in manifest['entries'] if e['file'] == name)
        raw = read_regular(p0/name, 33554432 if kind == 'verification' else MAX_RAW)
        need(digest(raw) == entry['sha256'] and len(raw) == entry['bytes'], 'P0 buffer differs')
        write_new(partial/name, raw)
        entries.append({'file':name, 'kind':kind, 'bytes':len(raw), 'sha256':digest(raw),
                        'source_manifest_sha256':P0_MANIFEST_PIN})
    result = {'complete_full_calendar':True, 'protocol':protocol, 'p0_protocol':original,
              'ledger_sha256':ledger_pin, 'entries':entries, 'generated_at':datetime.now(timezone.utc).isoformat()}
    raw = canonical(result); write_new(partial/'manifest.json', raw)
    os.rename(partial, output)
    try:
        sync_dir(output.parent)
    except OSError as exc:
        raise OSError('full snapshot exists; durability is unconfirmed') from exc
    return digest(raw)


def load_snapshot(directory: Path, protocol: dict, manifest_pin: str):
    validate_protocol(protocol)
    need(directory.is_dir() and not directory.is_symlink(), 'full directory refused')
    manifest_raw = read_regular(directory/'manifest.json', 1048576)
    need(digest(manifest_raw) == manifest_pin, 'external full manifest pin differs')
    manifest = parse_json(manifest_raw)
    need(manifest['protocol'] == protocol and manifest['complete_full_calendar'] is True, 'full export contract differs')
    original = manifest['p0_protocol']
    need(digest(canonical(original)) == SEMANTIC_P0, 'P0 semantic protocol differs')
    expected = expected_members()
    need(len(manifest['entries']) == len(expected), 'full entry count differs')
    forecasts, verification, tests = {}, None, {}
    for entry, (name, kind, day) in zip(manifest['entries'], expected):
        need(entry['file'] == name and entry['kind'] == kind, 'full member/order differs')
        raw = read_regular(directory/name, 33554432 if kind == 'verification' else MAX_RAW)
        need(digest(raw) == entry['sha256'] and len(raw) == entry['bytes'], 'full raw pin differs')
        if kind == 'verification':
            verification = parse_payload(raw, original, {'kind':'verification'})
        else:
            origin = cutoff(day)
            parsed = parse_payload(raw, original, {'kind':'forecast', 'origin':origin.isoformat()})
            if kind == 'training_forecast':
                need(entry['issue_date'] == str(day) and entry['origin'] == origin.isoformat(), 'training issue differs')
                CacheWorker(directory).validate(raw, _request(day))
                forecasts[day] = parsed
            else:
                tests[origin] = parsed
    return verification, forecasts, tests


def evaluate_series(verification, forecasts, tests, protocol):
    validate_protocol(protocol)
    need(set(tests) == set(ORIGINS), 'test window universe differs')
    need(set(forecasts) == set(ISSUES) and set(verification) == set(POINTS), 'full training universe differs')
    for issue, points in forecasts.items():
        need(set(points) == set(POINTS), 'training point omitted')
        for values in points.values():
            for h in range(1, 8):
                day = issue+timedelta(days=h)
                need(day in values and type(values[day]) in (int,float) and math.isfinite(values[day]), 'invalid training grid')
    windows = [evaluate_window(verification, forecasts, tests[o], protocol, o) for o in ORIGINS]
    methods = {}
    for name in METHODS:
        by_point = {c:{m:math.fsum(w['methods'][name]['by_point'][c][m] for w in windows)/len(windows)
                       for m in METRICS} for c in POINTS}
        by_lead = {}
        for h in range(1,8):
            values = [(w['daily_predictions'][c][name][h-1]['actual_minus_predicted_c'], w['mase_scales'][c])
                      for w in windows for c in POINTS]
            by_lead[str(h)] = {
                'mae':math.fsum(abs(e) for e,_ in values)/len(values),
                'rmse':math.sqrt(math.fsum(e*e for e,_ in values)/len(values)),
                'bias':math.fsum(e for e,_ in values)/len(values),
                'mase':math.fsum(abs(e)/scale for e,scale in values)/len(values)}
        methods[name] = {'by_point':by_point, 'by_lead':by_lead,
                         'macro':{m:math.fsum(by_point[c][m] for c in POINTS)/len(POINTS) for m in METRICS},
                         'worst_point_mae':max(POINTS,key=lambda c:by_point[c]['mae'])}
    return {'id':protocol['id'], 'label':LABEL, 'protocol':protocol, 'points':len(POINTS),
            'windows_per_point':len(ORIGINS), 'test_targets':len(POINTS)*len(ORIGINS)*7,
            'methods':methods, 'windows':windows,
            'calibrated_cells':sum(w['calibrated_cells'] for w in windows),
            'raw_fallback_cells':sum(w['raw_fallback_cells'] for w in windows),
            'promotion_allowed':False, 'verdict':'exploratory_only_no_confirmatory_claim',
            'generated_at':datetime.now(timezone.utc).isoformat(),
            'limitations':['previously exposed2025developmentwindows, not untouched holdout',
                           'revised ERA5 and event-time maturity, not historical availability',
                           'point samples are not country averages; no significance/promotion/liveM3 closure',
                           'macroRMSE averages point-windowRMSE; per-leadRMSE pools errors']}


def compare_reference(report, raw_reference, reference_pin):
    """Refuse publication if unchanged raw/baseline paths differ from pinned P0."""
    need(reference_pin == P0_RESULT_PIN and digest(raw_reference) == reference_pin,
         'original P0 result pin differs')
    reference = parse_json(raw_reference)
    need(set(report['methods']) == set(METHODS) and len(report['windows']) == len(ORIGINS),
         'full comparison methods/windows missing')
    need(all(w['origin'] == o.isoformat() for w,o in zip(report['windows'],ORIGINS)), 'full window order differs')
    need(reference['points'] == len(POINTS) and reference['windows_per_point'] == len(ORIGINS) and
         reference['origins'] == [o.isoformat() for o in ORIGINS], 'P0 comparison grid differs')
    checks = 0
    def same(left, right):
        nonlocal checks
        need(type(left) in (int,float) and type(right) in (int,float) and
             math.isfinite(left) and math.isfinite(right) and
             math.isclose(left,right,rel_tol=1e-11,abs_tol=1e-11), 'P0 raw/baseline metric differs')
        checks += 1
    for name in (m for m in METHODS if m != CAL):
        previous = reference['methods'][name]
        need(set(report['methods'][name]['by_point']) == set(POINTS) and
             all(set(w['methods'][name]['by_point']) == set(POINTS) for w in report['windows']), 'full point missing')
        need(set(previous['by_point']) == set(POINTS) and set(previous['windows']) == set(POINTS), 'P0 point missing')
        for metric in METRICS:
            same(report['methods'][name]['macro'][metric], previous['macro'][metric])
        for point in POINTS:
            need(len(previous['windows'][point]) == len(ORIGINS), 'P0 window missing')
            for metric in METRICS:
                same(report['methods'][name]['by_point'][point][metric], previous['by_point'][point][metric])
            for i, window in enumerate(report['windows']):
                need(previous['windows'][point][i]['window'] == i, 'P0 window order differs')
                for metric in METRICS:
                    same(window['methods'][name]['by_point'][point][metric], previous['windows'][point][i][metric])
    return {'status':'same_window_raw_and_baselines_match_p0', 'numeric_comparisons':checks,
            'reference_sha256':reference_pin}


def evaluate_window(verification, forecasts, test, protocol, origin):
    """Pure all-point evaluation; test labels cannot enter correction fitting."""
    validate_protocol(protocol)
    need(set(verification) == set(POINTS) and set(test) == set(POINTS) and
         set(forecasts) == set(ISSUES), 'incomplete point/issue universe')
    targets = [origin.date() + timedelta(days=h) for h in range(1, 8)]
    per_point, corrections, errors, scales = {}, {}, {}, {}
    for code in POINTS:
        actual = verification[code]
        train_days = [TRAIN_START + timedelta(days=i)
                      for i in range((origin.date() - TRAIN_START).days + 1)]
        need(set(train_days + targets) <= actual.keys() and set(targets) <= test[code].keys(), 'incomplete verification/test grid')
        need(all(type(actual[d]) in (int, float) and math.isfinite(actual[d]) for d in train_days + targets), 'invalid actual')
        pairs = []
        for day in ISSUES:
            need(set(forecasts[day]) == set(POINTS), 'missing training point')
            for h in range(1, 8):
                if not eligible(day, origin, h):
                    continue
                target = day + timedelta(days=h)
                need(target in forecasts[day][code] and target in actual, 'missing calibration pair')
                pairs.append(CalibrationPair(code, cutoff(day), h, forecasts[day][code][target], actual[target]))
        fitted = [fit_correction(pairs, point_code=code, lead_day=h, origin=origin) for h in range(1, 8)]
        expected = [sum(eligible(d, origin, h) for d in ISSUES) for h in range(1, 8)]
        need([f.eligible_pairs for f in fitted] == expected, 'calibration selection/count differs')
        corrections[code] = [{**asdict(f), 'origin': f.origin.isoformat(),
                              'first_issue': f.first_issue.isoformat() if f.first_issue else None, 'last_issue': f.last_issue.isoformat() if f.last_issue else None,
                              'last_target_end': f.last_target_end.isoformat() if f.last_target_end else None} for f in fitted]
        predictions = {RAW: [test[code][d] for d in targets],
                       CAL: [f.apply(test[code][d]) for f, d in zip(fitted, targets)]}
        train = pd.DataFrame({'ds': pd.to_datetime(train_days), 'y': [actual[d] for d in train_days]})
        for model in [*default_baselines(), ClimatologyForecast()]:
            model.fit(train, 'y')
            pred = model.predict(7)
            need(pred.dates == [str(d) for d in targets], 'baseline target dates differ')
            predictions[model.model_name] = pred.yhat
        need(set(predictions) == set(METHODS), 'baseline omitted')
        frame = pd.DataFrame({'ds': pd.to_datetime(train_days + targets), 'y': [actual[d] for d in train_days + targets]})
        values = [actual[d] for d in train_days]
        scale = math.fsum(abs(values[i] - values[i - 7]) for i in range(7, len(values))) / (len(values) - 7)
        need(scale > 0 and math.isfinite(scale), 'undefined MASE scale')
        scales[code] = scale
        per_point[code], errors[code] = {}, {}
        for name in METHODS:
            vector = list(predictions[name])
            need(len(vector) == 7 and all(type(v) in (int, float) and math.isfinite(v) for v in vector), 'invalid forecast vector')
            def fit_predict(prefix, horizon):
                need(len(prefix) == len(train_days) and horizon == 7 and prefix['ds'].max().date() == origin.date(), 'evaluation prefix differs')
                return vector
            report = evaluate_rolling_origin(fit_predict, frame, 'y', 7, len(train_days), 1, model_name=name)
            need(len(report.windows) == 1, 'unexpected evaluation windows')
            metric = {key: report.mean(key) for key in METRICS}
            need(all(math.isfinite(v) for v in metric.values()), 'nonfinite score')
            per_point[code][name] = metric
            errors[code][name] = [{'date': str(d), 'lead': h, 'actual_c': actual[d], 'prediction_c': float(v),
                                  'actual_minus_predicted_c': actual[d] - float(v)}
                                 for h, (d, v) in enumerate(zip(targets, vector), start=1)]
    by_method = {name: {'macro': {m: math.fsum(per_point[c][name][m] for c in POINTS) / len(POINTS) for m in METRICS},
                        'by_point': {c: per_point[c][name] for c in POINTS},
                        'worst_point_mae': max(POINTS, key=lambda c: per_point[c][name]['mae'])} for name in METHODS}
    for name in METHODS:
        by_method[name]['by_lead'] = {}
        for h in range(1, 8):
            values = [errors[c][name][h - 1]['actual_minus_predicted_c'] for c in POINTS]
            by_method[name]['by_lead'][str(h)] = {
                'mae': math.fsum(abs(e) for e in values) / len(POINTS),
                'rmse': math.sqrt(math.fsum(e * e for e in values) / len(POINTS)),
                'bias': math.fsum(values) / len(POINTS),
                'mase': math.fsum(abs(e) / scales[c] for c, e in zip(POINTS, values)) / len(POINTS),
            }
    return {'id': protocol['id'], 'label': 'exposed development window; revised-vintage/event-time only', 'protocol': protocol,
            'points': len(POINTS), 'windows_per_point': 1, 'test_targets': len(POINTS) * 7,
            'origin': origin.isoformat(),
            'calibration_pairs': sum(f['eligible_pairs'] for values in corrections.values() for f in values),
            'calibrated_cells': sum(f['status'] == 'calibrated' for values in corrections.values() for f in values),
            'raw_fallback_cells': sum(f['status'] != 'calibrated' for values in corrections.values() for f in values), 'corrections': corrections, 'methods': by_method,
            'mase_scales': scales, 'daily_predictions': errors, 'promotion_allowed': False,
            'verdict': 'exploratory_only_no_confirmatory_claim', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'limitations': ['previously exposed development outcomes',
                            'revised ERA5 labels, event-time maturity only, no historical publication attestation',
                            'fixed daily calendar; trailing365-day eligibility with event-time maturity',
                            'constant mean-bias correction does not optimize MAE',
                            'no uncertainty coverage, significance, model promotion or live M3 completion']}
