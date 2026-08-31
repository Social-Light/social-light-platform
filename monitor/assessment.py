"""The public media intelligence assessment.

A visitor answers ten questions about how they monitor their own coverage, and
gets a score and a short read of what it means. Six of the questions carry
points. The rest carry no score at all and exist to tell whoever follows up what
this organisation actually needs.

The questions live here rather than in the template because the same definitions
have to do two jobs: render the page, and score a submission after the fact. A
second copy in JavaScript would let the browser decide its own score, which is
precisely what a scored form must not allow. The browser gets these definitions
as JSON and may show a running score for feedback, but the number stored against
a lead is the one computed here, from the answers that were posted.

Question wording, option wording and the scoring weights are supplied by the
business and are reproduced verbatim.

Answers are stored keyed by the question's ``key``, never by its position, so
inserting a question later does not silently re-interpret every submission that
came before it.
"""
MULTIPLE_CHOICE = 'mc'
MULTI_SELECT = 'multi'
YES_NO = 'yn'
FREE_TEXT = 'text'


QUESTIONS = [
    {
        'key': 'setup',
        'section': 'Your Current Reality',
        'type': MULTIPLE_CHOICE,
        'text': "How would you describe your brand's current media monitoring setup?",
        'hint': 'Be honest — this directly shapes your score and recommendations.',
        'options': [
            'We have no formal monitoring in place',
            'We do ad hoc Google searches and social checks',
            'We use one or two tools but not consistently',
            "We have a structured process but it's manual and slow",
            'We have a fully integrated, real-time monitoring system',
        ],
        'scores': [0, 2, 5, 8, 10],
    },
    {
        'key': 'platforms',
        'section': 'Platform Coverage',
        'type': MULTI_SELECT,
        'text': 'Which platforms or channels do you need to monitor? Select all that apply.',
        'hint': 'Social Light covers print, broadcast, online news and social media across SADC.',
        'options': [
            'Social media (Facebook, Instagram, X, LinkedIn, TikTok)',
            'Online news and digital publications',
            'Print media and newspapers',
            'Radio and TV broadcast',
            'Government and regulatory announcements',
            'Competitor intelligence',
        ],
    },
    {
        'key': 'urgency',
        'section': 'Urgency',
        'type': MULTIPLE_CHOICE,
        'text': 'How quickly does your team need to know when your brand is mentioned?',
        'hint': 'Delayed alerts can turn a manageable situation into a reputation crisis.',
        'options': [
            'Within seconds — real-time alerts are critical',
            'Within the hour',
            'Same day is fine',
            'We review mentions weekly',
            "We don't currently track this",
        ],
        'scores': [10, 8, 5, 2, 0],
        'bands': ['High', 'High', 'Medium', 'Low', 'None'],
    },
    {
        'key': 'caught_out',
        'section': 'Reputation Readiness',
        'type': YES_NO,
        'text': ('Has your brand ever been caught off-guard by negative media coverage '
                 'or a social media storm?'),
        'hint': 'This is the single most common reason organisations come to Social Light.',
        'yes_label': "Yes — we've been caught out",
        'no_label': "No — we've stayed ahead",
        'yes_score': 0,
        'no_score': 10,
    },
    {
        'key': 'budget',
        'section': 'Investment Readiness',
        'type': MULTIPLE_CHOICE,
        'text': ('What monthly investment would your organisation consider for a '
                 'professional media monitoring service?'),
        'hint': 'Social Light offers flexible packages for teams of all sizes across SADC.',
        # TODO(content): these bands are quoted in US dollars. For a Botswana and
        # wider SADC audience, confirm with whoever owns pricing whether they
        # should be in Pula instead.
        'options': [
            'Under $500/month',
            '$500 – $1,500/month',
            '$1,500 – $3,000/month',
            '$3,000 – $6,000/month',
            '$6,000+/month — enterprise',
        ],
        'scores': [1, 4, 7, 9, 10],
        'bands': ['Entry', 'Growth', 'Professional', 'Enterprise', 'Enterprise'],
    },
    {
        'key': 'timeline',
        'section': 'Decision Timeline',
        'type': MULTIPLE_CHOICE,
        'text': ('How soon is your organisation looking to implement or upgrade its '
                 'media monitoring?'),
        'hint': 'Social Light can onboard new clients within 5 business days.',
        'options': [
            'Immediately — this is urgent',
            'Within the next 30 days',
            'Next quarter (1–3 months)',
            'Later this year',
            'Just exploring for now',
        ],
        'scores': [10, 8, 6, 3, 1],
        'bands': ['Immediate', 'This Month', 'Next Quarter', 'This Year', 'Exploring'],
    },
    {
        'key': 'team_size',
        'section': 'Scale',
        'type': MULTIPLE_CHOICE,
        'text': 'How many team members would use a media monitoring platform?',
        'hint': 'This helps us recommend the right package and seat count.',
        'options': [
            '1 person (just me)',
            '2–5 people',
            '6–15 people',
            '16–50 people',
            '50+ people / enterprise team',
        ],
        'scores': [3, 5, 7, 9, 10],
    },
    {
        'key': 'challenge',
        'section': 'Biggest Challenge',
        'type': MULTIPLE_CHOICE,
        'text': 'What is your single biggest media monitoring challenge right now?',
        'hint': 'This helps the team prioritise what to address first in your recommendations.',
        'options': [
            "I don't know what's being said about us online",
            'We react too slowly to coverage — good or bad',
            "We can't get Africa-specific coverage, only global tools",
            'We have data but no one to interpret or act on it',
            'We need competitive intelligence, not just brand tracking',
        ],
    },
    {
        'key': 'goal',
        'section': 'Your Goal',
        'type': MULTIPLE_CHOICE,
        'text': 'What would success look like for you with a media monitoring solution?',
        'hint': 'Your goal shapes the package and approach Social Light would recommend.',
        'options': [
            'Protect our reputation — know first, respond fast',
            'Generate intelligence to inform strategy and campaigns',
            'Track competitor activity across SADC markets',
            'Demonstrate PR and marketing ROI to leadership',
            'All of the above',
        ],
    },
    {
        'key': 'note',
        'section': 'Anything Else',
        'type': FREE_TEXT,
        'text': ('Is there anything specific you would like the Social Light team to know '
                 'before reviewing your results?'),
        'hint': ('Many clients share a timeline, a recent incident, or a specific market '
                 'here. This is optional but helps us give sharper recommendations.'),
    },
]

QUESTIONS_BY_KEY = {q['key']: q for q in QUESTIONS}

#: The questions that carry points, and the total they can add up to. Derived
#: rather than written down, so adding a scored question cannot leave a stale
#: maximum behind and quietly deflate every score.
SCORED_KEYS = [q['key'] for q in QUESTIONS if 'scores' in q or q['type'] == YES_NO]
MAX_SCORE = sum(
    max(q['scores']) if 'scores' in q else max(q['yes_score'], q['no_score'])
    for q in QUESTIONS if q['key'] in SCORED_KEYS
)

# Score bands. `tier` is about how well the visitor is monitoring today; `fit` is
# a separate judgement about whether Social Light is the right next step for
# them, which is not the same question — a well-organised team can still be a
# strong fit, and a struggling one with no budget is not.
TIER_STRONG, TIER_GROWING, TIER_ATTENTION = 'strong', 'growing', 'attention'
FIT_HIGH, FIT_ELIGIBLE, FIT_EARLY = 'high_value', 'eligible', 'early'

TIER_LABELS = {
    TIER_STRONG: 'Strong',
    TIER_GROWING: 'Growing',
    TIER_ATTENTION: 'Needs attention',
}
FIT_LABELS = {
    FIT_HIGH: 'High-priority fit',
    FIT_ELIGIBLE: 'Good fit',
    FIT_EARLY: 'Early stage',
}


def _choice_index(answers, key):
    """The selected option index for a multiple-choice question, or None.

    Answers arrive from a browser, so anything at all may be in them. Anything
    that is not an in-range integer is treated as unanswered rather than allowed
    to raise: a malformed submission should score low, not return a 500.
    """
    question = QUESTIONS_BY_KEY.get(key)
    if question is None or 'options' not in question:
        return None
    try:
        index = int(answers.get(key))
    except (TypeError, ValueError):
        return None
    return index if 0 <= index < len(question['options']) else None


def _band(key, answers, default='Unknown'):
    index = _choice_index(answers, key)
    bands = QUESTIONS_BY_KEY[key].get('bands') or []
    return bands[index] if index is not None and index < len(bands) else default


def selected_platforms(answers):
    """The platform options ticked, as their display text."""
    options = QUESTIONS_BY_KEY['platforms']['options']
    raw = answers.get('platforms')
    if not isinstance(raw, (list, tuple)):
        return []
    out = []
    for value in raw:
        try:
            index = int(value)
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(options) and options[index] not in out:
            out.append(options[index])
    return out


def raw_score(answers):
    total = 0
    for key in SCORED_KEYS:
        question = QUESTIONS_BY_KEY[key]
        if question['type'] == YES_NO:
            answer = answers.get(key)
            if answer == 'no':
                total += question['no_score']
            elif answer == 'yes':
                total += question['yes_score']
            continue
        index = _choice_index(answers, key)
        if index is not None:
            total += question['scores'][index]
    return total


def score(answers):
    """Grade a set of answers.

    Returns everything the results page, the report email and the sales
    notification need, so that none of them re-derives any of it and they cannot
    drift apart. ``answers`` is the raw dictionary as submitted.
    """
    answers = answers if isinstance(answers, dict) else {}
    percentage = round(raw_score(answers) / MAX_SCORE * 100) if MAX_SCORE else 0

    if percentage >= 70:
        tier = TIER_STRONG
    elif percentage >= 40:
        tier = TIER_GROWING
    else:
        tier = TIER_ATTENTION

    budget_index = _choice_index(answers, 'budget')
    urgency_index = _choice_index(answers, 'urgency')

    # The business's own qualification rules, kept exactly as briefed. Budget is
    # the gate in both: an organisation that will not spend is not a lead no
    # matter how badly it needs monitoring, and saying so on the page is kinder
    # than a sales call that goes nowhere.
    eligible = percentage >= 30 and budget_index is not None and budget_index >= 1
    high_value = (percentage >= 60
                  and budget_index is not None and budget_index >= 2
                  and urgency_index is not None and urgency_index <= 1)

    if high_value:
        fit = FIT_HIGH
    elif eligible:
        fit = FIT_ELIGIBLE
    else:
        fit = FIT_EARLY

    return {
        'score': percentage,
        'tier': tier,
        'tier_label': TIER_LABELS[tier],
        'fit': fit,
        'fit_label': FIT_LABELS[fit],
        'urgency': _band('urgency', answers),
        'budget': _band('budget', answers),
        'timeline': _band('timeline', answers),
        'platforms': selected_platforms(answers),
        'team_size': answer_text(answers, 'team_size'),
        'challenge': answer_text(answers, 'challenge'),
        'goal': answer_text(answers, 'goal'),
        'note': (answers.get('note') or '').strip() if isinstance(answers.get('note'), str) else '',
    }


def answer_text(answers, key):
    """The chosen option's wording, for a human reading the result."""
    question = QUESTIONS_BY_KEY.get(key)
    if question is None:
        return ''
    if question['type'] == YES_NO:
        answer = answers.get(key)
        if answer == 'yes':
            return question['yes_label']
        return question['no_label'] if answer == 'no' else ''
    if question['type'] == FREE_TEXT:
        value = answers.get(key)
        return value.strip() if isinstance(value, str) else ''
    if question['type'] == MULTI_SELECT:
        return ', '.join(selected_platforms(answers))
    index = _choice_index(answers, key)
    return question['options'][index] if index is not None else ''


# ── What the score means, in words ───────────────────────────────────────────
# Deliberately plain. Each line restates something the visitor told us and names
# its consequence. Nothing here asserts a fact about Social Light's coverage,
# performance or results — proof claims belong on pages where someone owns them,
# not in text generated from a form.

HEADLINES = {
    TIER_STRONG: 'Strong monitoring foundation.',
    TIER_GROWING: 'Gaps are holding you back.',
    TIER_ATTENTION: 'Your brand has significant blind spots.',
}

SUMMARIES = {
    TIER_STRONG: ('You are tracking coverage and can evidence it. The useful conversation '
                  'is about breadth — the print and broadcast sources most tools miss — '
                  'rather than starting from scratch.'),
    TIER_GROWING: ('You have the right instincts. Structured monitoring would close the '
                   'gaps you have just described.'),
    TIER_ATTENTION: ('Most of what is said about your organisation is currently reaching '
                     'you late, or not at all.'),
}

FIT_MESSAGES = {
    FIT_HIGH: ('Your urgency, investment range and monitoring needs line up with our '
               'professional and enterprise packages. Someone from the team will be in '
               'touch within one working day.'),
    FIT_ELIGIBLE: ('Your needs and investment range line up with our growth packages. A '
                   'short discovery call is the fastest way to map the right one.'),
    FIT_EARLY: ('Your organisation is still building its monitoring foundation. Start with '
                'the free guide and a group session before committing to anything paid.'),
}

CALLS_TO_ACTION = {
    FIT_HIGH: {
        'action': 'call',
        'title': 'Book a discovery call',
        'body': ('We will review your answers before we speak, so the call starts from '
                 'where you actually are rather than from a script.'),
        'button': 'Request my discovery call',
        'confirmation': 'Thank you. The team will be in touch within one working day.',
    },
    FIT_ELIGIBLE: {
        'action': 'webinar',
        'title': 'Join the next strategy session',
        'body': ('A group walkthrough of building a monitoring strategy for SADC markets, '
                 'including which channels carry the most weight by sector.'),
        'button': 'Register for the session',
        'confirmation': 'Thank you. We will send the invitation to your inbox.',
    },
    FIT_EARLY: {
        'action': 'guide',
        'title': 'Start with the foundation guide',
        'body': ('Our media monitoring starter guide, written for brands operating in '
                 'African markets, followed by an invitation to the next group session.'),
        'button': 'Send me the guide',
        'confirmation': 'Thank you. We will send the guide to your inbox.',
    },
}


def insights(result, answers):
    """Two to four short observations, drawn from what was actually answered."""
    out = []

    if answers.get('caught_out') == 'yes':
        out.append({
            'title': 'You have been caught out before',
            'body': ('You told us a story reached you before you were ready for it. That is '
                     'the gap monitoring is meant to close, and it is the most common '
                     'reason organisations start looking.'),
        })

    if result['urgency'] in ('Low', 'None'):
        out.append({
            'title': 'Your alerting is slower than your risk',
            'body': ('Reviewing mentions weekly, or not at all, means the first you hear of '
                     'a problem is usually from someone else. Real-time alerting changes '
                     'which conversations you get to be part of.'),
        })
    elif result['urgency'] == 'High':
        out.append({
            'title': 'You need to know inside the hour',
            'body': ('That rules out anything built around a daily or weekly digest. It is '
                     'worth checking how quickly any tool you consider actually alerts.'),
        })

    missing = [p for p in ('Print media and newspapers', 'Radio and TV broadcast')
               if p in result['platforms']]
    if missing:
        out.append({
            'title': 'You need more than online coverage',
            'body': ('You have asked for ' + ' and '.join(m.lower() for m in missing) +
                     '. These are the channels international platforms are weakest on in '
                     'this region, so they are worth testing before you commit to anyone.'),
        })

    index = _choice_index(answers, 'challenge')
    if index is not None:
        out.append({
            'title': 'Your biggest constraint, in your words',
            'body': QUESTIONS_BY_KEY['challenge']['options'][index],
        })

    return out[:4]
