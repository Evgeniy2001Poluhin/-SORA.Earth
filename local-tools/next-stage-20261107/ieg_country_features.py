"""Country indicators for the year before approval (plan section 9, 2026-11-09).

Value year = Approval FY - 2 (calendar). Country = Projects API countrycode (ISO2).
Candidates are admitted by a rule applied BEFORE any outcome is seen: fill rate
>= 70% in train and in test, and the two differ by at most 10 percentage points.
Only admitted columns are written. Outcomes are not read.
"""
import argparse
import csv
import hashlib
import json
import math
from pathlib import Path
import sys

from ieg_join import Refusal

CANDIDATES = {   # output column -> (indicator, transform)
    'gdp_growth': ('NY.GDP.MKTP.KD.ZG', 'raw'),
    'log_gdp_pc': ('NY.GDP.PCAP.KD', 'log10'),
    'inflation_slog': ('FP.CPI.TOTL.ZG', 'signed_log10'),
    'wgi_gov_effectiveness': ('GOV_WGI_GE.EST', 'raw'),
    'cpia_irai': ('IQ.CPA.IRAI.XQ', 'raw'),
}
LAG = 2
MIN_FILL, MAX_GAP = 0.70, 0.10


def transform(value, how):
    if value is None:
        return ''
    if how == 'log10':
        return f'{math.log10(value):.6f}' if value > 0 else ''
    if how == 'signed_log10':
        return f'{math.copysign(math.log10(1 + abs(value)), value):.6f}'
    return f'{value:.6f}'


def admitted(fill):
    return {c: fill['train'][c] >= MIN_FILL and fill['test'][c] >= MIN_FILL and
            round(abs(fill['train'][c] - fill['test'][c]), 6) <= MAX_GAP for c in CANDIDATES}


def load_snapshot(directory, name):
    try:
        manifest = json.loads((Path(directory) / 'manifest.json').read_text())
        raw = (Path(directory) / name).read_bytes()
        data = json.loads(raw)
    except (OSError, ValueError):
        raise Refusal(f'{name}_unreadable')
    if hashlib.sha256(raw).hexdigest() != manifest.get('json_sha256'):
        raise Refusal(f'{name}_hash_mismatch')
    return data, manifest


def run(features_dir, projects_dir, wdi_dir, out_dir):
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise Refusal('output_directory_exists')
    projects, _ = load_snapshot(projects_dir, 'projects.json')
    wdi, wdi_manifest = load_snapshot(wdi_dir, 'wdi.json')
    if set(i for i, _ in CANDIDATES.values()) - set(wdi):
        raise Refusal('wdi_snapshot_missing_an_indicator')
    country = {}
    for p in projects:
        code = p.get('countrycode')
        country[p['id']] = (code[0] if isinstance(code, list) and code else code) or ''
    try:
        with open(Path(features_dir) / 'features.csv', newline='', encoding='utf-8') as s:
            rows = list(csv.DictReader(s, strict=True))
    except (OSError, csv.Error, UnicodeDecodeError):
        raise Refusal('features_csv_unreadable')
    table = []
    for row in rows:
        ident = row['source_project_id']
        if ident not in country:
            raise Refusal('project_missing_from_snapshot')
        if not row['approval_fy'].isdigit():
            raise Refusal('approval_fy_not_a_year')
        year = int(row['approval_fy']) - LAG
        out = {'source_project_id': ident, 'split': row['split']}
        for col, (code, how) in CANDIDATES.items():
            out[col] = transform(wdi[code].get(f'{country[ident]}|{year}'), how)
        table.append(out)
    fill = {s: {c: round(sum(1 for r in table if r['split'] == s and r[c] != '') /
                         max(1, sum(1 for r in table if r['split'] == s)), 3)
                for c in CANDIDATES} for s in ('train', 'test')}
    decision = admitted(fill)
    columns = [c for c in CANDIDATES if decision[c]]
    report = {'inputs': {'wdi_json_sha256': wdi_manifest['json_sha256'], 'rows': len(table)},
              'value_year': f'Approval FY - {LAG} (calendar year)',
              'rule': f'fill >= {MIN_FILL} in both splits and |train - test| <= {MAX_GAP}',
              'fill_rate_by_split': fill, 'admitted': columns,
              'rejected': [c for c in CANDIDATES if not decision[c]],
              'outcome_read': False}
    out_dir.mkdir(parents=False)
    with (out_dir / 'country-features.csv').open('w', newline='', encoding='utf-8') as s:
        writer = csv.DictWriter(s, ['source_project_id', 'split', *columns],
                                extrasaction='ignore')
        writer.writeheader()
        writer.writerows(table)
    (out_dir / 'country-feature-report.json').write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ('features_dir', 'projects_dir', 'wdi_dir', 'out_dir'):
        parser.add_argument(name, type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.features_dir, args.projects_dir, args.wdi_dir, args.out_dir),
                         indent=2))
    except Refusal as exc:
        print(json.dumps({'country_features_passed': False, 'reason_code': str(exc)}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
