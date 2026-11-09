"""Build an outcomes.py table from the IEG export under the rules approved 2026-11-08.

Rules (IEG-ПРАВИЛА-ЦЕЛЬ-ПРОТОКОЛ-ПРОЕКТ-20261108.md):
  P1  repeated IEG rows: same raw rating -> one project, latest Evaluation FY;
      different ratings -> excluded.
  P2  projects sharing a catalog document form a cluster; the whole cluster goes to
      one side or is excluded; a cluster with differing ratings is excluded.
  P3  only ICRR evaluations; any other evaluation type excludes the project (and its cluster).
  P4  no document is chosen; docdt is not used.
  P5  Not Rated / missing / unrecognized are not outcomes.
Target A: Moderately Satisfactory and above -> 1; Moderately Unsatisfactory and below -> 0.
Protocol: test = Approval FY >= T; train = Evaluation FY <= T-1; the rest unused.

Timestamps are FISCAL-YEAR BOUNDS, not real dates: the World Bank fiscal year N ends
on 30 June of N, so prediction_at = end of Approval FY (approval happened no later)
and outcome_observed_at = end of Evaluation FY. features_available_at is set equal to
prediction_at as a bound: no feature may be newer than that. The feature source is
NOT decided and no features are produced here. No training, no validation.
"""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import sys

from ieg_join import (IEG_MAX_BYTES, Refusal, decode, load_catalog, load_ieg, normalize,
                      outcome_category, read_limited, rows_of)
from ieg_review import catalog_documents, clusters
from outcomes import FIELDS, validate

SOURCE = 'ieg_project_ratings'
SOURCE_URL = ('https://datacatalog.worldbank.org/search/dataset/0066221/'
              'ieg-world-bank-project-performance-ratings')
DEFINITION = 'IEG Outcome >= Moderately Satisfactory (target A, approved 2026-11-08)'
POSITIVE = {'highly satisfactory', 'satisfactory', 'moderately satisfactory'}
NEGATIVE = {'moderately unsatisfactory', 'unsatisfactory', 'highly unsatisfactory'}
REQUIRED = ('projectid', 'outcome', 'evaluationtype', 'approvalfy', 'evaluationfy')


def fy_end(year):
    return f'{year:04d}-06-30T23:59:59+00:00'


def read_ieg(path):
    headers, rows = rows_of(decode(read_limited(path, IEG_MAX_BYTES, 'ieg_csv'), 'ieg_csv'),
                            'ieg_csv')
    aliases = [normalize(h) for h in headers]
    if any(aliases.count(a) != 1 for a in REQUIRED):
        raise Refusal('ieg_csv_missing_or_ambiguous_protocol_columns')
    key = {a: headers[aliases.index(a)] for a in REQUIRED}
    per_id = defaultdict(list)
    for row in rows:
        ident = row[key['projectid']].strip().upper()
        if not ident:
            continue

        def year(name):
            value = row[key[name]].strip()
            return int(value) if value.isdigit() else None
        per_id[ident].append({'outcome': outcome_category(row[key['outcome']]),
                              'type': row[key['evaluationtype']].strip().upper(),
                              'approval_fy': year('approvalfy'),
                              'evaluation_fy': year('evaluationfy')})
    return per_id


def project_verdict(rows):
    """(reason or None, project record) for one project ID under P1, P3, P5."""
    if any(r['type'] != 'ICRR' for r in rows):
        return 'P3_not_icrr', None
    if len({r['outcome'] for r in rows}) > 1:
        return 'P1_repeated_rows_disagree', None
    outcome = rows[0]['outcome']
    if outcome not in POSITIVE | NEGATIVE:
        return 'P5_not_an_outcome', None
    approvals = {r['approval_fy'] for r in rows}
    evaluations = [r['evaluation_fy'] for r in rows]
    if None in approvals or None in evaluations or len(approvals) != 1:
        return 'fiscal_year_missing_or_inconsistent', None
    approval, evaluation = approvals.pop(), max(evaluations)   # P1: latest Evaluation FY
    if evaluation <= approval:
        return 'evaluation_not_after_approval', None
    return None, {'outcome': 1 if outcome in POSITIVE else 0,
                  'approval_fy': approval, 'evaluation_fy': evaluation,
                  'repeated_rows': len(rows) > 1}


def build(per_id, docs, threshold):
    catalog_ids = {p for d in docs for p in d['projects']}
    cluster_of = clusters(docs)
    reasons, records, members = Counter(), {}, defaultdict(list)
    for ident in sorted(per_id):
        if ident not in catalog_ids:
            reasons['not_in_catalog_cluster_unknown'] += 1
            continue
        reason, record = project_verdict(per_id[ident])
        if reason:
            reasons[reason] += 1
        else:
            records[ident] = record
    for ident in catalog_ids:
        members[cluster_of[ident][0]].append(ident)
    rows, units = [], []
    for cid, group in sorted(members.items()):
        kept = [p for p in group if p in records]
        if not kept:
            continue
        if len(group) > 1:                                     # P2
            if len(kept) != len(group):
                reasons['P2_cluster_member_excluded'] += len(kept)
                continue
            if len({records[p]['outcome'] for p in kept}) > 1:
                reasons['P2_cluster_ratings_disagree'] += len(kept)
                continue
        train = all(records[p]['evaluation_fy'] <= threshold - 1 for p in kept)
        test = all(records[p]['approval_fy'] >= threshold for p in kept)
        if train == test:      # neither side, or (impossible for valid FYs) both
            reasons['protocol_gap' if len(kept) == 1 else 'P2_cluster_straddles_threshold'] += len(kept)
            continue
        split = 'train' if train else 'test'
        for p in sorted(kept):
            rec = records[p]
            rows.append({'source': SOURCE, 'source_project_id': p,
                         'prediction_at': fy_end(rec['approval_fy']),
                         'features_available_at': fy_end(rec['approval_fy']),
                         'outcome': str(rec['outcome']),
                         'outcome_observed_at': fy_end(rec['evaluation_fy']),
                         'source_url': SOURCE_URL, 'outcome_definition': DEFINITION,
                         'split': split})
            units.append({'source_project_id': p, 'cluster_id': cid, 'cluster_size': len(kept),
                          'repeated_ieg_rows': 'yes' if rec['repeated_rows'] else 'no'})
    return rows, units, reasons


def run(ieg_csv, catalog_dir, out_dir, threshold):
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise Refusal('output_directory_exists')
    catalog = load_catalog(catalog_dir)
    ieg = load_ieg(ieg_csv)
    per_id = read_ieg(ieg_csv)
    rows, units, reasons = build(per_id, catalog_documents(catalog_dir), threshold)
    check = validate([dict(r) for r in rows])
    by_split = Counter((r['split'], r['outcome']) for r in rows)
    report = {
        'inputs': {'ieg_csv_file': ieg['summary']['file'], 'ieg_csv_sha256': ieg['summary']['sha256'],
                   'catalog_sha256': catalog['summary']['sha256']},
        'rules': 'P1-P5, target A, T approved by the owner 2026-11-08',
        'threshold_fy': threshold,
        'ieg_project_ids': len(per_id),
        'included_projects': len(rows),
        'included_clusters_of_two_or_more': len({u['cluster_id'] for u in units
                                                 if u['cluster_size'] > 1}),
        'excluded_projects_by_reason': dict(sorted(reasons.items())),
        'split_outcome_counts': {f'{s}_{o}': n for (s, o), n in sorted(by_split.items())},
        'outcomes_py_intake_passed': check['intake_passed'],
        'outcomes_py_errors': check['errors'][:20],
        'timestamps': 'fiscal-year-end bounds (30 June), not real dates',
        'features_produced': False,
        'feature_source_decided': False,
        'training_performed': False,
        'validation_performed': False,
    }
    if not check['intake_passed']:
        raise Refusal('outcomes_py_intake_failed')
    try:
        out_dir.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        raise Refusal('output_directory_exists')
    except OSError:
        raise Refusal('output_directory_not_creatable')
    with (out_dir / 'outcomes.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=list(FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    with (out_dir / 'units.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['source_project_id', 'cluster_id',
                                                    'cluster_size', 'repeated_ieg_rows'])
        writer.writeheader()
        writer.writerows(units)
    (out_dir / 'build-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('ieg_csv', type=Path)
    parser.add_argument('catalog_dir', type=Path)
    parser.add_argument('out_dir', type=Path)
    parser.add_argument('--threshold-fy', type=int, required=True,
                        help='T of the approved protocol (2014)')
    args = parser.parse_args(argv)
    try:
        report = run(args.ieg_csv, args.catalog_dir, args.out_dir, args.threshold_fy)
    except Refusal as exc:
        print(json.dumps({'build_passed': False, 'reason_code': str(exc)}))
        return 1
    print(json.dumps({'build_passed': True, **{k: report[k] for k in (
        'included_projects', 'included_clusters_of_two_or_more',
        'excluded_projects_by_reason', 'split_outcome_counts', 'outcomes_py_intake_passed')}},
        ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
