"""One fixed retrospective calibration window, not live M3 or a holdout claim.

Offline only. No app registration, network, training persistence or promotion.
The caller must supply independent manifest/protocol pins. Input data are public
retrieved-vintage ERA5 and IFS, not historically attested publication records.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
import math
import os
from pathlib import Path

import pandas as pd

from app.services.environmental.regions import OPENMETEO_POINTS_V1
from app.services.forecasting.baselines import default_baselines, ClimatologyForecast
from app.services.forecasting.evaluation import evaluate_rolling_origin
from app.services.forecasting.replay_calibration import CalibrationPair, fit_correction
from app.services.forecasting.replay_cache_plan import cutoff, _request, P0_MANIFEST_PIN, P0_PROTOCOL_PIN
from app.services.forecasting.replay_cache_worker import (
    CacheWorker, MAX_RAW, read_regular, write_new, sync_dir,
)
from app.services.forecasting.replay_p0 import canonical, digest, need, parse_json, parse_payload, read_snapshot

ORIGIN = datetime(2024, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
ISSUES = tuple(date(2024, 6, 27) + timedelta(days=i) for i in range(180))
POINTS = tuple(p.code for p in OPENMETEO_POINTS_V1)
RAW = 'API_IFS_uncalibrated'
CAL = 'API_IFS_bias365_min180'
METHODS = (RAW, CAL, 'Naive', 'SeasonalNaive(m=7)', 'MovingAverage(k=7)', 'Climatology(±3d)')
METRICS = ('mae', 'rmse', 'mase', 'bias')
LABEL = 'exploratory single January2025 window; revised-vintage/event-time only; not untouched holdout or live M3'


def validate_protocol(protocol):
    """The external execution freeze cannot silently choose a different window."""
    need(protocol['id'] == 'M3-P1-STAGE1-2026-10-22' and
         protocol['origin'] == ORIGIN.isoformat() and protocol['training_runs'] == len(ISSUES) and
         protocol['training_issue_start'] == str(ISSUES[0]) and
         protocol['training_issue_end'] == str(ISSUES[-1]), 'stage1 calendar differs')
    need(protocol['points'] == len(POINTS) and protocol['horizon'] == 7 and
         protocol['methods'] == list(METHODS) and protocol['promotion_allowed'] is False and
         protocol['previous_outcome_exposure'] is True, 'stage1 comparison/claim differs')
    need(protocol['p0_manifest_sha256'] == P0_MANIFEST_PIN and
         protocol['parent_plan_sha256'] == '909ef2009f7f9fc6128365bad38e14d9ebee2a744861b7e43afd56eea67e9ffa', 'stage1 source/plan pin differs')


def export_snapshot(cache: Path, p0: Path, p0_protocol: Path, protocol: dict, output: Path):
    """Durable standalone partial snapshot. Never claims all607 cache dates exist.

    Each copied buffer is pinned and later re-parsed by load_snapshot; a failure
    preserves .partial for review. Same-user hostile writes are out of scope.
    """
    validate_protocol(protocol)
    need(str(cache.resolve()) == protocol['canonical_ledger'], 'noncanonical cache path')
    p0raw = read_regular(p0_protocol, 100000)
    need(digest(p0raw) == P0_PROTOCOL_PIN, 'P0 protocol changed')
    original = parse_json(p0raw)
    manifest, _, _ = read_snapshot(p0, original, P0_MANIFEST_PIN)
    partial = output.with_name(output.name + '.partial')
    need(output.parent.is_dir() and not output.exists() and not output.is_symlink() and
         not partial.exists() and not partial.is_symlink(), 'new export directory required')
    partial.mkdir(mode=0o700)
    entries = []
    worker = CacheWorker(cache)
    with worker.locked(datetime.now(timezone.utc)) as (state, _, _):
        need(state['paused'] is None and state['inflight'] is None, 'cache not settled')
        need(state['code_commit'] == protocol['worker_commit'], 'cache execution commit differs')
        need(len(state['completed']) == protocol['partial_cache_expected_total'], 'unexpected cache extent')
        need({str(d) for d in ISSUES} <= state['completed'].keys(), 'first slice incomplete')
        for day in ISSUES:
            request = _request(day)
            raw = read_regular(cache / 'raw' / request['file'], MAX_RAW)
            receipt = state['completed'][str(day)]
            need(digest(raw) == receipt['sha256'] and len(raw) == receipt['bytes'], 'cache response differs')
            worker.validate(raw, request)
            write_new(partial / request['file'], raw)
            entries.append({'file': request['file'], 'kind': 'training_forecast',
                            'issue_date': str(day), 'origin': request['origin'],
                            'bytes': len(raw), 'sha256': digest(raw), 'source': receipt})
        ledger_sha = digest(read_regular(cache / 'state.json', 2097152))
    for name, kind in [('verification.json', 'verification'), ('forecast-00.json', 'test_forecast')]:
        entry = next(e for e in manifest['entries'] if e['file'] == name)
        raw = read_regular(p0 / name, 33554432)
        need(digest(raw) == entry['sha256'] and len(raw) == entry['bytes'], 'P0 source buffer changed')
        write_new(partial / name, raw)
        entries.append({'file': name, 'kind': kind, 'bytes': len(raw), 'sha256': digest(raw),
                        'source_manifest_sha256': P0_MANIFEST_PIN})
    result = {'complete_stage1': True, 'full_cache_complete': False,
              'protocol': protocol, 'ledger_sha256': ledger_sha, 'entries': entries,
              'p0_protocol': original, 'generated_at': datetime.now(timezone.utc).isoformat()}
    raw = canonical(result)
    write_new(partial / 'manifest.json', raw)
    os.rename(partial, output)
    try:
        sync_dir(output.parent)
    except OSError as exc:
        raise OSError('stage1 snapshot exists; durability is unconfirmed') from exc
    return digest(raw)


def load_snapshot(directory: Path, protocol: dict, manifest_pin: str):
    validate_protocol(protocol)
    need(directory.is_dir() and not directory.is_symlink(), 'stage1 directory refused')
    raw = read_regular(directory / 'manifest.json', 1048576)
    need(digest(raw) == manifest_pin, 'stage1 external manifest pin differs')
    manifest = parse_json(raw)
    need(manifest['protocol'] == protocol and manifest['complete_stage1'] is True and
         manifest['full_cache_complete'] is False, 'stage1 export contract differs')
    original = manifest['p0_protocol']
    need(digest(canonical(original)) == '05030934f241cf42ce78197066130c5cfdea31f2365c25cc963f96cd932c075f',
         'P0 semantic protocol pin differs')
    need(original['points'] == [{'code': p.code, 'lat': p.lat, 'lon': p.lon}
                               for p in OPENMETEO_POINTS_V1] and
         original['observation_min_hours'] == 19 and original['forecast_min_hours'] == 24 and
         original['training_start'] == '2022-01-01', 'P0 parser settings differ')
    expected = [(f'ifs-{day}.json', 'training_forecast', day) for day in ISSUES]
    expected += [('verification.json', 'verification', None), ('forecast-00.json', 'test_forecast', None)]
    entries = manifest['entries']
    need(len(entries) == len(expected), 'stage1 entry count differs')
    forecasts, verification, test = {}, None, None
    for entry, (name, kind, day) in zip(entries, expected):
        need(entry['file'] == name and entry['kind'] == kind, 'stage1 member/order differs')
        payload = read_regular(directory / name, 33554432 if kind == 'verification' else MAX_RAW)
        need(len(payload) == entry['bytes'] and digest(payload) == entry['sha256'], 'stage1 raw pin differs')
        if day:
            need(entry['issue_date'] == str(day) and entry['origin'] == cutoff(day).isoformat(), 'training issue differs')
            CacheWorker(directory).validate(payload, _request(day))
            forecasts[day] = parse_payload(payload, original, {'kind': 'forecast', 'origin': cutoff(day).isoformat()})
        elif kind == 'verification':
            verification = parse_payload(payload, original, {'kind': 'verification'})
        else:
            test = parse_payload(payload, original, {'kind': 'forecast', 'origin': ORIGIN.isoformat()})
    return verification, forecasts, test


def evaluate_series(verification, forecasts, test, protocol):
    """Pure all-point evaluation; test labels cannot enter correction fitting."""
    validate_protocol(protocol)
    need(set(verification) == set(POINTS) and set(test) == set(POINTS) and
         set(forecasts) == set(ISSUES), 'incomplete point/issue universe')
    targets = [ORIGIN.date() + timedelta(days=h) for h in range(1, 8)]
    per_point, corrections, errors, scales = {}, {}, {}, {}
    for code in POINTS:
        actual = verification[code]
        train_days = [date(2022, 1, 1) + timedelta(days=i)
                      for i in range((ORIGIN.date() - date(2022, 1, 1)).days + 1)]
        need(set(train_days + targets) <= actual.keys() and set(targets) <= test[code].keys(), 'incomplete verification/test grid')
        need(all(type(actual[d]) in (int, float) and math.isfinite(actual[d]) for d in train_days + targets), 'invalid actual')
        pairs = []
        for day in ISSUES:
            need(set(forecasts[day]) == set(POINTS), 'missing training point')
            for h in range(1, 8):
                target = day + timedelta(days=h)
                need(target in forecasts[day][code] and target in actual, 'missing calibration pair')
                pairs.append(CalibrationPair(code, cutoff(day), h, forecasts[day][code][target], actual[target]))
        fitted = [fit_correction(pairs, point_code=code, lead_day=h, origin=ORIGIN) for h in range(1, 8)]
        need(all(f.eligible_pairs == 180 and f.status == 'calibrated' for f in fitted), 'stage1 does not have all180 mature pairs')
        corrections[code] = [{**asdict(f), 'origin': f.origin.isoformat(),
                              'first_issue': f.first_issue.isoformat(), 'last_issue': f.last_issue.isoformat(),
                              'last_target_end': f.last_target_end.isoformat()} for f in fitted]
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
                need(len(prefix) == len(train_days) and horizon == 7 and prefix['ds'].max().date() == ORIGIN.date(), 'evaluation prefix differs')
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
    return {'id': protocol['id'], 'label': LABEL, 'protocol': protocol,
            'points': len(POINTS), 'windows_per_point': 1, 'test_targets': len(POINTS) * 7,
            'calibration_pairs': len(POINTS) * 7 * 180, 'calibrated_cells': len(POINTS) * 7,
            'raw_fallback_cells': 0, 'corrections': corrections, 'methods': by_method,
            'mase_scales': scales, 'daily_predictions': errors, 'promotion_allowed': False,
            'verdict': 'exploratory_only_no_confirmatory_claim', 'generated_at': datetime.now(timezone.utc).isoformat(),
            'limitations': ['one January2025 development window, previously exposed outcomes',
                            'revised ERA5 labels, event-time maturity only, no historical publication attestation',
                            'predeclared180 common issuance dates, not the complete trailing365-day calendar',
                            'constant mean-bias correction does not optimize MAE',
                            'no uncertainty coverage, significance, model promotion or live M3 completion']}
