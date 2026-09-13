"""Shared comparison response fixture for the offline runner tests."""

from check_diff.schema import DIMENSIONS


def annotation():
    return {
        'prelim': {
            'A1': {'value': 'comparable', 'justification': 'Both versions are readable and describe the same work.'},
            'A2': {side: {'value': 'yes', 'justification': 'Reports original experimental results.'}
                   for side in ('before', 'after')},
        },
        **{d: {'change': 'no change', 'justification': 'The content is unchanged.'} for d in DIMENSIONS},
        'improvement': {'change': 'no meaningful change',
                        'justification': 'The versions have no meaningful overall quality difference.'},
    }
