import csv
import hashlib
import json
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ieg_join import Refusal, run, GROUPS_NAME, REPORT_NAME

HERE = Path(__file__).resolve().parent
# Synthetic catalog: D3 is shared by P000003 and P000004; P000002 has two documents;
# D5 has no project; P000009 is catalog-only.
CATALOG = ('id,projectid,docdt,docty\n'
           'D1,P000001,2020-01-01T00:00:00Z,ICR Review\n'
           'D2,P000002,2020-01-01T00:00:00Z,ICR Review\n'
           'D6,P000002,2021-01-01T00:00:00Z,ICR Review\n'
           'D3,"P000003,P000004",2020-01-01T00:00:00Z,ICR Review\n'
           'D5,,2020-01-01T00:00:00Z,ICR Review\n'
           'D7,P000009,2020-01-01T00:00:00Z,ICR Review\n').encode()
# P000001 is rated twice; P000005 is IEG-only; one row has no ID; one outcome is unknown.
IEG = ('Project ID,Outcome,Evaluation FY,As Of Date\n'
       'P000001,Satisfactory,2019,2024-01-01\n'
       'p000001 ,Moderately Unsatisfactory,2021,2024-01-01\n'
       'P000002,Not Rated,2019,2024-01-01\n'
       'P000003,Highly Satisfactory,2019,2024-01-01\n'
       'P000005,Excellent,2019,2024-01-01\n'
       ',Unsatisfactory,2019,2024-01-01\n').encode()


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.catalog = self.tmp / 'catalog'
        self.catalog.mkdir()
        self.write_catalog(CATALOG)
        self.ieg = self.tmp / 'ieg.csv'
        self.ieg.write_bytes(IEG)
        self.out = self.tmp / 'out'
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def write_catalog(self, raw, **manifest):
        (self.catalog / 'documents.csv').write_bytes(raw)
        rows = list(csv.DictReader(raw.decode().splitlines()))
        base = {'documents': len(rows),
                'with_project_id': sum(bool(r['projectid']) for r in rows),
                'csv_sha256': hashlib.sha256(raw).hexdigest(),
                'contains_outcome_ratings': False}
        base.update(manifest)
        (self.catalog / 'manifest.json').write_text(json.dumps(base))

    def refused(self, code):
        with self.assertRaises(Refusal) as caught:
            run(self.ieg, self.catalog, self.out)
        self.assertEqual(str(caught.exception), code)
        self.assertFalse(self.out.exists(), 'a refusal must write nothing')


class Positive(Fixture):
    def test_coverage_counts_and_files(self):
        report = run(self.ieg, self.catalog, self.out)
        cov, groups = report['coverage'], report['groups']
        self.assertEqual(report['inputs']['ieg_csv']['rows'], 6)
        self.assertEqual(report['inputs']['ieg_csv']['unique_project_ids'], 4)
        self.assertEqual(report['inputs']['ieg_csv']['rows_with_empty_project_id'], 1)
        self.assertEqual(report['inputs']['catalog']['documents_without_project_id'], 1)
        self.assertEqual(cov['matched_project_ids'], 3)          # P1, P2, P3
        self.assertEqual(cov['matched_ieg_rows'], 4)              # both P1 rows kept
        self.assertEqual(cov['ieg_ids_missing_from_catalog'], 1)  # P5
        self.assertEqual(cov['catalog_ids_missing_from_ieg'], 2)  # P4, P9
        self.assertEqual(cov['matched_ids_one_row_one_document_unshared'], 0)
        self.assertEqual(cov['matched_ids_needing_manual_review'], 3)
        self.assertEqual(groups['ieg_ids_with_multiple_rows'], 1)
        self.assertEqual(groups['catalog_ids_with_multiple_documents'], 1)
        self.assertEqual(groups['catalog_ids_in_shared_documents'], 2)
        # Raw IEG scale preserved; nothing became 0/1.
        self.assertEqual(report['inputs']['ieg_csv']['outcome_distribution_raw_categories'],
                         {'highly satisfactory': 1, 'moderately unsatisfactory': 1,
                          'not rated': 1, 'satisfactory': 1, 'unrecognized': 1,
                          'unsatisfactory': 1})
        self.assertEqual(report['inputs']['ieg_csv']['unrecognized_outcome_values'],
                         {'Excellent': 1})
        self.assertEqual(report['inputs']['ieg_csv']['date_like_columns_present'],
                         ['as_of_date', 'evaluation_fy'])
        self.assertEqual(report['inputs']['ieg_csv']['sha256'], hashlib.sha256(IEG).hexdigest())
        self.assertFalse(report['binary_labels_created'])
        self.assertFalse(report['training_performed'])
        self.assertEqual(json.loads((self.out / REPORT_NAME).read_text()), report)

    def test_duplicates_are_kept_for_manual_review(self):
        run(self.ieg, self.catalog, self.out)
        with (self.out / GROUPS_NAME).open(newline='') as stream:
            rows = {r['project_id']: r for r in csv.DictReader(stream)}
        self.assertEqual(sorted(rows), ['P000001', 'P000002', 'P000003', 'P000004'])
        self.assertEqual(rows['P000001']['ieg_rows'], '2')
        self.assertEqual(rows['P000001']['reasons'], 'multiple_ieg_rows')
        self.assertEqual(rows['P000002']['catalog_documents'], '2')
        self.assertEqual(rows['P000004']['ieg_rows'], '0')
        self.assertEqual(rows['P000003']['reasons'], 'document_shared_with_other_projects')
        # Only IDs and counts leave the tool: no outcome text, titles or URLs.
        self.assertEqual(set(next(iter(rows.values()))),
                         {'project_id', 'ieg_rows', 'catalog_documents', 'reasons'})

    def test_cli_exit_code_and_refusal_on_existing_output(self):
        cmd = [sys.executable, str(HERE / 'ieg_join.py'), str(self.ieg),
               str(self.catalog), str(self.out)]
        first = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertTrue(json.loads(first.stdout)['join_passed'])
        before = (self.out / REPORT_NAME).read_bytes()
        second = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
        self.assertEqual(second.returncode, 1)
        self.assertEqual(json.loads(second.stdout)['reason_code'], 'output_directory_exists')
        self.assertEqual((self.out / REPORT_NAME).read_bytes(), before)


class DamagedCatalog(Fixture):
    def test_tampered_csv_fails_sha256(self):
        (self.catalog / 'documents.csv').write_bytes(CATALOG.replace(b'P000009', b'P000010'))
        self.refused('catalog_sha256_mismatch')

    def test_broken_manifest(self):
        (self.catalog / 'manifest.json').write_text('{"documents": 6,')
        self.refused('manifest_invalid_json')

    def test_count_and_uniqueness_are_checked(self):
        self.write_catalog(CATALOG, documents=7)
        self.refused('catalog_document_count_mismatch')
        self.write_catalog(CATALOG.replace(b'D7,', b'D1,'))
        self.refused('catalog_duplicate_document_id')

    def test_unclosed_quote_in_catalog_is_refused(self):
        # Hash matches the damaged bytes, so only the strict parser can catch it.
        raw = CATALOG + b'D8,"P000010,2020-01-01T00:00:00Z,ICR Review\n'
        (self.catalog / 'documents.csv').write_bytes(raw)
        manifest = json.loads((self.catalog / 'manifest.json').read_text())
        manifest['csv_sha256'] = hashlib.sha256(raw).hexdigest()
        (self.catalog / 'manifest.json').write_text(json.dumps(manifest))
        self.refused('catalog_malformed_csv')

    def test_catalog_claiming_outcomes_is_refused(self):
        self.write_catalog(CATALOG, contains_outcome_ratings=True)
        self.refused('manifest_must_declare_no_outcome_ratings')


class DamagedIeg(Fixture):
    def test_bad_ieg_files(self):
        cases = {
            b'': 'ieg_csv_empty',
            b'Project ID,Outcome\n': 'ieg_csv_no_rows',
            b'Project ID,project_id,Outcome\nP000001,P000001,Satisfactory\n':
                'ieg_csv_missing_or_ambiguous_columns',
            b'Project,Rating\nP000001,Satisfactory\n': 'ieg_csv_missing_or_ambiguous_columns',
            b'Project ID,Outcome\nP000001,Satisfactory,extra\n': 'ieg_csv_malformed_row',
            b'Project ID,Outcome\nP000001\n': 'ieg_csv_malformed_row',
            b'Project ID,Outcome\nP000001,\xff\xfe\n': 'ieg_csv_not_utf8',
        }
        for raw, code in cases.items():
            with self.subTest(code=code, raw=raw[:30]):
                self.ieg.write_bytes(raw)
                self.refused(code)

    def test_unclosed_or_stray_quote_is_refused(self):
        cases = {
            'first_row': b'Project ID,Outcome\nP000001,"Satisfactory\n',
            # Without strict mode the quote swallows the rest of the file into one
            # field and the good first row hides the damage.
            'second_row': b'Project ID,Outcome\nP000001,Satisfactory\nP000002,"Not Rated\n'
                          b'P000003,Unsatisfactory\n',
            'stray_quote': b'Project ID,Outcome\nP000001,"Satis"factory\n',
        }
        for name, raw in cases.items():
            with self.subTest(name):
                self.ieg.write_bytes(raw)
                self.refused('ieg_csv_malformed_csv')

    def test_missing_file_is_unreadable(self):
        self.ieg = self.tmp / 'absent.csv'
        self.refused('ieg_csv_unreadable')


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(
        unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__]))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('IEG_JOIN_CONTROLS_PASSED')
