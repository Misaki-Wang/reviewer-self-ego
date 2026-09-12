"""A1, per-version A2, and fourteen change judgments for one PDF pair."""
from copy import deepcopy

DIMENSIONS = ('M1', 'E1', 'E2', 'E3', 'E4', 'E5', 'E6', 'T1', 'C1', 'C2', 'D1', 'R1', 'P1', 'P2')
LABELS = ('major change', 'minor change', 'maybe', 'no change')
COMPARABILITY = ('comparable', 'partially_comparable', 'unable_to_determine')
EMPIRICAL_RESULTS = ('yes', 'no', 'uncertain')


def object_schema(properties):
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


def judgment(field, labels):
    return object_schema({field: {'type': 'string', 'enum': list(labels)},
                          'justification': {'type': 'string'}})


SCHEMA = object_schema({
    'overall': object_schema({
        'A1': judgment('value', COMPARABILITY),
        'A2': object_schema({side: judgment('value', EMPIRICAL_RESULTS) for side in ('before', 'after')}),
    }),
    **{dimension: judgment('change', LABELS) for dimension in DIMENSIONS},
})


def _validate(value, schema, path):
    if schema['type'] == 'object':
        if not isinstance(value, dict) or set(value) != set(schema['properties']):
            raise ValueError(f'{path}: expected exactly {list(schema["properties"])}')
        for key, child_schema in schema['properties'].items():
            _validate(value[key], child_schema, f'{path}.{key}')
    else:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f'{path}: expected a nonempty string')
        if 'enum' in schema and value not in schema['enum']:
            raise ValueError(f'{path}: invalid label {value!r}')


def validate_result(result):
    _validate(result, SCHEMA, 'result')


def normalize_result_labels(result):
    """Return a validated lowercase-label copy; leave historical input untouched."""
    normalized = deepcopy(result)
    if isinstance(normalized, dict):
        for dimension in DIMENSIONS:
            item = normalized.get(dimension)
            if isinstance(item, dict) and isinstance(item.get('change'), str):
                item['change'] = item['change'].lower()
    validate_result(normalized)
    return normalized
