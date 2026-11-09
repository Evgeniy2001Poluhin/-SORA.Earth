"""Save a snapshot of the World Bank Projects API (search.worldbank.org/api/v3/projects).

Only the fields listed in FIELDS are requested. Ratings (ieg_ratings, icr_ratings,
isr_ratings), results indicators and loan transactions are NOT requested: they are
outcome information. Pages are sorted on the unique id and the run is refused unless
the distinct ids equal the total the API reported. The page count is bounded by that
total; each page gets at most 3 attempts. Output directory must not exist.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

API = 'https://search.worldbank.org/api/v3/projects'
FIELDS = ['id', 'boardapprovaldate', 'fiscalyear', 'countrycode', 'countryshortname',
          'regionname', 'totalamt', 'idacommamt', 'ibrdcommamt', 'grantamt',
          'lendprojectcost', 'curr_project_cost', 'curr_total_commitment',
          'curr_ibrd_commitment', 'curr_ida_commitment', 'prodline', 'projectfinancialtype',
          'lendinginstr', 'envassesmentcategorycode', 'milestones', 'sectorcode',
          'sector_percent', 'borrower', 'impagency', 'status', 'closingdate',
          'proj_last_upd_date']
FORBIDDEN = {'ieg_ratings', 'icr_ratings', 'isr_ratings', 'indicators', 'loan_transactions'}
ROWS = 500


class Refusal(Exception):
    pass


def page(offset):
    query = urllib.parse.urlencode({'format': 'json', 'fl': ','.join(FIELDS), 'rows': ROWS,
                                    'os': offset, 'srt': 'id', 'order': 'asc'})
    request = urllib.request.Request(f'{API}?{query}',
                                     headers={'User-Agent': 'sora-research-client'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                return json.loads(response.read())
        except (OSError, ValueError):
            if attempt == 2:
                raise Refusal(f'page_failed_at_offset_{offset}')
            time.sleep(5 * (attempt + 1))


def run(out_dir):
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise Refusal('output_directory_exists')
    first = page(0)
    total = int(first['total'])
    records, offsets = {}, range(0, total, ROWS)
    repeats = 0
    for offset in offsets:
        data = first if offset == 0 else page(offset)
        for pid, project in (data.get('projects') or {}).items():
            if FORBIDDEN & set(project):
                raise Refusal('api_returned_a_forbidden_field')
            repeats += pid in records
            records[pid] = project
    if len(records) != total:
        raise Refusal(f'distinct_ids_{len(records)}_differ_from_total_{total}')
    out_dir.mkdir(parents=False)
    raw = json.dumps([records[k] for k in sorted(records)], ensure_ascii=False,
                     sort_keys=True).encode('utf-8')
    (out_dir / 'projects.json').write_bytes(raw)
    manifest = {'source': API, 'fields': FIELDS, 'sorted_on': 'id asc', 'page_rows': ROWS,
                'api_total': total, 'distinct_ids': len(records), 'repeated_ids': repeats,
                'json_sha256': hashlib.sha256(raw).hexdigest(),
                'fetched_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'contains_outcome_ratings': False,
                'values_as_of': 'fetch time (current values, not values at approval)'}
    (out_dir / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('out_dir', type=Path)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(run(args.out_dir), indent=2))
    except Refusal as exc:
        print(json.dumps({'fetch_passed': False, 'reason_code': str(exc)}))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
