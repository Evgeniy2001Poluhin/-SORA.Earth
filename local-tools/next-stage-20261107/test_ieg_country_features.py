import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from ieg_join import Refusal
from ieg_country_features import CANDIDATES, admitted, run
import ieg_baseline
import test_ieg_baseline
from test_ieg_baseline import make_table


def snapshot(directory, name, data):
    directory.mkdir()
    raw = json.dumps(data).encode()
    (directory / name).write_bytes(raw)
    (directory / 'manifest.json').write_text(json.dumps(
        {'json_sha256': hashlib.sha256(raw).hexdigest()}))


class CountryFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.features = self.tmp / 'features'
        self.features.mkdir()
        rows = [('P1', 'train', '2005'), ('P2', 'train', '2006'), ('P5', 'train', '2005'),
                ('P6', 'train', '2007'), ('P3', 'test', '2016'), ('P4', 'test', '2017'),
                ('P7', 'test', '2016'), ('P8', 'test', '2017')]   # 3 of 4 filled in each
        with (self.features / 'features.csv').open('w', newline='') as s:
            w = csv.writer(s)
            w.writerow(['source_project_id', 'split', 'approval_fy'])
            w.writerows(rows)
        snapshot(self.tmp / 'projects', 'projects.json',
                 [{'id': 'P1', 'countrycode': ['KE']}, {'id': 'P2', 'countrycode': 'PE'},
                  {'id': 'P3', 'countrycode': ['KE']}, {'id': 'P4', 'countrycode': '3A'},
                  {'id': 'P5', 'countrycode': '3A'}, {'id': 'P6', 'countrycode': 'KE'},
                  {'id': 'P7', 'countrycode': 'PE'}, {'id': 'P8', 'countrycode': 'KE'}])
        series = {}
        for code in {i for i, _ in CANDIDATES.values()}:
            # the value encodes its own year, so the lag is visible in the output
            series[code] = {f'{c}|{y}': float(y - 2000) for c in ('KE', 'PE')
                            for y in range(2000, 2018)}
        series['FP.CPI.TOTL.ZG']['KE|2003'] = -9.0
        series['NY.GDP.PCAP.KD']['PE|2004'] = 100.0
        series['IQ.CPA.IRAI.XQ'] = {}                          # never filled -> rejected
        self.wdi = series
        snapshot(self.tmp / 'wdi', 'wdi.json', series)
        self.out = self.tmp / 'out'

    def table(self):
        with (self.out / 'country-features.csv').open(newline='') as s:
            return {r['source_project_id']: r for r in csv.DictReader(s)}

    def test_value_is_from_approval_fy_minus_two(self):
        report = run(self.features, self.tmp / 'projects', self.tmp / 'wdi', self.out)
        t = self.table()
        self.assertEqual(t['P1']['gdp_growth'], '3.000000')            # 2005 - 2 = 2003
        self.assertEqual(t['P3']['gdp_growth'], '14.000000')           # 2016 - 2 = 2014
        self.assertEqual(t['P1']['inflation_slog'], '-1.000000')       # -log10(1 + 9)
        self.assertEqual(t['P2']['log_gdp_pc'], '2.000000')            # log10(100), PE 2004
        self.assertEqual(t['P4']['gdp_growth'], '')                    # code not in WDI
        self.assertNotIn('cpia_irai', t['P1'])
        self.assertIn('cpia_irai', report['rejected'])
        self.assertFalse(report['outcome_read'])

    def test_admission_rule_boundaries(self):
        cols = list(CANDIDATES)

        def fill(train, test):
            return {'train': {c: train for c in cols}, 'test': {c: test for c in cols}}
        self.assertTrue(all(admitted(fill(0.8, 0.7)).values()))       # gap exactly 0.10
        self.assertFalse(any(admitted(fill(0.81, 0.7)).values()))      # gap 0.11
        self.assertFalse(any(admitted(fill(0.75, 0.69)).values()))     # test fill below 0.70
        self.assertFalse(any(admitted(fill(0.69, 0.75)).values()))     # train fill below 0.70

    def test_refusals(self):
        (self.tmp / 'wdi' / 'manifest.json').write_text(json.dumps({'json_sha256': 'x'}))
        with self.assertRaises(Refusal) as caught:
            run(self.features, self.tmp / 'projects', self.tmp / 'wdi', self.out)
        self.assertEqual(str(caught.exception), 'wdi.json_hash_mismatch')
        shutil.rmtree(self.tmp / 'wdi')
        del self.wdi['NY.GDP.MKTP.KD.ZG']
        snapshot(self.tmp / 'wdi', 'wdi.json', self.wdi)
        with self.assertRaises(Refusal) as caught:
            run(self.features, self.tmp / 'projects', self.tmp / 'wdi', self.out)
        self.assertEqual(str(caught.exception), 'wdi_snapshot_missing_an_indicator')
        self.assertFalse(self.out.exists())


class CountryModels(unittest.TestCase):
    setUp = test_ieg_baseline.Baseline.setUp
    write = test_ieg_baseline.Baseline.write

    def write_country(self, table, columns):
        d = self.tmp / 'country'
        d.mkdir(exist_ok=True)
        with (d / 'country-features.csv').open('w', newline='') as s:
            w = csv.DictWriter(s, ['source_project_id', 'split', *columns], extrasaction='ignore')
            w.writeheader()
            w.writerows(table)
        return d

    def test_country_models_are_added_and_earlier_ones_unchanged(self):
        table = [dict(r, gdp_growth=f'{r["y"] * 3 + (i % 7) * 0.1:.3f}', log_gdp_pc='')
                 for i, r in enumerate(make_table(signal=False, seed=11))]
        f, o = self.write(table)
        plain = ieg_baseline.run(f, o, self.tmp / 'a', n_boot=100)
        withc = ieg_baseline.run(f, o, self.tmp / 'b', n_boot=100,
                                 country_dir=self.write_country(table, ['gdp_growth', 'log_gdp_pc']))
        self.assertEqual(plain['models']['M1_region_practice'], withc['models']['M1_region_practice'])
        self.assertGreater(withc['models']['M6_country_only']['auc'], 0.95)
        self.assertIn('auc_difference_M5_minus_M1', withc)
        self.assertNotIn('auc_difference_M6_minus_M1', withc)

    def test_a_column_outside_the_candidates_is_refused(self):
        table = [dict(r, outcome_hint=str(r['y'])) for r in make_table(n_train=20, n_test=10)]
        f, o = self.write(table)
        with self.assertRaises(Refusal) as caught:
            ieg_baseline.run(f, o, self.tmp / 'out', n_boot=10,
                             country_dir=self.write_country(table, ['outcome_hint']))
        self.assertEqual(str(caught.exception), 'country_features_columns_not_admissible')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('IEG_COUNTRY_FEATURES_CONTROLS_PASSED')
