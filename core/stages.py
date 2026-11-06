"""Application events: stable names, explicit rounds, and shared progression rules."""
import re
from html import unescape

STAGE_NAMES = (
    'Applied', 'Online Assessment', 'Group Assessment', 'Assessment Centre',
    'Phone Screen', 'Digital Interview', 'Technical Interview', 'Interview',
    'Final Interview', 'Offer', 'Offer Accepted', 'Rejected', 'Withdrawn', 'Ghosted', 'Reopened',
)
ROUND_STAGES = set(STAGE_NAMES[1:9])
_ALIASES = {name.casefold(): name for name in STAGE_NAMES}
_ALIASES.update({'oa': 'Online Assessment', 'assessment': 'Online Assessment',
    'assessment center': 'Assessment Centre', 'group exercise': 'Group Assessment',
    'phone interview': 'Phone Screen', 'application received': 'Applied',
    'application submitted': 'Applied', 'accepted': 'Offer Accepted', 'reject': 'Rejected'})


def canonical_stage(value):
    """Normalize labels without inventing a next round or changing custom labels."""
    text = ' '.join(unescape(str(value or '')).split())
    match = re.fullmatch(r'(.+?)\s+(?:round\s+)?([1-9][0-9]?)', text, re.I)
    base, number = (match.group(1), int(match.group(2))) if match else (text, None)
    name = _ALIASES.get(base.casefold(), base)
    return f'{name} {number}' if number is not None else name


def valid_stage(value, custom=False):
    if not isinstance(value, str) or not value or len(value) > 80 or re.search(r'[\x00-\x1f\x7f]', value):
        return False
    stage = canonical_stage(value)
    if stage in STAGE_NAMES:
        return True
    numbered = re.fullmatch(r'(.+) ([1-9][0-9]?)', stage)
    if numbered and numbered.group(1) in ROUND_STAGES:
        return True
    # An explicit Other choice allows a plain-text employer-specific event.
    # Formula-leading characters and markup are never accepted as Sheet values.
    return bool(custom and re.fullmatch(r'[A-Za-z][A-Za-z0-9 &(),./:+\-]{1,79}', stage))


def stage_key(value):
    stage = canonical_stage(value)
    numbered = re.fullmatch(r'(.+) ([1-9][0-9]?)', stage)
    if numbered and numbered.group(1) in ROUND_STAGES:
        return numbered.group(1).casefold(), int(numbered.group(2))
    return stage.casefold(), 1


def stage_recorded(stage, history):
    """A reminder is not another round; only explicit different rounds advance."""
    stage = canonical_stage(stage)
    if stage == 'Reopened':
        return bool(history and canonical_stage(history[-1]) == 'Reopened')
    reopened = [i for i, old in enumerate(history) if canonical_stage(old) == 'Reopened']
    if reopened:
        history = history[reopened[-1] + 1:]
    if any(stage_key(stage) == stage_key(old) for old in history):
        return True
    if stage in ROUND_STAGES:
        if any(stage_key(old)[0] == stage.casefold() for old in history):
            return True
        if stage == 'Interview' and any('interview' in stage_key(old)[0] for old in history):
            return True
    return False


def stage_phase(value):
    stage = canonical_stage(value).lower()
    if any(word in stage for word in ('reject', 'fail', 'offer', 'withdraw', 'ghost')):
        return 3
    if any(word in stage for word in ('interview', 'phone screen', 'group assessment', 'assessment centre')):
        return 2
    if any(word in stage for word in ('assessment', 'test')) or stage == 'oa':
        return 1
    return 0


def email_stage_conflicts(stage, history):
    """Automatic mail may neither reopen a finished role nor move a round backwards."""
    if not history or stage_recorded(stage, history) or stage == 'Application Update':
        return False
    reopened = [i for i, old in enumerate(history) if canonical_stage(old) == 'Reopened']
    if reopened:
        history = history[reopened[-1]:]
    latest = history[-1]
    if not valid_stage(latest):
        return True  # A custom event has no reliable automatic progression order.
    if stage_phase(latest) == 3 or stage_phase(stage) < stage_phase(latest):
        return True
    family, number = stage_key(stage)
    return any(old_family == family and old_number > number for old_family, old_number in map(stage_key, history))


def compact_stage_history(history):
    """Collapse adjacent repeats, retaining distinct rounds and intentional later events."""
    result = []
    for value in history:
        stage = canonical_stage(value)
        if stage and (not result or stage_key(stage) != stage_key(result[-1])):
            result.append(stage)
    return result
