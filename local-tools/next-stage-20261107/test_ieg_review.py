import csv
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from ieg_join import Refusal
from ieg_review import run, TABLE_NAME

# D3 joins P3+P4 and D9 joins P4+P5: one cluster of three only through transitivity.
CATALOG = ('id,projectid,docdt,docty\n'
           'D1,P000001,2020-01-01T00:00:00Z,Implementation Completion Report Review\n'
           'D2,P000002,2020-01-01T00:00:00Z,Implementation Completion Report Review\n'
           'D6,P000002,2021-01-01T00:00:00Z,Implementation Completion Report Review\n'
           'D3,"P000003,P000004",2020-01-01T00:00:00Z,Implementation Completion Report Review\n'
           'D9,"P000005,P000004",2020-01-01T00:00:00Z,Implementation Completion Report Review\n'
           'D7,P000007,2020-01-01T00:00:00Z,Implementation Completion Report Review\n'
           'D8,P000008,2020-01-01T00:00:00Z,Implementation Completion Report Review\n').encode()
IEG = ('Project ID,Evaluation Type,Outcome\n'
       'P000001,ICRR,Satisfactory\n'
       'P000001,ICRR,Satisfactory\n'          # duplicate, same rating
       'P000002,PPAR,Unsatisfactory\n'        # rating not from an ICR Review; two documents
       'P000003,ICRR,Satisfactory\n'
       'P000004,ICRR,Satisfactory\n'
       'P000005,ICRR,Unsatisfactory\n'        # cluster disagrees
       'P000007,ICRR,Moderately Satisfactory\n'
       'P000008,ICRR,Satisfactory\n'
       'P000008,ICRR,Not Rated\n'             # duplicate, ratings differ
       'P000099,ICRR,Satisfactory\n').encode()  # not in catalog


class Review(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.catalog = self.tmp / 'catalog'
        self.catalog.mkdir()
        (self.catalog / 'documents.csv').write_bytes(CATALOG)
        (self.catalog / 'manifest.json').write_text(json.dumps({
            'documents': 7, 'with_project_id': 7,
            'csv_sha256': hashlib.sha256(CATALOG).hexdigest(),
            'contains_outcome_ratings': False}))
        self.ieg = self.tmp / 'ieg.csv'
        self.ieg.write_bytes(IEG)
        self.out = self.tmp / 'out'

    def table(self):
        with (self.out / TABLE_NAME).open(newline='') as stream:
            return {r['project_id']: r for r in csv.DictReader(stream)}

    def test_flags_per_project(self):
        report = run(self.ieg, self.catalog, self.out)
        rows = self.table()
        self.assertNotIn('P000099', rows)
        self.assertEqual(rows['P000007']['status'], 'one_to_one_same_type')
        self.assertEqual(rows['P000001']['reasons'], 'multiple_ieg_rows')
        self.assertEqual(rows['P000001']['ieg_outcomes_agree'], 'yes')
        self.assertEqual(rows['P000008']['ieg_outcomes_agree'], 'no')
        self.assertEqual(rows['P000002']['reasons'],
                         'multiple_documents;evaluation_type_differs_from_document')
        for p in ('P000003', 'P000004', 'P000005'):
            self.assertEqual(rows[p]['cluster_id'], 'P000003')
            self.assertEqual(rows[p]['cluster_size'], '3')
            self.assertEqual(rows[p]['cluster_outcomes_agree'], 'no')
        review = report['review']
        self.assertEqual(review['matched_project_ids'], 7)
        self.assertEqual(review['status'], {'needs_review': 6, 'one_to_one_same_type': 1})
        self.assertEqual(review['shared_document_clusters'], 1)
        self.assertEqual(review['shared_document_clusters_outcomes_disagree'], 1)
        self.assertEqual(review['ieg_duplicate_ids_outcomes_disagree'], 1)
        self.assertFalse(report['decisions_made'])
        self.assertFalse(report['binary_labels_created'])

    def test_only_ids_counts_and_flags_leave_the_tool(self):
        run(self.ieg, self.catalog, self.out)
        text = (self.out / TABLE_NAME).read_text().casefold()
        for word in ('satisfactory', 'not rated'):
            self.assertNotIn(word, text)

    def test_verification_of_inputs_is_inherited(self):
        (self.catalog / 'documents.csv').write_bytes(CATALOG.replace(b'D7,', b'D0,'))
        with self.assertRaises(Refusal) as caught:
            run(self.ieg, self.catalog, self.out)
        self.assertEqual(str(caught.exception), 'catalog_sha256_mismatch')
        self.assertFalse(self.out.exists())
        self.out.mkdir()
        with self.assertRaises(Refusal) as caught:
            run(self.ieg, self.catalog, self.out)
        self.assertEqual(str(caught.exception), 'output_directory_exists')


if __name__ == '__main__':
    import sys
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('IEG_REVIEW_CONTROLS_PASSED')
