import csv
import json
from pathlib import Path
import random
import shutil
import sys
import tempfile
import unittest

from ieg_join import Refusal
from ieg_baseline import cluster_resamples, decide, evaluate, run

FEATURES = ['country', 'region', 'agreement_type', 'lending_instrument_type', 'approval_fy',
            'practice_group', 'global_practice']


def make_table(n_train=300, n_test=200, signal=True, seed=1):
    """Region decides the outcome when signal is on; otherwise outcomes are coin flips."""
    rng = random.Random(seed)
    table = []
    for i in range(n_train + n_test):
        split = 'train' if i < n_train else 'test'
        region = rng.choice(['North', 'South'])
        y = (region == 'North') if signal and rng.random() < 0.9 else rng.random() < 0.5
        table.append({'source_project_id': f'P{i:06d}', 'split': split,
                      'country': rng.choice(['A', 'B', 'C']), 'region': region,
                      'agreement_type': 'IDA', 'lending_instrument_type': 'IPF',
                      'approval_fy': str(2000 + i % 10 if split == 'train' else 2015 + i % 5),
                      'practice_group': rng.choice(['X', 'Y', '']), 'global_practice': 'G',
                      'y': int(y), 'cluster': f'P{i - i % 2:06d}'})   # clusters of two
    return table


class Baseline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write(self, table, sha='abc'):
        f, o = self.tmp / 'features', self.tmp / 'outcomes'
        for d in (f, o):
            d.mkdir(exist_ok=True)
        with (f / 'features.csv').open('w', newline='') as s:
            w = csv.DictWriter(s, ['source_project_id', 'split', *FEATURES], extrasaction='ignore')
            w.writeheader()
            w.writerows(table)
        with (o / 'outcomes.csv').open('w', newline='') as s:
            w = csv.writer(s)
            w.writerow(['source_project_id', 'split', 'outcome'])
            w.writerows([r['source_project_id'], r['split'], r['y']] for r in table)
        with (o / 'units.csv').open('w', newline='') as s:
            w = csv.writer(s)
            w.writerow(['source_project_id', 'cluster_id'])
            w.writerows([r['source_project_id'], r['cluster']] for r in table)
        (f / 'feature-report.json').write_text(json.dumps({'inputs': {'ieg_csv_sha256': 'abc'}}))
        (o / 'build-report.json').write_text(json.dumps({'inputs': {'ieg_csv_sha256': sha},
                                                         'threshold_fy': 2014}))
        return f, o

    def test_signal_is_found_and_beats_the_class_rate(self):
        report = run(*self.write(make_table()), self.tmp / 'out', n_boot=300)
        m2 = report['models']['M2_all_ieg_fields']
        self.assertGreater(m2['auc'], 0.85)
        self.assertGreater(m2['auc_ci95'][0], 0.5)
        self.assertTrue(m2['better_than_class_rate'])
        self.assertEqual(report['models']['M0_train_rate']['auc'], 0.5)
        self.assertTrue((self.tmp / 'out' / 'test-predictions.csv').exists())

    def test_noise_is_not_called_better(self):
        summary, _, _ = evaluate(make_table(signal=False, seed=7), n_boot=300)
        for name in ('M1_region_practice', 'M2_all_ieg_fields'):
            self.assertFalse(summary['models'][name]['better_than_class_rate'])
            self.assertLessEqual(summary['models'][name]['auc_ci95'][0], 0.5)

    def test_test_labels_do_not_reach_the_fit(self):
        table = make_table()
        _, _, before = evaluate(table, n_boot=10)
        flipped = [dict(r, y=1 - r['y']) if r['split'] == 'test' else r for r in table]
        _, _, after = evaluate(flipped, n_boot=10)
        for name in before:
            self.assertEqual(list(before[name]), list(after[name]))

    def test_approval_fy_is_not_used(self):
        table = make_table()
        _, _, before = evaluate(table, n_boot=10)
        changed = [dict(r, approval_fy=str(1900 + r['y'])) for r in table]   # would leak
        _, _, after = evaluate(changed, n_boot=10)
        for name in before:
            self.assertEqual(list(before[name]), list(after[name]))

    def test_unseen_value_counts_as_empty(self):
        table = make_table()
        probe = dict(table[-1], country='NEVER-IN-TRAIN')
        blank = dict(table[-1], country='')
        _, _, a = evaluate(table[:-1] + [probe], n_boot=10)
        _, _, b = evaluate(table[:-1] + [blank], n_boot=10)
        self.assertEqual(a['M2_all_ieg_fields'][-1], b['M2_all_ieg_fields'][-1])

    def test_resamples_keep_clusters_whole(self):
        clusters = ['a', 'a', 'b', 'c', 'c', 'c']
        for idx in cluster_resamples(clusters, 50, 3):
            picked = [clusters[i] for i in idx]
            for c in set(picked):
                self.assertEqual(picked.count(c) % clusters.count(c), 0)
            for c in set(picked):   # members of a drawn cluster appear together
                members = [i for i, x in enumerate(clusters) if x == c]
                self.assertTrue(all(m in idx for m in members))

    def test_decision_needs_both_lower_bounds(self):
        self.assertTrue(decide([0.55, 0.7], [0.01, 0.1]))
        self.assertFalse(decide([0.50, 0.7], [0.01, 0.1]))    # AUC bound at chance
        self.assertFalse(decide([0.55, 0.7], [0.0, 0.1]))     # Brier skill bound at zero
        self.assertFalse(decide([0.45, 0.7], [-0.1, 0.1]))

    def test_refusals(self):
        table = make_table(n_train=20, n_test=10)
        f, o = self.write(table, sha='other')
        with self.assertRaises(Refusal) as caught:
            run(f, o, self.tmp / 'out', n_boot=10)
        self.assertEqual(str(caught.exception), 'features_and_outcomes_come_from_different_ieg_files')
        self.write(table)
        (o / 'outcomes.csv').write_text('source_project_id,split,outcome\nP000000,train,1\n')
        with self.assertRaises(Refusal) as caught:
            run(f, o, self.tmp / 'out', n_boot=10)
        self.assertEqual(str(caught.exception), 'features_and_outcomes_list_different_projects')
        swapped = [dict(r, split='test') if i == 0 else r for i, r in enumerate(table)]
        f, o = self.write(table)
        with (f / 'features.csv').open('w', newline='') as s:
            w = csv.DictWriter(s, ['source_project_id', 'split', *FEATURES], extrasaction='ignore')
            w.writeheader()
            w.writerows(swapped)
        with self.assertRaises(Refusal) as caught:
            run(f, o, self.tmp / 'out', n_boot=10)
        self.assertEqual(str(caught.exception), 'features_and_outcomes_disagree_on_split')
        self.write(table)
        (self.tmp / 'out').mkdir()
        with self.assertRaises(Refusal) as caught:
            run(f, o, self.tmp / 'out', n_boot=10)
        self.assertEqual(str(caught.exception), 'output_directory_exists')
        self.assertEqual(list((self.tmp / 'out').iterdir()), [])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('IEG_BASELINE_CONTROLS_PASSED')
