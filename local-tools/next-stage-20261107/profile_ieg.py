"""Profile an operator-downloaded IEG CSV; never create labels, train, or claim validation."""
import argparse
from collections import Counter
import csv
import hashlib
import io
import json
from pathlib import Path
import re

MAX_BYTES=25*1024*1024
RATINGS=('highly satisfactory','satisfactory','moderately satisfactory',
         'moderately unsatisfactory','unsatisfactory','highly unsatisfactory')


def normalize(value):
    return re.sub('[^a-z0-9]','',value.casefold())


def profile(raw):
    if len(raw)>MAX_BYTES: raise ValueError('input_too_large')
    reader=csv.DictReader(io.StringIO(raw.decode('utf-8-sig'),newline=''))
    headers=reader.fieldnames or []
    aliases=[normalize(h) for h in headers]
    if len(set(aliases))!=len(aliases) or not {'projectid','outcome'}<=set(aliases):
        raise ValueError('missing_or_ambiguous_columns')
    project_key=headers[aliases.index('projectid')]
    outcome_key=headers[aliases.index('outcome')]
    projects=Counter();ratings=Counter();total=0;missing_ids=0
    for row in reader:
        total+=1
        if None in row or any(v is None for v in row.values()): raise ValueError('malformed_row')
        ident=row[project_key].strip()
        if ident: projects[ident]+=1
        else: missing_ids+=1
        rating=' '.join(row[outcome_key].casefold().split())
        category=rating if rating in RATINGS else ('not rated' if rating=='not rated' else 'missing' if not rating else 'unrecognized')
        ratings[category]+=1
    if total==0: raise ValueError('empty_dataset')
    return {'profiled_rows':total,'distinct_nonempty_project_ids':len(projects),
            'missing_project_ids':missing_ids,'duplicate_project_groups':sum(n>1 for n in projects.values()),
            'additional_duplicate_rows':sum(n-1 for n in projects.values()),'rating_distribution':dict(sorted(ratings.items())),
            'source_file_sha256':hashlib.sha256(raw).hexdigest(),
            'provenance':'operator-provided CSV; authenticity not verified by this tool',
            'independent_validation_ready':False,'training_performed':False,
            'limitation':'Profile only. Target definition, exact publication times, feature provenance, project linkage and temporal test are not checked. No rows are silently dropped or converted into binary labels.'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_path',type=Path)
    args=parser.parse_args()
    try:
        with args.csv_path.open('rb') as stream: raw=stream.read(MAX_BYTES+1)
        result=profile(raw)
    except (OSError,UnicodeError,csv.Error,ValueError) as exc:
        code=str(exc) if isinstance(exc,ValueError) and str(exc) in ('input_too_large','missing_or_ambiguous_columns','malformed_row','empty_dataset') else 'input_unreadable_or_invalid'
        print(json.dumps({'profile_passed':False,'reason_code':code}));return 1
    print(json.dumps(result,ensure_ascii=False,indent=2));return 0


if __name__=='__main__':raise SystemExit(main())
