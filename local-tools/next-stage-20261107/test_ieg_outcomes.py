import csv
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from ieg_join import Refusal
from ieg_outcomes import run
from outcomes import validate

T = 2014
DOCS = {  # document id -> listed projects
    'D01': 'P000001', 'D02': 'P000002', 'D03': 'P000003', 'D04': 'P000004',
    'D05': 'P000005', 'D06': 'P000006', 'D07': 'P000007', 'D08': 'P000008',
    'D09': 'P000011,P000012',   # cluster, agrees, all train -> kept
    'D10': 'P000021,P000022',   # cluster, ratings differ -> excluded
    'D11': 'P000031,P000032',   # cluster straddles T -> excluded
    'D12': 'P000041,P000042',   # one member is PPAR -> whole cluster excluded
    'D13': 'P000009', 'D14': 'P000010',
}
IEG_ROWS = [  # id, type, outcome, approval FY, evaluation FY
    ('P000001', 'ICRR', 'Moderately Satisfactory', 2005, 2012),   # train, 1
    ('P000002', 'ICRR', 'Moderately Unsatisfactory', 2006, 2013), # train, 0
    ('P000003', 'ICRR', 'Satisfactory', 2015, 2022),              # test, 1
    ('P000004', 'PPAR', 'Satisfactory', 2005, 2012),              # P3
    ('P000005', 'ICRR', 'Not Rated', 2005, 2012),                 # P5
    ('P000006', 'ICRR', 'Satisfactory', 2010, 2016),              # gap
    ('P000007', 'ICRR', 'Unsatisfactory', 2004, 2010),            # P1 agree, latest FY 2013
    ('P000007', 'ICRR', 'Unsatisfactory', 2004, 2013),
    ('P000008', 'ICRR', 'Satisfactory', 2004, 2010),              # P1 disagree
    ('P000008', 'ICRR', 'Unsatisfactory', 2004, 2011),
    ('P000009', 'ICRR', 'Highly Unsatisfactory', 2016, 2023),     # test, 0
    ('P000011', 'ICRR', 'Satisfactory', 2003, 2010),
    ('P000012', 'ICRR', 'Satisfactory', 2004, 2011),
    ('P000021', 'ICRR', 'Satisfactory', 2003, 2010),
    ('P000022', 'ICRR', 'Unsatisfactory', 2003, 2010),
    ('P000031', 'ICRR', 'Satisfactory', 2003, 2010),
    ('P000032', 'ICRR', 'Satisfactory', 2015, 2021),
    ('P000041', 'ICRR', 'Satisfactory', 2003, 2010),
    ('P000042', 'PPAR', 'Satisfactory', 2003, 2010),
    ('P000099', 'ICRR', 'Satisfactory', 2003, 2010),              # not in catalog
    ('P000010', 'ICRR', 'Satisfactory', 2014, 2013),              # evaluation before approval
]


class Build(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.catalog = self.tmp / 'catalog'
        self.catalog.mkdir()
        lines = ['id,projectid,docdt,docty'] + [
            f'{d},"{p}",2020-01-01T00:00:00Z,Implementation Completion Report Review'
            for d, p in DOCS.items()]
        raw = ('\n'.join(lines) + '\n').encode()
        (self.catalog / 'documents.csv').write_bytes(raw)
        (self.catalog / 'manifest.json').write_text(json.dumps({
            'documents': len(DOCS), 'with_project_id': len(DOCS),
            'csv_sha256': hashlib.sha256(raw).hexdigest(), 'contains_outcome_ratings': False}))
        self.ieg = self.tmp / 'ieg.csv'
        self.write_ieg(IEG_ROWS)
        self.out = self.tmp / 'out'

    def write_ieg(self, rows):
        with self.ieg.open('w', newline='') as stream:
            writer = csv.writer(stream)
            writer.writerow(['Project ID', 'Evaluation Type', 'Outcome', 'Approval FY',
                             'Evaluation FY', 'Quality at Entry'])
            writer.writerows([*r, 'QAE-MARKER'] for r in rows)

    def outcomes(self):
        with (self.out / 'outcomes.csv').open(newline='') as stream:
            return {r['source_project_id']: r for r in csv.DictReader(stream)}

    def test_each_rule_lands_where_approved(self):
        report = run(self.ieg, self.catalog, self.out, T)
        rows = self.outcomes()
        self.assertEqual({p: (r['split'], r['outcome']) for p, r in rows.items()}, {
            'P000001': ('train', '1'), 'P000002': ('train', '0'), 'P000003': ('test', '1'),
            'P000007': ('train', '0'), 'P000009': ('test', '0'),
            'P000011': ('train', '1'), 'P000012': ('train', '1')})
        self.assertEqual(report['excluded_projects_by_reason'], {
            'P1_repeated_rows_disagree': 1, 'P2_cluster_member_excluded': 1,
            'P2_cluster_ratings_disagree': 2, 'P2_cluster_straddles_threshold': 2,
            'P3_not_icrr': 2, 'P5_not_an_outcome': 1, 'evaluation_not_after_approval': 1,
            'not_in_catalog_cluster_unknown': 1, 'protocol_gap': 1})
        # P1: the later of the repeated evaluations bounds when the outcome was known.
        self.assertEqual(rows['P000007']['outcome_observed_at'], '2013-06-30T23:59:59+00:00')
        self.assertEqual(rows['P000003']['prediction_at'], '2015-06-30T23:59:59+00:00')
        self.assertTrue(report['outcomes_py_intake_passed'])
        self.assertFalse(report['features_produced'])
        self.assertFalse(report['training_performed'])
        with (self.out / 'units.csv').open(newline='') as stream:
            units = {u['source_project_id']: u for u in csv.DictReader(stream)}
        self.assertEqual(units['P000012']['cluster_id'], 'P000011')
        self.assertEqual(units['P000007']['repeated_ieg_rows'], 'yes')

    def test_output_passes_the_independent_intake_check(self):
        run(self.ieg, self.catalog, self.out, T)
        with (self.out / 'outcomes.csv').open(newline='') as stream:
            result = validate(list(csv.DictReader(stream)))
        self.assertTrue(result['intake_passed'], result['errors'])

    def test_no_evaluation_quality_column_leaks_into_output(self):
        run(self.ieg, self.catalog, self.out, T)
        for name in ('outcomes.csv', 'units.csv'):
            self.assertNotIn('qae-marker', (self.out / name).read_text().casefold())

    def test_refusals(self):
        self.write_ieg([])
        (self.tmp / 'x').write_text('Project ID,Outcome\nP000001,Satisfactory\n')
        with self.assertRaises(Refusal) as caught:
            run(self.tmp / 'x', self.catalog, self.out, T)
        self.assertEqual(str(caught.exception), 'ieg_csv_missing_or_ambiguous_protocol_columns')
        self.write_ieg(IEG_ROWS)
        self.out.mkdir()
        with self.assertRaises(Refusal) as caught:
            run(self.ieg, self.catalog, self.out, T)
        self.assertEqual(str(caught.exception), 'output_directory_exists')
        self.assertEqual(list(self.out.iterdir()), [])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('IEG_OUTCOMES_CONTROLS_PASSED')
