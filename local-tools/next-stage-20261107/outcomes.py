"""Offline intake checks; no training, row output, or quality acceptance."""
import argparse
import csv
from datetime import datetime
import json
from pathlib import Path

FIELDS = ('source', 'source_project_id', 'prediction_at', 'features_available_at',
          'outcome', 'outcome_observed_at', 'source_url', 'outcome_definition', 'split')


def timestamp(value):
    result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError('timezone required')
    return result


def validate(rows):
    errors, seen, definitions = [], set(), set()
    latest_train, earliest_test = None, None
    unknown = 0
    for line, row in enumerate(rows, 2):
        def fail(code):
            errors.append({'line': line, 'code': code})
        if any(not str(row.get(k, '')).strip() for k in FIELDS if k != 'outcome'):
            fail('missing_required_field')
            continue
        key = (row['source'].strip(), row['source_project_id'].strip())
        if key in seen:
            fail('project_repeated')
        seen.add(key)
        definitions.add(row['outcome_definition'].strip())
        if row['outcome'] == '':
            unknown += 1
            fail('unknown_outcome_not_evaluable')
        elif row['outcome'] not in ('0', '1'):
            fail('invalid_binary_outcome')
        if not row['source_url'].startswith('https://'):
            fail('source_url_requires_https')
        if row['split'] not in ('train', 'test'):
            fail('invalid_split')
        try:
            prediction = timestamp(row['prediction_at'])
            available = timestamp(row['features_available_at'])
            observed = timestamp(row['outcome_observed_at'])
        except (ValueError, TypeError):
            fail('invalid_or_naive_timestamp')
            continue
        if available > prediction:
            fail('features_after_prediction')
        if observed <= prediction:
            fail('outcome_not_future')
        if row['split'] == 'train':
            latest_train = max(latest_train, observed) if latest_train else observed
        elif row['split'] == 'test':
            earliest_test = min(earliest_test, prediction) if earliest_test else prediction
    if len(definitions) != 1:
        errors.append({'code': 'one_outcome_definition_required'})
    if latest_train is None or earliest_test is None:
        errors.append({'code': 'train_and_test_required'})
    elif latest_train >= earliest_test:
        errors.append({'code': 'training_outcomes_not_available_before_test'})
    return {'intake_passed': not errors, 'row_count': len(rows),
            'unknown_outcomes': unknown, 'errors': errors,
            'predictive_quality_validated': False,
            'limitation': 'Declared timestamps only; source authenticity, per-feature provenance and agreed target require independent review.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv_path', type=Path)
    args = parser.parse_args()
    with args.csv_path.open(newline='', encoding='utf-8-sig') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(FIELDS):
            print(json.dumps({'intake_passed': False, 'errors': [{'code': 'invalid_columns'}]}))
            return 1
        rows = list(reader)
    result = validate(rows)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['intake_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
