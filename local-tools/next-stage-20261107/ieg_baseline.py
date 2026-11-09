"""Baseline models on the IEG-only features, under the plan fixed before the run.

Plan: IEG-ПРАВИЛА-ЦЕЛЬ-ПРОТОКОЛ-ПРОЕКТ-20261108.md, section 7 (2026-11-09).

  M0  constant = share of successes in train
  M1  region + practice_group
  M2  country, region, agreement_type, lending_instrument_type, practice_group,
      global_practice
  approval_fy is used by no model (train and test never share a value).
  One-hot fitted on train; empty and unseen-in-train values give a zero vector.
  Logistic regression, C=1.0, no tuning, no weights. Test labels never reach fit.
  Primary metric: test ROC AUC, 95% CI by bootstrap over P2 clusters (2000, seed
  20261109). Also Brier, Brier skill vs M0, log loss, AUC(M2)-AUC(M1), calibration
  in the large. Decision: better than the class rate only if the lower CI bound of
  AUC > 0.5 AND of Brier skill > 0.

Inputs are the outputs of ieg_outcomes.py and ieg_features.py; nothing is written
anywhere but out_dir, which must not exist. Needs numpy and scikit-learn.
"""
import argparse
import csv
import json
from pathlib import Path
import sys

import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, log_loss, roc_auc_score

from ieg_join import Refusal

MODELS = {
    'M1_region_practice': ['region', 'practice_group'],
    'M2_all_ieg_fields': ['country', 'region', 'agreement_type', 'lending_instrument_type',
                          'practice_group', 'global_practice'],
}
PROJECTS = ['log_commitment', 'prep_months', 'appraisal_months', 'env_category']
EXTRA_MODELS = {   # plan section 8; only with a projects-features directory
    'M3_region_practice_projects': ['region', 'practice_group', *PROJECTS],
    'M4_projects_only': PROJECTS,
}
COUNTRY = ['gdp_growth', 'log_gdp_pc', 'inflation_slog', 'wgi_gov_effectiveness',
           'cpia_irai']   # candidates of plan section 9; the file says which were admitted
NUMERIC = {'log_commitment', 'prep_months', 'appraisal_months', *COUNTRY}
NOT_USED = ['approval_fy']
C = 1.0
BOOTSTRAP = 2000
SEED = 20261109


def read(path):
    try:
        with open(path, newline='', encoding='utf-8') as stream:
            reader = csv.DictReader(stream, strict=True)
            return reader.fieldnames or [], list(reader)
    except (OSError, UnicodeDecodeError, csv.Error):
        raise Refusal(f'unreadable_{Path(path).name}')


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        raise Refusal(f'unreadable_{Path(path).name}')


def merge(table, path, columns, code):
    """Copy columns from an extra feature file listing exactly the table's projects."""
    head, rows = read(path)
    if not {'source_project_id', 'split', *columns} <= set(head):
        raise Refusal(f'{code}_csv_missing_columns')
    if [(r['source_project_id'], r['split']) for r in rows] != \
            [(r['source_project_id'], r['split']) for r in table]:
        raise Refusal(f'{code}_list_different_projects_or_splits')
    for row, extra in zip(table, rows):
        for col in columns:
            if col in NUMERIC and extra[col] != '':
                try:
                    float(extra[col])
                except ValueError:
                    raise Refusal(f'{code}_feature_not_numeric')
            row[col] = extra[col]


def country_columns(country_dir):
    head, _ = read(Path(country_dir) / 'country-features.csv')
    columns = [c for c in head if c not in ('source_project_id', 'split')]
    if not columns or set(columns) - set(COUNTRY):
        raise Refusal('country_features_columns_not_admissible')
    return columns


def load(features_dir, outcomes_dir, extra_dir=None, country_dir=None):
    features_dir, outcomes_dir = Path(features_dir), Path(outcomes_dir)
    build = read_json(outcomes_dir / 'build-report.json')
    freport = read_json(features_dir / 'feature-report.json')
    if (build.get('inputs') or {}).get('ieg_csv_sha256') != \
            (freport.get('inputs') or {}).get('ieg_csv_sha256'):
        raise Refusal('features_and_outcomes_come_from_different_ieg_files')
    fhead, frows = read(features_dir / 'features.csv')
    _, orows = read(outcomes_dir / 'outcomes.csv')
    _, urows = read(outcomes_dir / 'units.csv')
    needed = {f for cols in MODELS.values() for f in cols}
    if not {'source_project_id', 'split'} | needed <= set(fhead):
        raise Refusal('features_csv_missing_columns')
    outcome = {r['source_project_id']: r for r in orows}
    cluster = {r['source_project_id']: r['cluster_id'] for r in urows}
    if len(outcome) != len(orows) or [r['source_project_id'] for r in frows] != \
            [r['source_project_id'] for r in orows]:
        raise Refusal('features_and_outcomes_list_different_projects')
    table = []
    for row in frows:
        ident = row['source_project_id']
        if row['split'] != outcome[ident]['split'] or row['split'] not in ('train', 'test'):
            raise Refusal('features_and_outcomes_disagree_on_split')
        if outcome[ident]['outcome'] not in ('0', '1'):
            raise Refusal('outcome_not_binary')
        if ident not in cluster:
            raise Refusal('project_missing_from_units_csv')
        table.append({**row, 'y': int(outcome[ident]['outcome']), 'cluster': cluster[ident]})
    if not any(r['split'] == 'train' for r in table) or \
            not any(r['split'] == 'test' for r in table):
        raise Refusal('train_or_test_is_empty')
    if extra_dir is not None:
        merge(table, Path(extra_dir) / 'projects-features.csv', PROJECTS, 'projects_features')
    if country_dir is not None:
        merge(table, Path(country_dir) / 'country-features.csv', country_columns(country_dir),
              'country_features')
    return table, build, freport


def encode(train, test, columns):
    """Categorical: one-hot over train values. Numeric: standardized by train
    mean/std, missing -> 0 plus a missing flag. Nothing is fitted on test."""
    vocab, scale = [], {}
    for col in columns:
        if col in NUMERIC:
            seen = np.array([float(r[col]) for r in train if r[col] != ''])
            scale[col] = (float(seen.mean()), float(seen.std()) or 1.0) if len(seen) else (0.0, 1.0)
            vocab += [(col, 'value'), (col, 'missing')]
        else:
            vocab += [(col, v) for v in sorted({r[col] for r in train if r[col] != ''})]
    index = {k: i for i, k in enumerate(vocab)}

    def matrix(rows):
        x = np.zeros((len(rows), len(vocab)))
        for i, r in enumerate(rows):
            for col in columns:
                if col in NUMERIC:
                    if r[col] == '':
                        x[i, index[(col, 'missing')]] = 1.0
                    else:
                        mean, std = scale[col]
                        x[i, index[(col, 'value')]] = (float(r[col]) - mean) / std
                    continue
                j = index.get((col, r[col]))
                if j is not None:
                    x[i, j] = 1.0
        return x
    return matrix(train), matrix(test), vocab


def fit_predict(train, test, columns):
    x_train, x_test, vocab = encode(train, test, columns)
    y_train = np.array([r['y'] for r in train])
    if len(set(y_train)) < 2:
        raise Refusal('train_has_one_class')
    model = LogisticRegression(C=C, solver='lbfgs', max_iter=5000)
    model.fit(x_train, y_train)
    coef = sorted(zip(vocab, model.coef_[0]), key=lambda t: -abs(t[1]))[:10]
    return model.predict_proba(x_test)[:, 1], [
        {'feature': c, 'value': v, 'coefficient': round(float(w), 4)} for (c, v), w in coef]


def cluster_resamples(clusters, n, seed):
    """Index arrays; each resample draws whole clusters with replacement."""
    groups = {}
    for i, c in enumerate(clusters):
        groups.setdefault(c, []).append(i)
    members = list(groups.values())
    rng = np.random.default_rng(seed)
    for _ in range(n):
        picks = rng.integers(0, len(members), len(members))
        yield np.concatenate([members[k] for k in picks])


def metrics(y, p, base):
    brier, brier0 = brier_score_loss(y, p), brier_score_loss(y, base)
    return {'auc': roc_auc_score(y, p) if len(set(y)) == 2 else None,
            'brier': brier, 'brier_skill_vs_M0': 1 - brier / brier0,
            'log_loss': log_loss(y, np.clip(p, 1e-12, 1 - 1e-12), labels=[0, 1]),
            'mean_prediction': float(np.mean(p)), 'observed_rate': float(np.mean(y))}


def interval(values):
    values = [v for v in values if v is not None]
    lo, hi = np.percentile(values, [2.5, 97.5])
    return [round(float(lo), 4), round(float(hi), 4)]


def decide(auc_ci, brier_skill_ci):
    """Plan section 7: both lower bounds must clear their chance level."""
    return bool(auc_ci[0] > 0.5 and brier_skill_ci[0] > 0)


def evaluate(table, n_boot=BOOTSTRAP, seed=SEED, models=None):
    models = MODELS if models is None else models
    train = [r for r in table if r['split'] == 'train']
    test = [r for r in table if r['split'] == 'test']
    y = np.array([r['y'] for r in test])
    rate = float(np.mean([r['y'] for r in train]))
    base = np.full(len(test), rate)
    preds, coefs = {'M0_train_rate': base}, {}
    for name, cols in models.items():
        preds[name], coefs[name] = fit_predict(train, test, cols)
    point = {name: metrics(y, p, base) for name, p in preds.items()}
    boot = {name: {'auc': [], 'brier_skill_vs_M0': []} for name in models}
    pairs = [(a, 'M1_region_practice') for a in models
             if a != 'M1_region_practice' and not a.endswith('_only')]
    diff, degenerate = {a: [] for a, _ in pairs}, 0
    for idx in cluster_resamples([r['cluster'] for r in test], n_boot, seed):
        yb = y[idx]
        if len(set(yb)) < 2:
            degenerate += 1
            continue
        aucs = {}
        for name in models:
            m = metrics(yb, preds[name][idx], base[idx])
            aucs[name] = m['auc']
            boot[name]['auc'].append(m['auc'])
            boot[name]['brier_skill_vs_M0'].append(m['brier_skill_vs_M0'])
        for a, b in pairs:
            diff[a].append(aucs[a] - aucs[b])
    result = {}
    for name, m in point.items():
        entry = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()}
        if name in models:
            entry['auc_ci95'] = interval(boot[name]['auc'])
            entry['brier_skill_ci95'] = interval(boot[name]['brier_skill_vs_M0'])
            entry['better_than_class_rate'] = decide(entry['auc_ci95'], entry['brier_skill_ci95'])
            entry['top_coefficients'] = coefs[name]
        result[name] = entry
    return {
        'train_rows': len(train), 'test_rows': len(test),
        'train_positive_rate': round(rate, 4), 'test_positive_rate': round(float(np.mean(y)), 4),
        'test_clusters': len({r['cluster'] for r in test}),
        'models': result,
        **{f'auc_difference_{a.split("_")[0]}_minus_M1': {
            'point': round(point[a]['auc'] - point[b]['auc'], 4), 'ci95': interval(diff[a])}
           for a, b in pairs},
        'bootstrap': {'resamples': n_boot, 'seed': seed, 'unit': 'P2 cluster',
                      'skipped_one_class': degenerate},
    }, test, preds


def run(features_dir, outcomes_dir, out_dir, n_boot=BOOTSTRAP, extra_dir=None,
        country_dir=None):
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise Refusal('output_directory_exists')
    table, build, freport = load(features_dir, outcomes_dir, extra_dir, country_dir)
    models = {**MODELS, **(EXTRA_MODELS if extra_dir is not None else {})}
    if country_dir is not None:     # plan section 9
        admitted = country_columns(country_dir)
        models['M5_region_practice_country'] = ['region', 'practice_group', *admitted]
        models['M6_country_only'] = admitted
    summary, test, preds = evaluate(table, n_boot, models=models)
    report = {
        'plan': 'IEG-ПРАВИЛА-ЦЕЛЬ-ПРОТОКОЛ-ПРОЕКТ-20261108.md section 7'
                + (' and section 8' if extra_dir is not None else '')
                + (' and section 9' if country_dir is not None else '') + ', fixed 2026-11-09',
        'inputs': {'ieg_csv_sha256': build['inputs']['ieg_csv_sha256'],
                   'threshold_fy': build.get('threshold_fy')},
        'target': 'IEG Outcome >= Moderately Satisfactory (target A); not an ESG score',
        'features_not_used': NOT_USED, 'logistic_C': C,
        **summary,
        'training_performed': True,
        'test_labels_used_in_fit': False,
    }
    try:
        out_dir.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        raise Refusal('output_directory_exists')
    except OSError:
        raise Refusal('output_directory_not_creatable')
    with (out_dir / 'test-predictions.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.writer(stream)
        writer.writerow(['source_project_id', 'cluster_id', 'outcome', *preds])
        for i, r in enumerate(test):
            writer.writerow([r['source_project_id'], r['cluster'], r['y'],
                             *(f'{p[i]:.6f}' for p in preds.values())])
    (out_dir / 'baseline-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('features_dir', type=Path)
    parser.add_argument('outcomes_dir', type=Path)
    parser.add_argument('out_dir', type=Path)
    parser.add_argument('--projects-features', type=Path, default=None,
                        help='output of ieg_projects_features.py; adds M3 and M4 (plan section 8)')
    parser.add_argument('--country-features', type=Path, default=None,
                        help='output of ieg_country_features.py; adds M5 and M6 (plan section 9)')
    args = parser.parse_args(argv)
    try:
        report = run(args.features_dir, args.outcomes_dir, args.out_dir,
                     extra_dir=args.projects_features, country_dir=args.country_features)
    except Refusal as exc:
        print(json.dumps({'baseline_passed': False, 'reason_code': str(exc)}))
        return 1
    print(json.dumps({k: report[k] for k in (
        'train_rows', 'test_rows', 'train_positive_rate', 'test_positive_rate',
        'test_clusters', 'bootstrap')} | {
        k: v for k, v in report.items() if k.startswith('auc_difference_')} | {
        'models': {n: {k: v for k, v in m.items() if k != 'top_coefficients'}
                   for n, m in report['models'].items()}}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
