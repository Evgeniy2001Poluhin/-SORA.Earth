"""Attach approval-time IEG fields to the outcomes table built by ieg_outcomes.py.

Owner decision 2026-11-08: features come only from the IEG export. Every value in
the export is a snapshot as of its "As of Date", so a column is admitted only if its
value is fixed at approval (or is a recoding of something fixed then):

  admitted     Country / Economy, WB Region (a recoding of the country), Agreement
               Type, Lending Instrument Type, Approval FY; Practice Group and Global
               Practice (a later taxonomy of the project's fixed sector -- caveat)
  excluded     Country / Economy Lending Group, FCS Status, FCS Lending Group:
               time-varying country status measured at the snapshot, i.e. future
               information relative to approval
  forbidden    anything rated or known at evaluation (Outcome, Quality at Entry,
               Quality of Supervision, Bank Performance, M&E Quality, Final Closing
               FY, Evaluation FY, Evaluation Type, As of Date) and the free-text name

Values are written raw (no encoding, no imputation). No training, no validation.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import sys

from ieg_join import IEG_MAX_BYTES, Refusal, decode, normalize, read_limited, rows_of

ADMITTED = {  # normalized header -> output column
    'countryeconomy': 'country', 'wbregion': 'region', 'agreementtype': 'agreement_type',
    'lendinginstrumenttype': 'lending_instrument_type', 'approvalfy': 'approval_fy',
    'practicegroup': 'practice_group', 'globalpractice': 'global_practice'}
EXCLUDED_SNAPSHOT = ('countryeconomylendinggroup', 'countryeconomyfcsstatus',
                     'countryeconomyfcslendinggroup')
FORBIDDEN = ('outcome', 'qualityatentry', 'qualityofsupervision', 'bankperformance',
             'mequality', 'finalclosingfy', 'evaluationfy', 'evaluationtype', 'asofdate',
             'projectname')
FEATURES = list(ADMITTED.values())
assert not set(ADMITTED) & set(FORBIDDEN + EXCLUDED_SNAPSHOT)


def read_csv(path, code, limit=IEG_MAX_BYTES):
    raw = read_limited(path, limit, code)
    return raw, rows_of(decode(raw, code), code)


def run(ieg_csv, outcomes_dir, out_dir):
    out_dir, outcomes_dir = Path(out_dir), Path(outcomes_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise Refusal('output_directory_exists')
    try:
        build = json.loads(read_limited(outcomes_dir / 'build-report.json', 1 << 20, 'build_report'))
    except ValueError:
        raise Refusal('build_report_invalid_json')
    raw, (headers, rows) = read_csv(ieg_csv, 'ieg_csv')
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(build, dict) or (build.get('inputs') or {}).get('ieg_csv_sha256') != digest:
        raise Refusal('ieg_csv_is_not_the_source_of_the_outcomes')
    aliases = [normalize(h) for h in headers]
    needed = ['projectid', *ADMITTED]
    if any(aliases.count(a) != 1 for a in needed):
        raise Refusal('ieg_csv_missing_or_ambiguous_feature_columns')
    key = {a: headers[aliases.index(a)] for a in needed}

    values = {}
    for row in rows:
        ident = row[key['projectid']].strip().upper()
        if not ident:
            continue
        record = tuple(' '.join(row[key[a]].split()) for a in ADMITTED)
        if values.setdefault(ident, record) != record:
            raise Refusal('repeated_project_rows_disagree_on_features')

    _, (out_headers, outcome_rows) = read_csv(outcomes_dir / 'outcomes.csv', 'outcomes_csv',
                                              64 << 20)
    if not {'source_project_id', 'split'} <= set(out_headers):
        raise Refusal('outcomes_csv_missing_columns')
    table = []
    for row in outcome_rows:
        ident = row['source_project_id']
        if ident not in values:
            raise Refusal('outcome_project_missing_from_ieg_csv')
        table.append({'source_project_id': ident, 'split': row['split'],
                      **dict(zip(FEATURES, values[ident]))})
    if len(table) != build.get('included_projects'):
        raise Refusal('outcomes_csv_does_not_match_build_report')

    profile = {}
    for name in FEATURES:
        train = Counter(r[name] for r in table if r['split'] == 'train')
        test = Counter(r[name] for r in table if r['split'] == 'test')
        profile[name] = {
            'distinct_train': len(train), 'distinct_test': len(test),
            'empty_values': train[''] + test[''],
            'test_rows_with_value_unseen_in_train': sum(n for v, n in test.items()
                                                        if v not in train)}
    report = {
        'inputs': {'ieg_csv_sha256': digest, 'outcomes_rows': len(table)},
        'features': FEATURES,
        'excluded_snapshot_columns': [headers[aliases.index(a)] for a in EXCLUDED_SNAPSHOT
                                      if a in aliases],
        'forbidden_columns_present_in_source': [headers[aliases.index(a)] for a in FORBIDDEN
                                                if a in aliases],
        'profile': profile,
        'values_as_of': 'snapshot of the export (As of Date), not the approval date',
        'caveat': 'practice_group and global_practice are a later taxonomy applied '
                  'retroactively; region names follow the current regional structure',
        'outcome_column_included': False,
        'training_performed': False,
        'validation_performed': False,
    }
    try:
        out_dir.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        raise Refusal('output_directory_exists')
    except OSError:
        raise Refusal('output_directory_not_creatable')
    with (out_dir / 'features.csv').open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['source_project_id', 'split', *FEATURES])
        writer.writeheader()
        writer.writerows(table)
    (out_dir / 'feature-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('ieg_csv', type=Path)
    parser.add_argument('outcomes_dir', type=Path)
    parser.add_argument('out_dir', type=Path)
    args = parser.parse_args(argv)
    try:
        report = run(args.ieg_csv, args.outcomes_dir, args.out_dir)
    except Refusal as exc:
        print(json.dumps({'features_passed': False, 'reason_code': str(exc)}))
        return 1
    print(json.dumps({'features_passed': True, 'rows': report['inputs']['outcomes_rows'],
                      'excluded_snapshot_columns': report['excluded_snapshot_columns'],
                      'profile': report['profile']}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
