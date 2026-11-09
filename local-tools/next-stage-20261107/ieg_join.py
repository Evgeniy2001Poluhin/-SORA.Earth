"""Aggregate coverage of an operator-downloaded IEG CSV against the saved WB document catalog.

Offline only: no network, no labels, no training, no choice between duplicate rows.
Writes coverage-report.json and ambiguous-groups.csv (IDs and counts only) into a
NEW output directory; refuses if it already exists.
"""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys

from profile_ieg import MAX_BYTES as IEG_MAX_BYTES, RATINGS, normalize

CATALOG_MAX_BYTES = 64 * 1024 * 1024
PROJECT_ID = re.compile(r'P\d{6}')
SHA256 = re.compile(r'[0-9a-f]{64}')
CATALOG_REQUIRED = ('id', 'projectid')
IEG_DATE_ALIASES = {'asofdate': 'as_of_date', 'evaluationfy': 'evaluation_fy',
                    'approvalfy': 'approval_fy', 'finalclosingfy': 'final_closing_fy'}
REPORT_NAME = 'coverage-report.json'
GROUPS_NAME = 'ambiguous-groups.csv'


class Refusal(Exception):
    """A reason code; the run stops and writes nothing."""


def read_limited(path, limit, empty_code):
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(limit + 1)
    except OSError:
        raise Refusal(f'{empty_code}_unreadable')
    if len(raw) > limit:
        raise Refusal(f'{empty_code}_too_large')
    if not raw.strip():
        raise Refusal(f'{empty_code}_empty')
    return raw


def decode(raw, code):
    try:
        return raw.decode('utf-8-sig')
    except UnicodeDecodeError:
        raise Refusal(f'{code}_not_utf8')


def rows_of(text, code):
    """Yield (line_number, row); refuse rows with too many or too few fields."""
    # strict=True: an unclosed or stray quote is csv.Error, not a silently merged field.
    reader = csv.DictReader(io.StringIO(text, newline=''), strict=True)
    try:
        headers = reader.fieldnames or []
        if not headers:
            raise Refusal(f'{code}_no_header')
        rows = []
        for row in reader:
            if None in row or any(v is None for v in row.values()):
                raise Refusal(f'{code}_malformed_row')
            rows.append(row)
    except csv.Error:
        raise Refusal(f'{code}_malformed_csv')
    return headers, rows


def load_catalog(directory):
    directory = Path(directory)
    try:
        manifest = json.loads(read_limited(directory / 'manifest.json', 1024 * 1024, 'manifest'))
    except (ValueError, UnicodeDecodeError):
        raise Refusal('manifest_invalid_json')
    if not isinstance(manifest, dict):
        raise Refusal('manifest_not_object')

    def integer(key):
        value = manifest.get(key)
        if type(value) is not int or value < 0:
            raise Refusal(f'manifest_bad_{key}')
        return value
    documents, with_project_id = integer('documents'), integer('with_project_id')
    digest = manifest.get('csv_sha256')
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        raise Refusal('manifest_bad_csv_sha256')
    if manifest.get('contains_outcome_ratings') is not False:
        raise Refusal('manifest_must_declare_no_outcome_ratings')

    raw = read_limited(directory / 'documents.csv', CATALOG_MAX_BYTES, 'catalog')
    actual = hashlib.sha256(raw).hexdigest()
    if actual != digest:
        raise Refusal('catalog_sha256_mismatch')
    headers, rows = rows_of(decode(raw, 'catalog'), 'catalog')
    if len(set(headers)) != len(headers) or any(headers.count(k) != 1 for k in CATALOG_REQUIRED):
        raise Refusal('catalog_missing_or_ambiguous_columns')
    if len(rows) != documents:
        raise Refusal('catalog_document_count_mismatch')
    doc_ids = [r['id'].strip() for r in rows]
    if any(not d for d in doc_ids):
        raise Refusal('catalog_empty_document_id')
    if len(set(doc_ids)) != len(doc_ids):
        raise Refusal('catalog_duplicate_document_id')

    docs_per_project, shared_doc_projects = Counter(), set()
    empty, multi_project_docs, nonstandard = 0, 0, 0
    for row in rows:
        field = row['projectid'].strip()
        if not field:
            empty += 1
            continue
        parts = [p.strip().upper() for p in field.split(',')]
        if any(not p for p in parts):
            raise Refusal('catalog_empty_project_id_in_list')
        parts = sorted(set(parts))
        nonstandard += sum(not PROJECT_ID.fullmatch(p) for p in parts)
        if len(parts) > 1:
            multi_project_docs += 1
            shared_doc_projects.update(parts)
        docs_per_project.update(parts)
    if documents - empty != with_project_id:
        raise Refusal('catalog_with_project_id_mismatch')
    return {
        'docs_per_project': docs_per_project,
        'shared_doc_projects': shared_doc_projects,
        'summary': {
            'file': 'documents.csv', 'sha256': actual, 'manifest_sha256_verified': True,
            'documents': documents, 'unique_document_ids': len(set(doc_ids)),
            'documents_without_project_id': empty,
            'documents_listing_several_project_ids': multi_project_docs,
            'distinct_project_ids_after_splitting_lists': len(docs_per_project),
            'nonstandard_project_ids': nonstandard,
            'manifest_sha256': hashlib.sha256(
                (directory / 'manifest.json').read_bytes()).hexdigest(),
        },
    }


def outcome_category(value):
    rating = ' '.join(value.casefold().split())
    if rating in RATINGS:
        return rating
    if rating == 'not rated':
        return 'not rated'
    return 'missing' if not rating else 'unrecognized'


def load_ieg(path):
    raw = read_limited(path, IEG_MAX_BYTES, 'ieg_csv')
    headers, rows = rows_of(decode(raw, 'ieg_csv'), 'ieg_csv')
    aliases = [normalize(h) for h in headers]
    if (len(set(aliases)) != len(aliases)
            or aliases.count('projectid') != 1 or aliases.count('outcome') != 1):
        raise Refusal('ieg_csv_missing_or_ambiguous_columns')
    if not rows:
        raise Refusal('ieg_csv_no_rows')
    id_key = headers[aliases.index('projectid')]
    outcome_key = headers[aliases.index('outcome')]
    rows_per_id, outcomes, unrecognized = Counter(), Counter(), Counter()
    outcomes_by_id = {}
    empty_ids = nonstandard = 0
    for row in rows:
        ident = row[id_key].strip().upper()
        category = outcome_category(row[outcome_key])
        outcomes[category] += 1
        if category == 'unrecognized':
            unrecognized[' '.join(row[outcome_key].split())[:80]] += 1
        if not ident:
            empty_ids += 1
            continue
        nonstandard += not PROJECT_ID.fullmatch(ident)
        rows_per_id[ident] += 1
        outcomes_by_id.setdefault(ident, Counter())[category] += 1
    return {
        'rows_per_id': rows_per_id, 'outcomes_by_id': outcomes_by_id,
        'summary': {
            'file': Path(path).name, 'sha256': hashlib.sha256(raw).hexdigest(),
            'provenance': 'operator-provided CSV; authenticity not verified by this tool',
            'rows': len(rows), 'unique_project_ids': len(rows_per_id),
            'rows_with_empty_project_id': empty_ids,
            'nonstandard_project_id_rows': nonstandard,
            'outcome_distribution_raw_categories': dict(sorted(outcomes.items())),
            'unrecognized_outcome_values': dict(sorted(unrecognized.items())[:50]),
            'unrecognized_outcome_distinct_values': len(unrecognized),
            'date_like_columns_present': sorted(
                IEG_DATE_ALIASES[a] for a in aliases if a in IEG_DATE_ALIASES),
        },
    }


def build(ieg, catalog):
    ieg_ids, cat_ids = set(ieg['rows_per_id']), set(catalog['docs_per_project'])
    matched = ieg_ids & cat_ids
    groups = []
    for ident in sorted(ieg_ids | cat_ids):
        n_ieg = ieg['rows_per_id'].get(ident, 0)
        n_docs = catalog['docs_per_project'].get(ident, 0)
        reasons = [name for name, flag in (
            ('multiple_ieg_rows', n_ieg > 1), ('multiple_documents', n_docs > 1),
            ('document_shared_with_other_projects', ident in catalog['shared_doc_projects']))
            if flag]
        if reasons:
            groups.append({'project_id': ident, 'ieg_rows': n_ieg,
                           'catalog_documents': n_docs, 'reasons': ';'.join(reasons)})
    ambiguous_matched = {g['project_id'] for g in groups if g['project_id'] in matched}
    matched_outcomes = Counter()
    for ident in matched:
        matched_outcomes.update(ieg['outcomes_by_id'][ident])
    report = {
        'inputs': {'ieg_csv': ieg['summary'], 'catalog': catalog['summary']},
        'coverage': {
            'matched_project_ids': len(matched),
            'matched_ieg_rows': sum(ieg['rows_per_id'][i] for i in matched),
            'ieg_ids_missing_from_catalog': len(ieg_ids - cat_ids),
            'catalog_ids_missing_from_ieg': len(cat_ids - ieg_ids),
            'matched_ids_one_row_one_document_unshared': len(matched - ambiguous_matched),
            'matched_ids_needing_manual_review': len(ambiguous_matched),
            'matched_outcome_distribution_raw_categories': dict(sorted(matched_outcomes.items())),
        },
        'groups': {
            'ieg_ids_with_multiple_rows': sum(n > 1 for n in ieg['rows_per_id'].values()),
            'ieg_additional_duplicate_rows': sum(n - 1 for n in ieg['rows_per_id'].values()),
            'catalog_ids_with_multiple_documents': sum(
                n > 1 for n in catalog['docs_per_project'].values()),
            'catalog_ids_in_shared_documents': len(catalog['shared_doc_projects']),
            'ambiguous_groups_total': len(groups),
            'ambiguous_groups_file': GROUPS_NAME,
        },
        'binary_labels_created': False,
        'rows_dropped_or_deduplicated': False,
        'training_performed': False,
        'validation_performed': False,
        'date_interpretation': (
            'as_of_date, evaluation_fy and docdt are not used and are NOT exact '
            'first-publication or feature-availability dates.'),
        'limitation': (
            'Join on project ID only, after trimming and upper-casing. Coverage of '
            'project IDs is not evidence that a document is the evaluation behind the '
            'rating, nor that the rating is the latest version. Duplicates are kept '
            'for manual review; no row is chosen.'),
    }
    return report, groups


def write_output(out_dir, report, groups):
    out_dir = Path(out_dir)
    try:
        out_dir.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        raise Refusal('output_directory_exists')
    except OSError:
        raise Refusal('output_directory_not_creatable')
    with (out_dir / GROUPS_NAME).open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=['project_id', 'ieg_rows',
                                                    'catalog_documents', 'reasons'])
        writer.writeheader()
        writer.writerows(groups)
    (out_dir / REPORT_NAME).write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def run(ieg_csv, catalog_dir, out_dir):
    if Path(out_dir).exists() or os.path.islink(out_dir):
        raise Refusal('output_directory_exists')
    catalog = load_catalog(catalog_dir)
    ieg = load_ieg(ieg_csv)
    report, groups = build(ieg, catalog)
    write_output(out_dir, report, groups)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ieg_csv', type=Path)
    parser.add_argument('catalog_dir', type=Path)
    parser.add_argument('out_dir', type=Path)
    args = parser.parse_args(argv)
    try:
        report = run(args.ieg_csv, args.catalog_dir, args.out_dir)
    except Refusal as exc:
        print(json.dumps({'join_passed': False, 'reason_code': str(exc)}))
        return 1
    print(json.dumps({'join_passed': True, 'coverage': report['coverage'],
                      'groups': report['groups']}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
