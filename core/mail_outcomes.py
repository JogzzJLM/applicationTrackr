"""Outcome rules shared by the two independent trackers; keep the contract tests in sync."""
import re

def latest_message_text(text):
    """Discard reply history and quoted lines; keep an intentionally forwarded message."""
    text = str(text or '').replace('\r\n', '\n')
    boundary = re.search(r"(?im)^\s*(?:on .{5,150}wrote:|[-_]{2,}\s*original message|previous (?:email|message)\s*:|from:.*\n(?:sent:|date:))", text)
    # Plain one-line HTML email exports can still contain an explicit history label.
    boundary = boundary or re.search(r"(?i)\bprevious (?:email|message)\s*:", text)
    if boundary and text[:boundary.start()].strip():
        text = text[:boundary.start()]
    return '\n'.join(line for line in text.splitlines() if not line.lstrip().startswith('>'))


REJECTION_WORDING_REVISION = 2
PROGRESSION_REJECTION = re.compile(
    r"\b(?:unable to|cannot|can't|will not|won't) "
    r"(?:be )?(?:progress(?:ing)?|proceed(?:ing)?|continue|continuing|move|moving)"
    r"(?: forward)?(?: with)? (?:your|the) (?:application|candidacy)(?: further| forward)?\b|"
    r"\b(?:decided|decision) not to (?:progress|proceed|continue|move forward)(?: with)? (?:your|the) (?:application|candidacy)\b|"
    r"\b(?:will not|won't) be taking (?:your|the) application (?:further|forward)\b",
    re.I,
)


def progression_rejection(text):
    """An actual application outcome, not a conditional eligibility warning."""
    text = latest_message_text(text).lower()
    for match in PROGRESSION_REJECTION.finditer(text):
        before = text[max(0, match.start() - 200):match.start()]
        sentence = re.split(r'[.!?\n]', before)[-1]
        if re.search(r'\b(?:if|unless)\b', sentence):
            continue
        if re.search(r"\b(?:not|never|isn't|wasn't|hasn't|haven't)(?:\s+\w+){0,3}\s*$", before[-45:]):
            continue
        return True
    return False


def classify_email_stage(text):
    text_lower = latest_message_text(text).lower()
    # A provider name, newsletter or preparation resource is not a stage event.
    recruitment = re.search(r"\b(?:your application|application (?:for|received|submitted|status|was)|thank you for applying|candidate|recruitment|interview|assessment centre|assessment center|group assessment|group exercise|offer of employment|offer letter|pleased to offer|complete your assessment|assessment invitation|online assessment)\b", text_lower)
    if not recruitment:
        return None
    if progression_rejection(text_lower):
        return "Rejected"
    rejection = re.finditer(r"regret to inform.{0,70}(?:application|candidacy).{0,40}(?:unsuccessful|not (?:been )?successful|rejected)|(?:your |the )?application (?:has |was |is )?(?:not (?:been )?successful|unsuccessful)|unable to offer (?:you )?(?:an? |the )?(?:role|position|job|employment)|not moving forward|(?:proceed|progress|move forward|moving forward) with other candidates|unsuccessful|application was rejected|decided not to proceed|will not be proceeding", text_lower)
    for match in rejection:
        before = text_lower[max(0, match.start()-45):match.start()]
        # Avoid negated outcomes and references to hypothetical rejection.
        if re.search(r"\b(?:not|never|isn't|wasn't|hasn't|haven't)(?:\s+\w+){0,3}\s*$|\b(?:if|unless)(?:\s+\w+){0,4}\s*$", before):
            continue
        return "Rejected"
    offers = re.finditer(r"offer of employment|pleased to offer|congratulations on your offer|formal offer|offer letter|we would like to offer", text_lower)
    for match in offers:
        before = text_lower[max(0, match.start()-45):match.start()]
        if not re.search(r"\b(?:not|no|never|isn't|cannot)(?:\s+\w+){0,3}\s*$|\b(?:if|unless)(?:\s+\w+){0,5}\s*$", before):
            return "Offer"
    if re.search(r"(?:your interview|interview (?:has been|is)).{0,30}\b(?:cancelled|canceled)\b", text_lower):
        return "Application Update"
    # Keep the employer's explicit event type; a group exercise is not an interview.
    assessment_event = re.search(r'\b(assessment cent(?:re|er)|group assessment|group exercise)\b', text_lower)
    if assessment_event and re.search(r'\b(?:invite|inviting|invited|invitation|book|scheduled|confirmed|please attend)\b', text_lower):
        stage = 'Assessment Centre' if assessment_event.group(1).startswith('assessment cent') else 'Group Assessment'
        return explicit_round(stage, text_lower, r'(?:assessment cent(?:re|er)|group assessment|group exercise)')
    invitation = re.search(
        r"\b(?:invitation to (?:an? )?interview|interview invitation|"
        r"(?:invite|inviting|invited) you (?:to|for) (?:an? |your |the |first |second |third |final |technical |video |digital |recorded |online |on-demand ){0,5}interview|"
        r"your (?:[a-z0-9-]+\s+){0,3}interview.{0,35}\b(?:scheduled|confirmed)|"
        r"(?:schedule|book|reschedule|confirm).{0,45}\b(?:an? |your )?interview|"
        r"interview (?:has been |is )?(?:scheduled|confirmed)|"
        r"(?:first|second|third|final) round interview)\b", text_lower, re.S)
    if invitation:
        kind = 'Digital Interview' if re.search(r'\b(?:digital|recorded|on-demand) interview\b', text_lower) else 'Interview'
        return explicit_round(kind, text_lower, r'interview')
    if any(k in text_lower for k in ("online test", "coding assessment", "online assessment", "numerical reasoning", "logic test", "take-home", "complete your assessment", "assessment invitation", "experience platform")):
        # Generic practice/promotional messages must never advance an application.
        if not re.search(r"\b(?:practice|preparation|prepare for|sample|mock)\b", text_lower) or re.search(r"\b(?:invite|invited|invitation|must complete|please complete|your application)\b", text_lower):
            return explicit_round('Online Assessment', text_lower, r'(?:online |coding )?(?:assessment|test)')
    if any(k in text_lower for k in ("thank you for applying", "thank you for your application", "thanks for your application", "application received", "received your application", "confirming your application", "application submitted", "successfully submitted")):
        return "Applied"
    if any(k in text_lower for k in ("application status", "update regarding your", "regarding your application", "technical support")):
        return "Application Update"
    return None




def explicit_round(stage, text, event_pattern):
    """Only numbers/ordinals tied to this event count, not dates or unrelated rounds."""
    ordinal = {'first':1, 'second':2, 'third':3, 'fourth':4, 'fifth':5}
    after = re.search(r'\b' + event_pattern + r'\s+(?:round\s+)?([1-9][0-9]?)(?![0-9])\b(?!\s*(?:days?|hours?|minutes?|weeks?|january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|oct|nov|dec))', text, re.I)
    before = re.search(r'\b(first|second|third|fourth|fifth)\s+(?:round\s+)?' + event_pattern + r'\b', text, re.I)
    number = int(after.group(1)) if after else ordinal[before.group(1).lower()] if before else None
    return f'{stage} {number}' if number else stage
