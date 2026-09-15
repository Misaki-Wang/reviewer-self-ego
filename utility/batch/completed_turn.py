"""Strict, side-effect-free evidence rules for a completed turn in a failed CLI."""
import json


KIND = 'completed-model-turn-v1'
WARNING = ('Recovered a completed model turn from a failed CLI process. Original exit code, '
           'timeout and error remain in the immutable receipt; this is not a successful CLI exit.')


def decode(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f'Duplicate JSON key: {key}')
            result[key] = value
        return result

    def invalid(value):
        raise ValueError(f'Invalid JSON constant: {value}')

    return json.loads(data, object_pairs_hook=unique, parse_constant=invalid)


def completed_turn(raw, data):
    """Return exact reported usage only after the entire terminal transcript proves it."""
    events = [(number, decode(line)) for number, line in enumerate(data.splitlines(), 1) if line.strip()]
    if not events or any(not isinstance(e, dict) for _, e in events):
        raise ValueError('Missing or malformed event stream')
    if (events[-1][1].get('type') != 'turn.completed'
            or sum(e.get('type') == 'turn.completed' for _, e in events) != 1
            or any(e.get('type') == 'turn.failed' for _, e in events)):
        raise ValueError('Expected exactly one final completed turn and no failed turns')
    messages = [e for _, e in events if isinstance(e.get('item'), dict)
                and e['item'].get('type') == 'agent_message']
    if (not messages or messages[-1].get('type') != 'item.completed'
            or not isinstance(messages[-1]['item'].get('text'), str)
            or decode(messages[-1]['item']['text']) != decode(raw)):
        raise ValueError('Raw response differs from the last completed agent message')
    usage = events[-1][1].get('usage')
    fields = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
              'output_tokens', 'reasoning_output_tokens')
    if (not isinstance(usage, dict) or not {'input_tokens', 'output_tokens'} <= usage.keys()
            or any(type(usage[k]) is not int or usage[k] < 0 for k in fields if k in usage)
            or usage.get('cached_input_tokens', 0) > usage['input_tokens']
            or usage.get('reasoning_output_tokens', 0) > usage['output_tokens']):
        raise ValueError('Missing or invalid reported usage')
    return {'source': 'codex_exec_turn_completed',
            'records': [{'event_line': events[-1][0], 'usage': usage}], 'errors': []}


def eligible_failure(receipt):
    # Interrupted/coordinator-cancelled attempts are deliberately ineligible, even
    # if cancellation happened just after model completion.
    return (receipt.get('status') == 'failed' and type(receipt.get('exit_code')) is int
            and (receipt['exit_code'] != 0 or receipt.get('timed_out') is True)
            and receipt.get('terminal_error') is None
            and not receipt.get('cancelled') and not receipt.get('user_cancelled')
            and not any(word in str(receipt.get('error', '')).lower()
                        for word in ('cancel', 'interrupt')))
