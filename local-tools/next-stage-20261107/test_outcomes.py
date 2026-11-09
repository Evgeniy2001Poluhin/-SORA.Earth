import copy
import unittest
from outcomes import validate


def sample():
    common = dict(source='synthetic', features_available_at='2019-01-01T00:00:00Z',
                  source_url='https://example.org/fixture', outcome_definition='fixture-only')
    return [dict(common, source_project_id='a', prediction_at='2020-01-01T00:00:00Z',
                 outcome='0', outcome_observed_at='2021-01-01T00:00:00Z', split='train'),
            dict(common, source_project_id='b', prediction_at='2022-01-01T00:00:00Z',
                 outcome='1', outcome_observed_at='2023-01-01T00:00:00Z', split='test')]


class Controls(unittest.TestCase):
    def test_positive(self):
        r = validate(sample())
        self.assertTrue(r['intake_passed'])
        self.assertFalse(r['predictive_quality_validated'])

    def test_negative_controls(self):
        cases = [
            ('features_available_at', '2024-01-01T00:00:00Z', 'features_after_prediction'),
            ('source_project_id', 'a', 'project_repeated'),
            ('outcome', '', 'unknown_outcome_not_evaluable'),
            ('outcome', 'Closed', 'invalid_binary_outcome'),
            ('prediction_at', '2022-01-01', 'invalid_or_naive_timestamp'),
            ('outcome_observed_at', '2020-01-01T00:00:00Z', 'outcome_not_future'),
            ('outcome_definition', 'other', 'one_outcome_definition_required'),
        ]
        for field, value, code in cases:
            with self.subTest(code=code):
                rows = copy.deepcopy(sample())
                rows[1][field] = value
                result = validate(rows)
                self.assertFalse(result['intake_passed'])
                self.assertIn(code, [e['code'] for e in result['errors']])

    def test_train_labels_must_precede_test(self):
        rows = sample()
        rows[0]['outcome_observed_at'] = '2022-01-01T00:00:00Z'
        self.assertIn('training_outcomes_not_available_before_test',
                      [e['code'] for e in validate(rows)['errors']])

    def test_empty_not_accepted(self):
        self.assertFalse(validate([])['intake_passed'])


if __name__ == '__main__':
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Controls))
    if not result.wasSuccessful():
        raise SystemExit(1)
    print('OUTCOME_CONTROLS_PASSED')
