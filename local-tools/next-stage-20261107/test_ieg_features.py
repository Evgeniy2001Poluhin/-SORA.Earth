import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from ieg_join import Refusal
from ieg_features import run, FEATURES
from ieg_outcomes import run as build_outcomes

HEADER = ['As of Date', 'Project ID', 'Project Name', 'WB Region', 'Country / Economy',
          'Country / Economy Lending Group', 'Country / Economy FCS Status',
          'Country / Economy FCS Lending Group', 'Practice Group', 'Global Practice',
          'Agreement Type', 'Lending Instrument Type', 'Approval FY', 'Final Closing FY',
          'Evaluation Type', 'Outcome', 'Quality at Entry', 'Quality of Supervision',
          'Bank Performance', 'M&E Quality', 'Evaluation FY']


def row(pid, country, approval, evaluation, outcome='Satisfactory', practice='Infrastructure'):
    return ['11/06/2026', pid, 'NAME-MARKER', 'Region A', country, 'LENDGROUP-MARKER',
            'FCS-MARKER', 'FCSGROUP-MARKER', practice, 'Transport', 'IDA', 'IPF',
            str(approval), 'CLOSING-MARKER', 'ICRR', outcome, 'QAE-MARKER', 'QOS-MARKER',
            'BANK-MARKER', 'ME-MARKER', str(evaluation)]


ROWS = [row('P000001', 'Kenya', 2005, 2012),
        row('P000002', 'Peru', 2006, 2013, 'Unsatisfactory'),
        row('P000003', 'Chile', 2016, 2022),           # country unseen in train
        row('P000004', 'Kenya', 2015, 2021, practice=''),
        row('P000004', 'Kenya', 2015, 2023, practice=''),  # repeated, same features
        row('P000005', 'Peru', 2017, 2024)]               # seen country, so seen != unseen


class Features(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        catalog = self.tmp / 'catalog'
        catalog.mkdir()
        lines = ['id,projectid,docdt,docty'] + [
            f'D{i},P00000{i},2020-01-01T00:00:00Z,Implementation Completion Report Review'
            for i in range(1, 6)]
        raw = ('\n'.join(lines) + '\n').encode()
        (catalog / 'documents.csv').write_bytes(raw)
        (catalog / 'manifest.json').write_text(json.dumps({
            'documents': 5, 'with_project_id': 5, 'csv_sha256': hashlib.sha256(raw).hexdigest(),
            'contains_outcome_ratings': False}))
        self.ieg = self.tmp / 'ieg.csv'
        self.write(ROWS)
        self.outcomes = self.tmp / 'outcomes'
        build_outcomes(self.ieg, catalog, self.outcomes, 2014)
        self.out = self.tmp / 'out'

    def write(self, rows):
        with self.ieg.open('w', newline='') as stream:
            csv.writer(stream).writerows([HEADER, *rows])

    def test_admitted_values_and_nothing_else(self):
        report = run(self.ieg, self.outcomes, self.out)
        text = (self.out / 'features.csv').read_text()
        self.assertNotIn('MARKER', text)          # evaluation-time and snapshot columns absent
        with (self.out / 'features.csv').open(newline='') as stream:
            reader = csv.DictReader(stream)
            self.assertEqual(reader.fieldnames, ['source_project_id', 'split', *FEATURES])
            rows = {r['source_project_id']: r for r in reader}
        self.assertEqual(rows['P000002']['country'], 'Peru')
        self.assertEqual(rows['P000003']['split'], 'test')
        self.assertEqual(rows['P000001']['approval_fy'], '2005')
        self.assertEqual(report['profile']['country']['test_rows_with_value_unseen_in_train'], 1)
        self.assertEqual(report['profile']['practice_group']['empty_values'], 1)
        self.assertEqual(report['excluded_snapshot_columns'],
                         ['Country / Economy Lending Group', 'Country / Economy FCS Status',
                          'Country / Economy FCS Lending Group'])
        self.assertFalse(report['outcome_column_included'])
        self.assertFalse(report['training_performed'])

    def test_a_different_ieg_file_is_refused(self):
        self.write(ROWS + [row('P000009', 'Chad', 2005, 2012)])
        with self.assertRaises(Refusal) as caught:
            run(self.ieg, self.outcomes, self.out)
        self.assertEqual(str(caught.exception), 'ieg_csv_is_not_the_source_of_the_outcomes')
        self.assertFalse(self.out.exists())

    def test_repeated_rows_with_different_features_are_refused(self):
        changed = ROWS[:4] + [row('P000004', 'Uganda', 2015, 2023, practice=''), ROWS[5]]
        self.write(changed)
        build = json.loads((self.outcomes / 'build-report.json').read_text())
        build['inputs']['ieg_csv_sha256'] = hashlib.sha256(self.ieg.read_bytes()).hexdigest()
        (self.outcomes / 'build-report.json').write_text(json.dumps(build))
        with self.assertRaises(Refusal) as caught:
            run(self.ieg, self.outcomes, self.out)
        self.assertEqual(str(caught.exception), 'repeated_project_rows_disagree_on_features')

    def test_existing_output_is_refused(self):
        self.out.mkdir()
        with self.assertRaises(Refusal) as caught:
            run(self.ieg, self.outcomes, self.out)
        self.assertEqual(str(caught.exception), 'output_directory_exists')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('IEG_FEATURES_CONTROLS_PASSED')
