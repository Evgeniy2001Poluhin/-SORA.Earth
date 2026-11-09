"""Classify matched IEG project IDs for manual review; propose, never decide.

Reuses ieg_join's verified loaders (manifest, SHA-256, strict CSV), then adds:
evaluation type versus catalog document type, clusters of projects joined by a
shared document, and whether the raw ratings inside a group agree. Writes only
IDs, counts and flags -- no rating text, no binary labels, no chosen row.
"""
import argparse
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import sys

from ieg_join import (CATALOG_MAX_BYTES, IEG_MAX_BYTES, Refusal, decode, load_catalog,
                      load_ieg, normalize, outcome_category, read_limited, rows_of)

REPORT_NAME = 'review-report.json'
TABLE_NAME = 'review-by-project.csv'
# The saved catalog holds only this document type; IEG calls the same review ICRR.
CATALOG_TYPE_TO_IEG = {'implementation completion report review': 'ICRR'}
FIELDS = ['project_id', 'status', 'reasons', 'ieg_rows', 'ieg_outcomes_agree',
          'evaluation_types', 'catalog_documents', 'cluster_id', 'cluster_size',
          'cluster_rated_members', 'cluster_outcomes_agree']


def ieg_rows(path):
    headers, rows = rows_of(decode(read_limited(path, IEG_MAX_BYTES, 'ieg_csv'), 'ieg_csv'),
                            'ieg_csv')
    aliases = [normalize(h) for h in headers]
    key = {a: headers[aliases.index(a)] for a in ('projectid', 'outcome', 'evaluationtype')
           if a in aliases}
    per_id = defaultdict(list)
    for row in rows:
        ident = row[key['projectid']].strip().upper()
        if ident:
            per_id[ident].append({
                'outcome': outcome_category(row[key['outcome']]),
                'type': row[key['evaluationtype']].strip().upper()
                if 'evaluationtype' in key else ''})
    return per_id


def catalog_documents(directory):
    headers, rows = rows_of(decode(read_limited(Path(directory) / 'documents.csv',
                                                CATALOG_MAX_BYTES, 'catalog'), 'catalog'),
                            'catalog')
    docs = []
    for row in rows:
        ids = sorted({p.strip().upper() for p in row['projectid'].split(',') if p.strip()})
        if ids:
            docty = ' '.join(row.get('docty', '').casefold().split())
            docs.append({'id': row['id'].strip(), 'projects': ids,
                         'ieg_type': CATALOG_TYPE_TO_IEG.get(docty, '')})
    return docs


def clusters(docs):
    """Union-find over project IDs that share a document; id = smallest member."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    for doc in docs:
        root = find(doc['projects'][0])
        for other in doc['projects'][1:]:
            a, b = sorted((root, find(other)))
            parent[b] = a
            root = a
    members = defaultdict(list)
    for p in {p for d in docs for p in d['projects']}:
        members[find(p)].append(p)
    return {p: (min(group), group) for group in members.values() for p in group}


def agree(categories):
    """'yes' only if every raw category is identical; 'not rated' differs from a rating."""
    return 'yes' if len(set(categories)) == 1 else 'no'


def classify(per_id, docs):
    docs_of = defaultdict(list)
    for doc in docs:
        for p in doc['projects']:
            docs_of[p].append(doc)
    cluster_of = clusters(docs)
    table = []
    for ident in sorted(set(per_id) & set(docs_of)):
        rows, mine = per_id[ident], docs_of[ident]
        cid, group = cluster_of[ident]
        rated = [m for m in group if m in per_id]
        types = sorted({r['type'] or '?' for r in rows})
        doc_types = {d['ieg_type'] for d in mine}
        reasons = []
        if len(rows) > 1:
            reasons.append('multiple_ieg_rows')
        if len(mine) > 1:
            reasons.append('multiple_documents')
        if len(group) > 1:
            reasons.append('shared_document_cluster')
        if types == ['?']:
            reasons.append('evaluation_type_unknown')
        elif not set(types) & doc_types:
            reasons.append('evaluation_type_differs_from_document')
        cluster_agree = (agree([r['outcome'] for m in rated for r in per_id[m]])
                         if len(group) > 1 else 'na')
        table.append({
            'project_id': ident,
            'status': 'needs_review' if reasons else 'one_to_one_same_type',
            'reasons': ';'.join(reasons),
            'ieg_rows': len(rows),
            'ieg_outcomes_agree': agree([r['outcome'] for r in rows]) if len(rows) > 1 else 'na',
            'evaluation_types': ';'.join(types),
            'catalog_documents': len(mine),
            'cluster_id': cid, 'cluster_size': len(group), 'cluster_rated_members': len(rated),
            'cluster_outcomes_agree': cluster_agree})
    return table


def summarize(table):
    reasons = Counter(r for row in table for r in row['reasons'].split(';') if r)
    cluster_rows = {row['cluster_id']: row for row in table if row['cluster_size'] > 1}
    return {
        'matched_project_ids': len(table),
        'status': dict(sorted(Counter(row['status'] for row in table).items())),
        'reasons': dict(sorted(reasons.items())),
        'evaluation_types_of_matched_ids': dict(sorted(
            Counter(row['evaluation_types'] for row in table).items())),
        'one_to_one_by_evaluation_type': dict(sorted(Counter(
            row['evaluation_types'] for row in table if row['ieg_rows'] == 1
            and row['catalog_documents'] == 1 and row['cluster_size'] == 1).items())),
        'shared_document_clusters': len(cluster_rows),
        'shared_document_clusters_outcomes_disagree': sum(
            row['cluster_outcomes_agree'] == 'no' for row in cluster_rows.values()),
        'cluster_size_distribution': dict(sorted(Counter(
            row['cluster_size'] for row in cluster_rows.values()).items())),
        'ieg_duplicate_ids_outcomes_disagree': sum(
            row['ieg_outcomes_agree'] == 'no' for row in table),
    }


def run(ieg_csv, catalog_dir, out_dir):
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise Refusal('output_directory_exists')
    catalog = load_catalog(catalog_dir)        # manifest, hash, counts, uniqueness
    ieg = load_ieg(ieg_csv)                    # strict CSV, required columns
    table = classify(ieg_rows(ieg_csv), catalog_documents(catalog_dir))
    report = {
        'inputs': {'ieg_csv_sha256': ieg['summary']['sha256'],
                   'ieg_csv_file': ieg['summary']['file'],
                   'catalog_sha256': catalog['summary']['sha256']},
        'review': summarize(table),
        'table_file': TABLE_NAME,
        'decisions_made': False,
        'binary_labels_created': False,
        'training_performed': False,
        'validation_performed': False,
        'limitation': (
            'Flags only. A shared document is a hint of a joint operation, not proof; '
            'a same-type match on project ID does not prove the document is the source '
            'of the rating; FY columns and docdt are not dates of publication.'),
    }
    try:
        out_dir.mkdir(parents=False, exist_ok=False)
    except FileExistsError:
        raise Refusal('output_directory_exists')
    except OSError:
        raise Refusal('output_directory_not_creatable')
    with (out_dir / TABLE_NAME).open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(table)
    (out_dir / REPORT_NAME).write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n',
                                       encoding='utf-8')
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
        print(json.dumps({'review_passed': False, 'reason_code': str(exc)}))
        return 1
    print(json.dumps({'review_passed': True, **report['review']}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
