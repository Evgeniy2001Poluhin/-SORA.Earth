"""Approval-time features from a Projects API snapshot (wb_projects_fetch.py).

Plan: IEG-ПРАВИЛА-ЦЕЛЬ-ПРОТОКОЛ-ПРОЕКТ-20261108.md, section 8 (2026-11-09).

  log_commitment    log10(totalamt); totalamt <= 0 or missing -> empty
  prep_months       concept review -> board approval, in months
  appraisal_months  begin appraisal -> board approval, in months
  env_category      envassesmentcategorycode as given
A milestone dated after approval is empty, never clipped. No curr_* field, cost,
closing date, status, borrower or post-approval milestone is read.
Refuses: snapshot hash mismatch, a project missing from the snapshot, or an
approval date whose fiscal year differs from the IEG Approval FY.
"""
import argparse
from collections import Counter
import csv
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sys

from ieg_join import Refusal

COLUMNS = ['log_commitment', 'prep_months', 'appraisal_months', 'env_category']
READ = ('id', 'boardapprovaldate', 'totalamt', 'milestones', 'envassesmentcategorycode')


def date(value):
    try:
        return dt.date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def fiscal_year(day):
    return day.year + (1 if day.month >= 7 else 0)


def months(start, end):
    if start is None or start > end:
        return ''
    return f'{(end - start).days / 30.4375:.3f}'


def derive(project, approval_fy, counts):
    approved = date(project.get('boardapprovaldate'))
    if approved is None or str(fiscal_year(approved)) != approval_fy:
        raise Refusal('approval_date_disagrees_with_ieg_approval_fy')
    try:
        amount = float(project.get('totalamt'))
    except (TypeError, ValueError):
        amount = 0.0
    milestones = project.get('milestones') or [{}]
    stones = milestones[0] if isinstance(milestones, list) and milestones else {}
    out = {'log_commitment': f'{math.log10(amount):.6f}' if amount > 0 else '',
           'prep_months': months(date(stones.get('concpt_review_date')), approved),
           'appraisal_months': months(date(stones.get('begin_apprsl_date')), approved),
           'env_category': (project.get('envassesmentcategorycode') or '').strip()}
    for name, key in (('prep_months', 'concpt_review_date'),
                      ('appraisal_months', 'begin_apprsl_date')):
        started = date(stones.get(key))
        if started and started > approved:
            counts[f'{name}_dated_after_approval'] += 1
    return out


def run(features_dir, snapshot_dir, out_dir):
    out_dir, snapshot_dir = Path(out_dir), Path(snapshot_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise Refusal('output_directory_exists')
    try:
        manifest = json.loads((snapshot_dir / 'manifest.json').read_text())
        raw = (snapshot_dir / 'projects.json').read_bytes()
        projects = {p['id']: {k: p.get(k) for k in READ} for p in json.loads(raw)}
    except (OSError, ValueError, KeyError, TypeError):
        raise Refusal('snapshot_unreadable')
    if hashlib.sha256(raw).hexdigest() != manifest.get('json_sha256'):
        raise Refusal('snapshot_hash_mismatch')
    try:
        with open(Path(features_dir) / 'features.csv', newline='', encoding='utf-8') as s:
            rows = list(csv.DictReader(s, strict=True))
    except (OSError, csv.Error, UnicodeDecodeError):
        raise Refusal('features_csv_unreadable')
    counts, table = Counter(), []
    for row in rows:
        ident = row['source_project_id']
        if ident not in projects:
            raise Refusal('project_missing_from_snapshot')
        table.append({'source_project_id': ident, 'split': row['split'],
                      **derive(projects[ident], row['approval_fy'], counts)})
    fill = {s: {c: round(sum(1 for r in table if r['split'] == s and r[c] != '') /
                         max(1, sum(1 for r in table if r['split'] == s)), 3)
                for c in COLUMNS} for s in ('train', 'test')}
    report = {'inputs': {'snapshot_json_sha256': manifest['json_sha256'],
                         'snapshot_fetched_at_utc': manifest.get('fetched_at_utc'),
                         'rows': len(table)},
              'columns': COLUMNS, 'fill_rate_by_split': fill,
              'milestones_after_approval_set_empty': dict(counts),
              'outcome_column_included': False}
    out_dir.mkdir(parents=False)
    with (out_dir / 'projects-features.csv').open('w', newline='', encoding='utf-8') as s:
        writer = csv.DictWriter(s, ['source_project_id', 'split', *COLUMNS])
        writer.writeheader()
        writer.writerows(table)
    (out_dir / 'projects-feature-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('features_dir', type=Path)
    parser.add_argument('snapshot_dir', type=Path)
    parser.add_argument('out_dir', type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.features_dir, args.snapshot_dir, args.out_dir), indent=2))
    except Refusal as exc:
        print(json.dumps({'projects_features_passed': False, 'reason_code': str(exc)}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
