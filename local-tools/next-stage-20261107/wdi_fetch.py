"""Save a snapshot of selected World Development Indicators (api.worldbank.org/v2).

One request per indicator page; pages are bounded by what the API reports and the
run is refused unless the rows received equal the API's total. Values are the
current revision, not the vintage available at the time. Output dir must not exist.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import urllib.parse
import urllib.request

API = 'https://api.worldbank.org/v2/country/all/indicator/{}'
INDICATORS = {   # id -> source id (None: default WDI)
    'NY.GDP.MKTP.KD.ZG': None, 'NY.GDP.PCAP.KD': None, 'FP.CPI.TOTL.ZG': None,
    'GOV_WGI_GE.EST': '3', 'IQ.CPA.IRAI.XQ': None}
PER_PAGE = 20000
YEARS = '1960:2025'


class Refusal(Exception):
    pass


def get(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'sora-research-client'})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.loads(response.read())
        except (OSError, ValueError):
            if attempt == 2:
                raise Refusal('request_failed')
            time.sleep(5 * (attempt + 1))


def indicator(code, source):
    rows, page, pages, total = [], 1, 1, None
    while page <= pages:
        query = {'format': 'json', 'per_page': PER_PAGE, 'date': YEARS, 'page': page}
        if source:
            query['source'] = source
        data = get(API.format(code) + '?' + urllib.parse.urlencode(query))
        if not isinstance(data, list) or len(data) < 2 or data[1] is None:
            raise Refusal(f'no_data_for_{code}')
        pages, total = int(data[0]['pages']), int(data[0]['total'])
        rows += data[1]
        page += 1
    if len(rows) != total:
        raise Refusal(f'{code}_received_{len(rows)}_of_{total}')
    values, no_code = {}, 0
    for r in rows:
        if r.get('value') is None:
            continue
        if not r['country']['id']:      # territories without an ISO2 code
            no_code += 1
            continue
        key = f"{r['country']['id']}|{r['date']}"
        if key in values:
            raise Refusal(f'{code}_repeated_country_year')
        values[key] = float(r['value'])
    return values, {'api_rows': total, 'skipped_without_country_code': no_code}


def run(out_dir):
    out_dir = Path(out_dir)
    if out_dir.exists() or out_dir.is_symlink():
        raise Refusal('output_directory_exists')
    data, totals = {}, {}
    for code, source in INDICATORS.items():
        data[code], totals[code] = indicator(code, source)
    out_dir.mkdir(parents=False)
    raw = json.dumps(data, sort_keys=True).encode()
    (out_dir / 'wdi.json').write_bytes(raw)
    manifest = {'source': API, 'indicators': INDICATORS, 'years': YEARS,
                'rows': totals, 'non_null_values': {k: len(v) for k, v in data.items()},
                'key': 'ISO2|calendar year', 'json_sha256': hashlib.sha256(raw).hexdigest(),
                'fetched_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                'values_as_of': 'current revision at fetch time, not the historical vintage'}
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
