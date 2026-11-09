import csv
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import sys
import tempfile
import unittest

import numpy as np

from ieg_join import Refusal
import ieg_baseline
from ieg_country_features import CANDIDATES, LAG, transform
import ieg_frozen_m6
from ieg_frozen_m6 import evaluate, freeze
import test_ieg_baseline
from test_ieg_country_features import snapshot

COUNTRIES = [f'C{i}' for i in range(25)]
COLUMNS = ['gdp_growth', 'log_gdp_pc', 'inflation_slog']
HEADER = ['Project ID', 'Outcome', 'Evaluation Type', 'Approval FY', 'Evaluation FY']


def rating(y):
    return 'Satisfactory' if y else 'Unsatisfactory'


class Frozen(unittest.TestCase):
    setUp_base = test_ieg_baseline.Baseline.setUp
    write = test_ieg_baseline.Baseline.write

    def setUp(self):
        self.setUp_base()
        rng = random.Random(4)
        self.rng = rng
        self.wdi = {code: {} for code, _ in CANDIDATES.values()}
        for c in COUNTRIES:
            for year in range(1995, 2026):
                if rng.random() < 0.08:          # some gaps -> missing flags are exercised
                    continue
                self.wdi['NY.GDP.MKTP.KD.ZG'][f'{c}|{year}'] = rng.gauss(3, 3)
                self.wdi['NY.GDP.PCAP.KD'][f'{c}|{year}'] = 10 ** rng.uniform(2.5, 4.5)
                self.wdi['FP.CPI.TOTL.ZG'][f'{c}|{year}'] = rng.gauss(6, 8)
        snapshot(self.tmp / 'wdi', 'wdi.json', self.wdi)
        self.projects = []
        table, ieg_rows = [], []
        for i in range(600):
            split = 'train' if i < 400 else 'test'
            approval = 2000 + i % 9 if split == 'train' else 2014 + i % 5
            ident = f'P{i:06d}'
            country = rng.choice(COUNTRIES)
            values = self.values(country, approval)
            y = self.outcome(values, signal=True)
            self.projects.append({'id': ident, 'countrycode': [country]})
            table.append({'source_project_id': ident, 'split': split, 'country': country,
                          'region': 'R', 'agreement_type': 'IDA', 'lending_instrument_type': 'IPF',
                          'approval_fy': str(approval), 'practice_group': 'X',
                          'global_practice': 'G', 'y': y, 'cluster': ident, **values})
            ieg_rows.append([ident, rating(y), 'ICRR', approval,
                             approval + 6 if split == 'train' else 2021])
        self.ieg = self.tmp / 'ieg.csv'
        self.write_ieg(self.ieg, ieg_rows)
        self.old_rows = ieg_rows
        ieg_sha = hashlib.sha256(self.ieg.read_bytes()).hexdigest()
        f, o = self.write(table, sha=ieg_sha)
        (f / 'feature-report.json').write_text(json.dumps({'inputs': {'ieg_csv_sha256': ieg_sha}}))
        self.dirs = {'features': f, 'outcomes': o, 'country': self.tmp / 'country',
                     'baseline': self.tmp / 'baseline'}
        self.dirs['country'].mkdir()
        with (self.dirs['country'] / 'country-features.csv').open('w', newline='') as s:
            w = csv.DictWriter(s, ['source_project_id', 'split', *COLUMNS], extrasaction='ignore')
            w.writeheader()
            w.writerows(table)
        (self.dirs['country'] / 'country-feature-report.json').write_text(json.dumps(
            {'inputs': {'wdi_json_sha256': self.sha('wdi', 'wdi.json')}}))
        self.new_rows = self.new_projects(600, signal=True, prefix='N')
        self.noise_rows = self.new_projects(400, signal=False, prefix='Z')
        self.few_country_rows = self.new_projects(400, signal=True, prefix='K',
                                                  countries=COUNTRIES[:4],
                                                  shift={'C0': 3, 'C1': -3, 'C2': 2, 'C3': -2})
        snapshot(self.tmp / 'projects', 'projects.json', self.projects)
        ieg_baseline.run(f, o, self.dirs['baseline'], n_boot=20, country_dir=self.dirs['country'])

    def sha(self, d, name):
        return hashlib.sha256((self.tmp / d / name).read_bytes()).hexdigest()

    def values(self, country, approval):
        year = approval - LAG
        return {c: transform(self.wdi[CANDIDATES[c][0]].get(f'{country}|{year}'), CANDIDATES[c][1])
                for c in COLUMNS}

    def outcome(self, values, signal, shift=0.0):
        if not signal or values['gdp_growth'] == '':
            return int(self.rng.random() < 0.7)
        z = 1.0 + shift + 1.2 * (float(values['gdp_growth']) - 3) / 3
        return int(self.rng.random() < 1 / (1 + math.exp(-z)))

    def new_projects(self, n, signal, prefix, countries=COUNTRIES, shift=None):
        rows = []
        for i in range(n):
            ident, country, approval = f'{prefix}{i:06d}', self.rng.choice(countries), 2015 + i % 6
            y = self.outcome(self.values(country, approval), signal,
                             (shift or {}).get(country, 0.0))
            self.projects.append({'id': ident, 'countrycode': country})
            rows.append([ident, rating(y), 'ICRR', approval, 2027])
        return rows

    def write_ieg(self, path, rows):
        with path.open('w', newline='') as s:
            w = csv.writer(s)
            w.writerow(HEADER)
            w.writerows(rows)

    def frozen(self):
        result = freeze(self.dirs['features'], self.dirs['outcomes'], self.dirs['country'],
                        self.dirs['baseline'], self.tmp / 'projects', self.tmp / 'wdi',
                        self.ieg, self.tmp / 'frozen')
        return result

    def new_export(self, rows, name='new.csv'):
        extra = [['X000001', 'Satisfactory', 'PPAR', 2016, 2027],        # P3
                 ['X000002', 'Not Rated', 'ICRR', 2016, 2027],           # P5
                 ['X000003', 'Satisfactory', 'ICRR', 2012, 2027],        # approved too early
                 ['X000004', 'Satisfactory', 'ICRR', 2016, 2027]]        # not in the snapshot
        path = self.tmp / name
        self.write_ieg(path, self.old_rows + rows + extra)
        return path

    def run_eval(self, path, sha, out='eval'):
        return evaluate(self.tmp / 'frozen', path, self.tmp / 'projects', self.tmp / 'wdi',
                        self.tmp / out, expected_sha=sha, n_boot=300)

    def test_freeze_reproduces_section_9_and_signal_is_confirmed(self):
        result = self.frozen()
        self.assertLessEqual(result['reproduction_max_abs_difference'], 1e-6)
        self.assertEqual(result['columns'], COLUMNS)
        self.assertEqual(result['seen_project_ids'], 600)
        report = self.run_eval(self.new_export(self.new_rows[:400]), result['model_sha256'])
        self.assertTrue(report['evaluated'])
        self.assertEqual(report['new_projects'], 400)
        self.assertEqual(report['excluded_by_reason'],
                         {'P3_not_icrr': 1, 'P5_not_an_outcome': 1, 'approval_before_2014': 1,
                          'not_in_projects_snapshot': 1, 'seen_before_the_freeze': 600})
        self.assertGreater(report['auc'], 0.7)
        self.assertTrue(report['better_than_class_rate'])
        with (self.tmp / 'eval' / 'new-test-predictions.csv').open() as s:
            ids = [r['source_project_id'] for r in csv.DictReader(s)]
        self.assertTrue(all(i.startswith('N') for i in ids))

    def test_prediction_uses_stored_numbers_like_sklearn(self):
        self.frozen()
        model = json.loads((self.tmp / 'frozen' / 'model.json').read_text())
        rows = [{c: '' for c in COLUMNS}, {'gdp_growth': '1.5', 'log_gdp_pc': '', 'inflation_slog': '0.3'}]
        z = model['intercept'] + model['coef'][1] + model['coef'][3] + model['coef'][5]
        self.assertAlmostEqual(ieg_frozen_m6.predict(model, rows[:1])[0], 1 / (1 + math.exp(-z)), 12)
        mean, std = model['scale']['gdp_growth']
        mi, si = model['scale']['inflation_slog']
        z = (model['intercept'] + model['coef'][0] * (1.5 - mean) / std + model['coef'][3]
             + model['coef'][4] * (0.3 - mi) / si)
        self.assertAlmostEqual(ieg_frozen_m6.predict(model, rows[1:])[0], 1 / (1 + math.exp(-z)), 12)

    def test_noise_is_not_confirmed(self):
        sha = self.frozen()['model_sha256']
        report = self.run_eval(self.new_export(self.noise_rows, 'noise.csv'), sha)
        self.assertTrue(report['evaluated'])
        self.assertFalse(report['better_than_class_rate'])

    def test_below_the_size_rule_only_counts_are_reported(self):
        sha = self.frozen()['model_sha256']
        report = self.run_eval(self.new_export(self.new_rows[:299]), sha)
        self.assertFalse(report['evaluated'])
        self.assertEqual(report['reason'], 'not_enough_new_evaluations')
        self.assertEqual(report['new_projects'], 299)
        self.assertNotIn('auc', report)
        self.assertFalse((self.tmp / 'eval').exists())
        few_negative = [r for r in self.new_rows if r[1] == 'Satisfactory'] + \
            [r for r in self.new_rows if r[1] != 'Satisfactory'][:29]
        report = self.run_eval(self.new_export(few_negative, 'few.csv'), sha)
        self.assertGreaterEqual(report['new_projects'], 300)
        self.assertEqual(report['negative'], 29)
        self.assertFalse(report['evaluated'])

    def test_the_bootstrap_draws_whole_countries(self):
        # 400 projects in 4 countries with strong country effects the model does not
        # see: resampling countries must give a much wider interval than projects.
        sha = self.frozen()['model_sha256']
        report = self.run_eval(self.new_export(self.few_country_rows, 'four.csv'), sha)
        self.assertEqual(report['countries'], 4)
        with (self.tmp / 'eval' / 'new-test-predictions.csv').open() as s:
            rows = list(csv.DictReader(s))
        y = np.array([int(r['outcome']) for r in rows])
        p = np.array([float(r['M6_country_only']) for r in rows])
        aucs = [ieg_baseline.roc_auc_score(y[i], p[i]) for i in ieg_baseline.cluster_resamples(
            [r['source_project_id'] for r in rows], 300, ieg_baseline.SEED)]
        by_project = ieg_baseline.interval(aucs)
        width = report['auc_ci95'][1] - report['auc_ci95'][0]
        self.assertGreater(width, 1.4 * (by_project[1] - by_project[0]))   # measured 0.147 vs 0.090

    def test_a_changed_model_or_input_is_refused(self):
        sha = self.frozen()['model_sha256']
        path = self.new_export(self.new_rows)
        with self.assertRaises(Refusal) as caught:
            self.run_eval(path, None)
        self.assertEqual(str(caught.exception), 'model_is_not_the_frozen_one')
        model_file = self.tmp / 'frozen' / 'model.json'
        original = model_file.read_text()
        model = json.loads(original)
        model['coef'][0] += 0.5
        model_file.write_text(json.dumps(model))
        with self.assertRaises(Refusal) as caught:
            self.run_eval(path, sha)
        self.assertEqual(str(caught.exception), 'model_is_not_the_frozen_one')
        model_file.write_text(original)
        ids = self.tmp / 'frozen' / 'seen-project-ids.txt'
        ids.write_text(ids.read_text().replace('P000001\n', ''))
        with self.assertRaises(Refusal) as caught:
            self.run_eval(path, sha)
        self.assertEqual(str(caught.exception), 'seen_project_ids_changed')
        self.assertFalse((self.tmp / 'eval').exists())

    def test_a_snapshot_other_than_the_frozen_one_is_refused(self):
        sha = self.frozen()['model_sha256']
        shutil.rmtree(self.tmp / 'wdi')
        self.wdi['NY.GDP.MKTP.KD.ZG']['C0|2015'] = 99.0
        snapshot(self.tmp / 'wdi', 'wdi.json', self.wdi)
        with self.assertRaises(Refusal) as caught:
            self.run_eval(self.new_export(self.new_rows), sha)
        self.assertEqual(str(caught.exception), 'snapshot_is_not_the_frozen_one')

    def test_freeze_refuses_when_section_9_is_not_reproduced(self):
        path = self.dirs['baseline'] / 'test-predictions.csv'
        with path.open(newline='') as s:
            rows = list(csv.DictReader(s))
        rows[3]['M6_country_only'] = f"{float(rows[3]['M6_country_only']) + 1e-3:.6f}"
        with path.open('w', newline='') as s:
            w = csv.DictWriter(s, list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        with self.assertRaises(Refusal) as caught:
            self.frozen()
        self.assertEqual(str(caught.exception), 'frozen_model_does_not_reproduce_section_9')
        self.assertFalse((self.tmp / 'frozen').exists())

    def test_freeze_refuses_another_ieg_export(self):
        self.write_ieg(self.ieg, self.old_rows[:-1])
        with self.assertRaises(Refusal) as caught:
            self.frozen()
        self.assertEqual(str(caught.exception), 'ieg_csv_is_not_the_source_of_the_outcomes')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('IEG_FROZEN_M6_CONTROLS_PASSED')
