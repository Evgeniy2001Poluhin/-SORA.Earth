"""Freeze M6 (country indicators only) and evaluate it once on new IEG evaluations.

Plan: IEG-ПРАВИЛА-ЦЕЛЬ-ПРОТОКОЛ-ПРОЕКТ-20261108.md, section 10 (2026-11-09).

  freeze    refit M6 exactly as section 9 did (train only), write model.json with
            coefficients, scaling, the train success rate (M0), the hashes of the
            WDI, Projects API and IEG inputs and the list of every IEG project ID
            already seen. Refused unless the frozen model reproduces the section 9
            M6 test predictions to 1e-6.
  evaluate  new test = projects whose ID is absent from the frozen IEG export;
            rules P1, P3, P5, target A, Approval FY >= 2014; country from the
            frozen Projects snapshot, values from the frozen WDI snapshot. Below
            300 projects or 30 per class only counts are reported. Decision as in
            section 7, bootstrap over countries. The model file must have the
            SHA-256 recorded in FROZEN_MODEL_SHA256.
Prediction uses only the stored numbers (no scikit-learn at evaluation).
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

import numpy as np

from ieg_join import Refusal
import ieg_baseline
from ieg_country_features import CANDIDATES, LAG, load_snapshot, transform
from ieg_outcomes import project_verdict, read_ieg

FROZEN_MODEL_SHA256 = '471d7cb18e9f81b71484e33f858e4a4b2054e534ef98f626737a09108e7f017d'  # protocol section 10
MIN_PROJECTS, MIN_PER_CLASS = 300, 30
MIN_APPROVAL_FY = 2014
REPRODUCE_TOLERANCE = 1e-6
MODEL_NAME = 'M6_country_only'


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def country_of(projects):
    out = {}
    for p in projects:
        code = p.get('countrycode')
        out[p['id']] = (code[0] if isinstance(code, list) and code else code) or ''
    return out


def features_for(country, approval_fy, wdi, columns):
    year = approval_fy - LAG
    return {c: transform(wdi[CANDIDATES[c][0]].get(f'{country}|{year}'), CANDIDATES[c][1])
            for c in columns}


def predict(model, rows):
    """Probability from the stored numbers only."""
    out = []
    for r in rows:
        z = model['intercept']
        for (col, kind), w in zip(model['vocab'], model['coef']):
            if kind == 'missing':
                z += w * (1.0 if r[col] == '' else 0.0)
            elif r[col] != '':
                mean, std = model['scale'][col]
                z += w * (float(r[col]) - mean) / std
        out.append(1.0 / (1.0 + math.exp(-z)))
    return np.array(out)


def freeze(features_dir, outcomes_dir, country_dir, baseline_dir, projects_dir, wdi_dir,
           ieg_csv, out_dir):
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise Refusal('output_directory_exists')
    table, build, _ = ieg_baseline.load(features_dir, outcomes_dir, country_dir=country_dir)
    columns = ieg_baseline.country_columns(country_dir)
    creport = ieg_baseline.read_json(Path(country_dir) / 'country-feature-report.json')
    _, wdi_manifest = load_snapshot(wdi_dir, 'wdi.json')
    _, projects_manifest = load_snapshot(projects_dir, 'projects.json')
    if creport['inputs']['wdi_json_sha256'] != wdi_manifest['json_sha256']:
        raise Refusal('country_features_not_from_this_wdi_snapshot')
    ieg_sha = sha256(ieg_csv)
    if ieg_sha != build['inputs']['ieg_csv_sha256']:
        raise Refusal('ieg_csv_is_not_the_source_of_the_outcomes')
    train = [r for r in table if r['split'] == 'train']
    test = [r for r in table if r['split'] == 'test']
    x_train, _, vocab = ieg_baseline.encode(train, test, columns)
    y_train = np.array([r['y'] for r in train])
    fitted = ieg_baseline.LogisticRegression(C=ieg_baseline.C, solver='lbfgs', max_iter=5000)
    fitted.fit(x_train, y_train)
    scale = {}
    for col in columns:
        seen = np.array([float(r[col]) for r in train if r[col] != ''])
        scale[col] = [float(seen.mean()), float(seen.std()) or 1.0]
    seen_ids = sorted(read_ieg(ieg_csv))
    ids_text = ''.join(f'{i}\n' for i in seen_ids)
    model = {
        'plan': 'IEG-ПРАВИЛА-ЦЕЛЬ-ПРОТОКОЛ-ПРОЕКТ-20261108.md section 10, fixed 2026-11-09',
        'model': MODEL_NAME, 'columns': columns,
        'transforms': {c: CANDIDATES[c] for c in columns}, 'value_year_lag': LAG,
        'vocab': [list(v) for v in vocab], 'coef': [float(w) for w in fitted.coef_[0]],
        'intercept': float(fitted.intercept_[0]), 'scale': scale,
        'm0_train_rate': float(np.mean(y_train)), 'train_rows': len(train),
        'inputs': {'ieg_csv_sha256': ieg_sha, 'wdi_json_sha256': wdi_manifest['json_sha256'],
                   'projects_json_sha256': projects_manifest['json_sha256'],
                   'seen_project_ids': len(seen_ids),
                   'seen_project_ids_sha256': hashlib.sha256(ids_text.encode()).hexdigest()},
        'evaluation_rule': {'min_projects': MIN_PROJECTS, 'min_per_class': MIN_PER_CLASS,
                            'min_approval_fy': MIN_APPROVAL_FY,
                            'bootstrap': ieg_baseline.BOOTSTRAP, 'seed': ieg_baseline.SEED,
                            'bootstrap_unit': 'country'},
    }
    _, rows = ieg_baseline.read(Path(baseline_dir) / 'test-predictions.csv')
    if [r['source_project_id'] for r in rows] != [r['source_project_id'] for r in test]:
        raise Refusal('baseline_predictions_list_different_projects')
    reproduced = predict(model, test)
    gap = float(np.max(np.abs(reproduced - np.array([float(r[MODEL_NAME]) for r in rows]))))
    if gap > REPRODUCE_TOLERANCE:
        raise Refusal('frozen_model_does_not_reproduce_section_9')
    model['reproduction_max_abs_difference'] = gap
    out_dir.mkdir(parents=False)
    raw = (json.dumps(model, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
    (out_dir / 'model.json').write_bytes(raw)
    (out_dir / 'seen-project-ids.txt').write_text(ids_text, encoding='utf-8')
    return {'model_sha256': hashlib.sha256(raw).hexdigest(), 'columns': columns,
            'coef': model['coef'], 'intercept': model['intercept'],
            'm0_train_rate': model['m0_train_rate'], 'reproduction_max_abs_difference': gap,
            'seen_project_ids': len(seen_ids)}


def evaluate(model_dir, new_ieg_csv, projects_dir, wdi_dir, out_dir,
             expected_sha=FROZEN_MODEL_SHA256, n_boot=None):
    out_dir, model_dir = Path(out_dir), Path(model_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise Refusal('output_directory_exists')
    if not expected_sha or sha256(model_dir / 'model.json') != expected_sha:
        raise Refusal('model_is_not_the_frozen_one')
    model = json.loads((model_dir / 'model.json').read_text(encoding='utf-8'))
    ids_text = (model_dir / 'seen-project-ids.txt').read_text(encoding='utf-8')
    if hashlib.sha256(ids_text.encode()).hexdigest() != model['inputs']['seen_project_ids_sha256']:
        raise Refusal('seen_project_ids_changed')
    seen = set(ids_text.split())
    projects, pman = load_snapshot(projects_dir, 'projects.json')
    wdi, wman = load_snapshot(wdi_dir, 'wdi.json')
    if pman['json_sha256'] != model['inputs']['projects_json_sha256'] or \
            wman['json_sha256'] != model['inputs']['wdi_json_sha256']:
        raise Refusal('snapshot_is_not_the_frozen_one')
    country = country_of(projects)
    per_id = read_ieg(new_ieg_csv)
    reasons, rows = Counter(), []
    for ident in sorted(per_id):
        if ident in seen:
            reasons['seen_before_the_freeze'] += 1
            continue
        reason, record = project_verdict(per_id[ident])
        if reason:
            reasons[reason] += 1
            continue
        if record['approval_fy'] < MIN_APPROVAL_FY:
            reasons['approval_before_2014'] += 1
            continue
        if ident not in country:
            reasons['not_in_projects_snapshot'] += 1
            continue
        rows.append({'source_project_id': ident, 'y': record['outcome'],
                     'country': country[ident],
                     **features_for(country[ident], record['approval_fy'], wdi,
                                    model['columns'])})
    counts = {'new_projects': len(rows), 'positive': sum(r['y'] for r in rows),
              'negative': sum(1 - r['y'] for r in rows),
              'excluded_by_reason': dict(sorted(reasons.items())),
              'new_ieg_csv_sha256': sha256(new_ieg_csv)}
    if len(rows) < MIN_PROJECTS or min(counts['positive'], counts['negative']) < MIN_PER_CLASS:
        return {'evaluated': False, 'reason': 'not_enough_new_evaluations', **counts}
    y = np.array([r['y'] for r in rows])
    p = predict(model, rows)
    base = np.full(len(rows), model['m0_train_rate'])
    point = ieg_baseline.metrics(y, p, base)
    boot = {'auc': [], 'brier_skill_vs_M0': []}
    resamples = model['evaluation_rule']['bootstrap'] if n_boot is None else n_boot
    for idx in ieg_baseline.cluster_resamples([r['country'] for r in rows], resamples,
                                              model['evaluation_rule']['seed']):
        if len(set(y[idx])) < 2:
            continue
        m = ieg_baseline.metrics(y[idx], p[idx], base[idx])
        boot['auc'].append(m['auc'])
        boot['brier_skill_vs_M0'].append(m['brier_skill_vs_M0'])
    auc_ci = ieg_baseline.interval(boot['auc'])
    skill_ci = ieg_baseline.interval(boot['brier_skill_vs_M0'])
    report = {'evaluated': True, **counts,
              'model_sha256': expected_sha, 'countries': len({r['country'] for r in rows}),
              **{k: round(v, 4) for k, v in point.items()},
              'auc_ci95': auc_ci, 'brier_skill_ci95': skill_ci,
              'better_than_class_rate': ieg_baseline.decide(auc_ci, skill_ci),
              'note': 'the first run that passes the size rule is confirmatory; later runs are post hoc'}
    out_dir.mkdir(parents=False)
    with (out_dir / 'new-test-predictions.csv').open('w', newline='', encoding='utf-8') as s:
        writer = csv.writer(s)
        writer.writerow(['source_project_id', 'country', 'outcome', MODEL_NAME])
        for r, q in zip(rows, p):
            writer.writerow([r['source_project_id'], r['country'], r['y'], f'{q:.6f}'])
    (out_dir / 'frozen-evaluation-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    f = sub.add_parser('freeze')
    for name in ('features_dir', 'outcomes_dir', 'country_dir', 'baseline_dir',
                 'projects_dir', 'wdi_dir', 'ieg_csv', 'out_dir'):
        f.add_argument(name, type=Path)
    e = sub.add_parser('evaluate')
    for name in ('model_dir', 'new_ieg_csv', 'projects_dir', 'wdi_dir', 'out_dir'):
        e.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'freeze':
            result = freeze(args.features_dir, args.outcomes_dir, args.country_dir,
                            args.baseline_dir, args.projects_dir, args.wdi_dir,
                            args.ieg_csv, args.out_dir)
        else:
            result = evaluate(args.model_dir, args.new_ieg_csv, args.projects_dir,
                              args.wdi_dir, args.out_dir)
    except Refusal as exc:
        print(json.dumps({'passed': False, 'reason_code': str(exc)}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
