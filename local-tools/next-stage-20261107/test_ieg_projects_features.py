import csv
import hashlib
import json
from pathlib import Path
import random
import shutil
import sys
import tempfile
import unittest

from ieg_join import Refusal
from ieg_projects_features import run
import ieg_baseline
import test_ieg_baseline
from test_ieg_baseline import make_table


def project(pid, approved, amount='5000000', concept='2001-01-01', appraisal='2001-07-01',
            env='B'):
    return {'id': pid, 'boardapprovaldate': f'{approved}T00:00:00Z', 'totalamt': amount,
            'curr_total_commitment': '999999999999', 'curr_project_cost': '999999999999',
            'lendprojectcost': '999999999999', 'closingdate': '2030-01-01',
            'envassesmentcategorycode': env,
            'milestones': [{'concpt_review_date': f'{concept}T00:00:00Z' if concept else None,
                            'begin_apprsl_date': f'{appraisal}T00:00:00Z' if appraisal else None,
                            'effctvnss_date': '2003-01-01T00:00:00Z'}]}


class ProjectsFeatures(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.features = self.tmp / 'features'
        self.features.mkdir()
        self.rows = [('P1', 'train', '2002'), ('P2', 'train', '2002'), ('P3', 'test', '2016')]
        with (self.features / 'features.csv').open('w', newline='') as s:
            w = csv.writer(s)
            w.writerow(['source_project_id', 'split', 'approval_fy'])
            w.writerows(self.rows)
        self.snapshot = self.tmp / 'snapshot'
        self.write_snapshot([
            project('P1', '2002-01-01'),                                    # FY2002
            project('P2', '2001-09-15', amount='0', concept='2002-01-01'),  # concept after approval
            project('P3', '2015-07-01', amount='', concept=None, env='')])  # FY2016

    def write_snapshot(self, projects, bad_hash=False):
        self.snapshot.mkdir(exist_ok=True)
        raw = json.dumps(projects).encode()
        (self.snapshot / 'projects.json').write_bytes(raw)
        (self.snapshot / 'manifest.json').write_text(json.dumps(
            {'json_sha256': 'x' if bad_hash else hashlib.sha256(raw).hexdigest()}))

    def table(self):
        with (self.tmp / 'out' / 'projects-features.csv').open(newline='') as s:
            return {r['source_project_id']: r for r in csv.DictReader(s)}

    def test_values_are_approval_time_only(self):
        report = run(self.features, self.snapshot, self.tmp / 'out')
        t = self.table()
        self.assertEqual(t['P1']['log_commitment'], '6.698970')       # log10(5e6), not curr_*
        self.assertEqual(t['P1']['prep_months'], f'{365 / 30.4375:.3f}')
        self.assertEqual(t['P1']['appraisal_months'], f'{184 / 30.4375:.3f}')
        self.assertEqual(t['P1']['env_category'], 'B')
        self.assertEqual(t['P2']['log_commitment'], '')               # zero amount
        self.assertEqual(t['P2']['prep_months'], '')                  # dated after approval
        self.assertEqual(t['P3']['prep_months'], '')                  # missing
        self.assertEqual(report['milestones_after_approval_set_empty'],
                         {'prep_months_dated_after_approval': 1})
        text = (self.tmp / 'out' / 'projects-features.csv').read_text()
        self.assertNotIn('999999999999', text)
        self.assertNotIn('2030', text)

    def test_fiscal_year_must_match_ieg(self):
        self.write_snapshot([project('P1', '2002-07-01'),             # FY2003, IEG says 2002
                             project('P2', '2001-09-15'), project('P3', '2015-07-01')])
        with self.assertRaises(Refusal) as caught:
            run(self.features, self.snapshot, self.tmp / 'out')
        self.assertEqual(str(caught.exception), 'approval_date_disagrees_with_ieg_approval_fy')
        self.assertFalse((self.tmp / 'out').exists())

    def test_refusals(self):
        self.write_snapshot([project('P1', '2002-01-01'), project('P2', '2001-09-15')])
        with self.assertRaises(Refusal) as caught:
            run(self.features, self.snapshot, self.tmp / 'out')
        self.assertEqual(str(caught.exception), 'project_missing_from_snapshot')
        self.write_snapshot([project('P1', '2002-01-01')], bad_hash=True)
        with self.assertRaises(Refusal) as caught:
            run(self.features, self.snapshot, self.tmp / 'out')
        self.assertEqual(str(caught.exception), 'snapshot_hash_mismatch')


def with_projects(table, signal=True, seed=3):
    rng = random.Random(seed)
    out = []
    for i, r in enumerate(table):
        amount = 7 + r['y'] * 0.8 + rng.gauss(0, 0.3) if signal else 7 + rng.gauss(0, 0.5)
        keep = rng.random() > 0.1 or i == len(table) - 1    # last row always has a value
        out.append(dict(r, log_commitment=f'{amount:.4f}' if keep else '',
                        prep_months=f'{rng.uniform(6, 30):.3f}', appraisal_months='',
                        env_category=rng.choice(['A', 'B', ''])))
    return out


class ProjectsModels(unittest.TestCase):
    setUp = test_ieg_baseline.Baseline.setUp
    write = test_ieg_baseline.Baseline.write

    def write_extra(self, table):
        e = self.tmp / 'extra'
        e.mkdir(exist_ok=True)
        with (e / 'projects-features.csv').open('w', newline='') as s:
            w = csv.DictWriter(s, ['source_project_id', 'split', *ieg_baseline.PROJECTS],
                               extrasaction='ignore')
            w.writeheader()
            w.writerows(table)
        return e

    def test_numeric_signal_is_found_by_M4(self):
        table = with_projects(make_table(signal=False, seed=5))
        f, o = self.write(table)
        report = ieg_baseline.run(f, o, self.tmp / 'out', n_boot=200,
                                  extra_dir=self.write_extra(table))
        m4 = report['models']['M4_projects_only']
        self.assertGreater(m4['auc'], 0.85)
        self.assertTrue(m4['better_than_class_rate'])
        self.assertFalse(report['models']['M1_region_practice']['better_than_class_rate'])
        self.assertIn('auc_difference_M3_minus_M1', report)

    def test_earlier_models_are_unchanged_by_the_extra_features(self):
        table = with_projects(make_table())
        f, o = self.write(table)
        without = ieg_baseline.run(f, o, self.tmp / 'a', n_boot=100)
        withx = ieg_baseline.run(f, o, self.tmp / 'b', n_boot=100,
                                 extra_dir=self.write_extra(table))
        for name in ('M0_train_rate', 'M1_region_practice', 'M2_all_ieg_fields'):
            self.assertEqual(without['models'][name], withx['models'][name])
        self.assertEqual(without['auc_difference_M2_minus_M1'],
                         withx['auc_difference_M2_minus_M1'])

    def test_scaling_is_fitted_on_train_only(self):
        table = with_projects(make_table())
        models = {'M4_projects_only': ieg_baseline.PROJECTS}
        _, _, before = ieg_baseline.evaluate(table, n_boot=5, models=models)
        shifted = [dict(r, log_commitment=f'{float(r["log_commitment"]) + 100:.4f}')
                   if r['split'] == 'test' and r['log_commitment'] and i != len(table) - 1
                   else r for i, r in enumerate(table)]
        _, _, after = ieg_baseline.evaluate(shifted, n_boot=5, models=models)
        self.assertEqual(before['M4_projects_only'][-1], after['M4_projects_only'][-1])

    def test_missing_numeric_uses_the_flag(self):
        table = with_projects(make_table())
        x_train, x_test, vocab = ieg_baseline.encode(
            [r for r in table if r['split'] == 'train'],
            [dict(r, log_commitment='') for r in table if r['split'] == 'test'][:1],
            ['log_commitment'])
        self.assertEqual(vocab, [('log_commitment', 'value'), ('log_commitment', 'missing')])
        self.assertEqual(list(x_test[0]), [0.0, 1.0])
        values = x_train[x_train[:, 1] == 0, 0]
        self.assertAlmostEqual(float(values.mean()), 0.0, places=6)

    def test_mismatched_extra_file_is_refused(self):
        table = with_projects(make_table(n_train=20, n_test=10))
        f, o = self.write(table)
        e = self.write_extra(table[:-1])
        with self.assertRaises(Refusal) as caught:
            ieg_baseline.run(f, o, self.tmp / 'out', n_boot=10, extra_dir=e)
        self.assertEqual(str(caught.exception),
                         'projects_features_list_different_projects_or_splits')
        self.assertFalse((self.tmp / 'out').exists())


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('IEG_PROJECTS_FEATURES_CONTROLS_PASSED')
