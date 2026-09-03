"""The onboarding flow: which page a user should be on, and why.

The wizard is described here as data rather than as a chain of redirects inside
the views, so that the middleware, the views and the progress rail in the
templates all read the *same* definition of the flow. Adding a step means adding
one ``Step`` and its view; nothing else has to learn about it.

Two properties matter more than the step list itself:

**It is resumable.** ``OnboardingProgress.state`` is the furthest point reached
and only ever moves forward, so a user who closes the tab at the payment step
comes back to the payment step. Nothing is re-asked and nothing is lost.

**It is skippable where a step does not apply.** A colleague invited into an
existing organisation must accept the legal documents themselves, but must not be
walked through payment and plan selection for a subscription that already exists
and is not theirs. Skips are declared per step by an ``applies`` predicate.
"""
from django.conf import settings
from django.urls import reverse

from .onboarding_models import STATE_ORDER, OnboardingProgress


# Every African country, alphabetically, followed by the handful of non-African
# markets clients actually register from and an "Other" catch-all. Africa is
# listed in full rather than trimmed to Southern Africa: the product sells across
# the continent, and a Kenyan or Nigerian client picking "Other" because their own
# country was left out is both a poor first impression and lost data.
#
# Stored as plain text on User.country and Organization.country, not as ISO codes,
# because that is what those fields already hold for every existing record.
AFRICAN_COUNTRIES = [
    'Algeria', 'Angola', 'Benin', 'Botswana', 'Burkina Faso', 'Burundi',
    'Cabo Verde', 'Cameroon', 'Central African Republic', 'Chad', 'Comoros',
    'Congo (Democratic Republic of the)', 'Congo (Republic of the)',
    "Côte d'Ivoire", 'Djibouti', 'Egypt', 'Equatorial Guinea', 'Eritrea',
    'Eswatini', 'Ethiopia', 'Gabon', 'Gambia', 'Ghana', 'Guinea',
    'Guinea-Bissau', 'Kenya', 'Lesotho', 'Liberia', 'Libya', 'Madagascar',
    'Malawi', 'Mali', 'Mauritania', 'Mauritius', 'Morocco', 'Mozambique',
    'Namibia', 'Niger', 'Nigeria', 'Rwanda', 'São Tomé and Príncipe',
    'Senegal', 'Seychelles', 'Sierra Leone', 'Somalia', 'South Africa',
    'South Sudan', 'Sudan', 'Tanzania', 'Togo', 'Tunisia', 'Uganda',
    'Zambia', 'Zimbabwe',
]

OTHER_COUNTRIES = [
    'United Kingdom', 'United States', 'United Arab Emirates', 'China',
    'India', 'Australia', 'Canada', 'Other',
]

COUNTRY_CHOICES = AFRICAN_COUNTRIES + OTHER_COUNTRIES

# ISO 3166-1 alpha-2 codes for every name above. Kept here, beside the list it
# maps, so the two cannot drift apart when a country is added.
#
# The application itself has no use for these — country is stored, filtered and
# displayed as a plain name. They exist for external systems that will not accept
# anything else: DPO's payment API rejects a full country name outright with
# "902 Data mismatch". Converting at that boundary keeps the ISO requirement in
# the one place that has it, rather than migrating a field that six coverage
# models also use and that is populated from media feeds we do not control.
#
# 'Other' is deliberately absent: it is a real choice in the dropdown but not a
# country, so it has no code and callers must omit the field rather than guess.
COUNTRY_ALPHA2 = {
    'Algeria': 'DZ', 'Angola': 'AO', 'Benin': 'BJ', 'Botswana': 'BW',
    'Burkina Faso': 'BF', 'Burundi': 'BI', 'Cabo Verde': 'CV', 'Cameroon': 'CM',
    'Central African Republic': 'CF', 'Chad': 'TD', 'Comoros': 'KM',
    'Congo (Democratic Republic of the)': 'CD', 'Congo (Republic of the)': 'CG',
    "Côte d'Ivoire": 'CI', 'Djibouti': 'DJ', 'Egypt': 'EG',
    'Equatorial Guinea': 'GQ', 'Eritrea': 'ER', 'Eswatini': 'SZ',
    'Ethiopia': 'ET', 'Gabon': 'GA', 'Gambia': 'GM', 'Ghana': 'GH',
    'Guinea': 'GN', 'Guinea-Bissau': 'GW', 'Kenya': 'KE', 'Lesotho': 'LS',
    'Liberia': 'LR', 'Libya': 'LY', 'Madagascar': 'MG', 'Malawi': 'MW',
    'Mali': 'ML', 'Mauritania': 'MR', 'Mauritius': 'MU', 'Morocco': 'MA',
    'Mozambique': 'MZ', 'Namibia': 'NA', 'Niger': 'NE', 'Nigeria': 'NG',
    'Rwanda': 'RW', 'São Tomé and Príncipe': 'ST', 'Senegal': 'SN',
    'Seychelles': 'SC', 'Sierra Leone': 'SL', 'Somalia': 'SO',
    'South Africa': 'ZA', 'South Sudan': 'SS', 'Sudan': 'SD',
    'Tanzania': 'TZ', 'Togo': 'TG', 'Tunisia': 'TN', 'Uganda': 'UG',
    'Zambia': 'ZM', 'Zimbabwe': 'ZW',
    'United Kingdom': 'GB', 'United States': 'US',
    'United Arab Emirates': 'AE', 'China': 'CN', 'India': 'IN',
    'Australia': 'AU', 'Canada': 'CA',
}


def country_alpha2(value):
    """The ISO 3166-1 alpha-2 code for a stored country name, or ''.

    Returns '' rather than a guess for anything unrecognised — including 'Other',
    a blank, and free text typed before the dropdown existed. A caller sending
    this to an external API must omit the field entirely in that case: an invalid
    code is rejected outright, whereas a missing optional one is not.

    A value that is already a two-letter code is passed through, so a record
    written directly with 'BW' still works.
    """
    text = (value or '').strip()
    if not text:
        return ''
    if len(text) == 2 and text.isalpha():
        return text.upper()
    return COUNTRY_ALPHA2.get(text, '')

# Pre-selected on a blank form. Botswana is the home market, and without this the
# browser would default to whatever sorts first alphabetically.
DEFAULT_COUNTRY = 'Botswana'


# The seven stages shown in the progress rail. Several pages can share a stage —
# the three consent pages are all "Legal & consent" — so the user sees a coherent
# seven-step process rather than ten disconnected screens.
STAGES = [
    (1, 'Account'),
    (2, 'Verify'),
    (3, 'Organisation'),
    (4, 'Legal & consent'),
    (5, 'Payment'),
    (6, 'Plan'),
    (7, 'Complete'),
]


def _is_billing_owner(user):
    """Whether this account is the one responsible for the organisation's
    subscription. Only the organisation's admin is taken through payment and
    plan selection; an invited colleague joins a subscription that already
    exists."""
    return getattr(user, 'role', '') == 'org_admin' or user.is_superuser


def _payments_configured(user):
    """The payment step is always *shown* — a user needs to be told why they are
    not being asked for a card — but it only applies to the billing owner."""
    return _is_billing_owner(user)


class Step:
    """One page of the wizard.

    ``completes`` is the onboarding state reaching this page's end records. That
    single value is what makes the flow resumable: the next step is simply the
    first applicable step whose ``completes`` state has not been reached.
    """

    def __init__(self, key, url_name, stage, title, blurb, completes, applies=None):
        self.key = key
        self.url_name = url_name
        self.stage = stage
        self.title = title
        self.blurb = blurb
        self.completes = completes
        self._applies = applies

    def __repr__(self):
        return f'<Step {self.key}>'

    @property
    def url(self):
        return reverse(f'monitor:{self.url_name}')

    def applies_to(self, user):
        return True if self._applies is None else self._applies(user)

    @property
    def is_terminal(self):
        """The confirmation page. It is not an action the user has to take, so it
        is never something the flow waits on — completion is derived from every
        other step being done, and this page is where the user is told so."""
        return self.completes == 'complete'


STEPS = [
    Step('verify', 'onboarding_verify', 2, 'Confirm your email address',
         'We sent you a link. Confirming the address keeps your account, your alerts and your '
         'reports going to a mailbox you control.',
         completes='email_verified'),

    Step('profile', 'onboarding_profile', 3, 'About you',
         'Your name and role tell us who to address reports to, and your country sets the '
         'markets we monitor by default.',
         completes='profile_completed'),

    Step('agency', 'onboarding_agency', 3, 'Who is this account for?',
         'Whether you are using Social Light for yourself or on behalf of an organisation '
         'changes who is bound by the terms you accept next.',
         completes='agency_declared'),

    Step('terms', 'onboarding_terms', 4, 'Terms & Conditions',
         'The terms on which you may use Social Light.',
         completes='terms_accepted'),

    Step('privacy', 'onboarding_privacy', 4, 'Privacy & personal data',
         'A separate consent, because this is about your personal data rather than the service.',
         completes='privacy_accepted'),

    Step('disclaimer', 'onboarding_disclaimer', 4, 'Disclaimer',
         'What you can and cannot rely on, and your responsibility for how you use it.',
         completes='disclaimer_accepted'),

    Step('payment', 'onboarding_payment', 5, 'Payment method',
         'Your card is held by our payment provider, not by us. We only ever store their '
         'token for it.',
         completes='payment_method_added', applies=_payments_configured),

    Step('plan', 'onboarding_plan', 6, 'Choose your plan',
         'Your plan decides which features are available. You can change it later.',
         completes='plan_assigned', applies=_is_billing_owner),

    Step('done', 'onboarding_done', 7, "You're all set",
         'Everything is in place.',
         completes='complete'),
]

STEPS_BY_KEY = {s.key: s for s in STEPS}
STEP_KEYS = [s.key for s in STEPS]


# ── Progress ─────────────────────────────────────────────────────────────────

def get_progress(user, create=False):
    """This user's onboarding record.

    ``create=False`` by default and returns None for accounts that predate
    onboarding — the callers treat a missing record as "nothing to do", which is
    what grandfathers existing users in rather than trapping them in a wizard
    they were never shown.
    """
    if user is None or not user.is_authenticated:
        return None
    if create:
        progress, _ = OnboardingProgress.objects.get_or_create(user=user)
        return progress
    return user.onboarding_progress


def start(user):
    """Create the onboarding record for a brand-new account."""
    progress, _ = OnboardingProgress.objects.get_or_create(user=user)
    progress.mark('registered')
    return progress


def applicable_steps(user):
    """The steps this user actually has to complete.

    The terminal confirmation page is excluded: including it would mean the flow
    was always waiting on one more step, so ``next_step`` would never return None
    and onboarding could never complete.
    """
    return [s for s in STEPS if not s.is_terminal and s.applies_to(user)]


def is_complete(user):
    progress = get_progress(user)
    return progress is None or progress.is_complete


def next_step(user):
    """The step the user should be on now, or None when onboarding is finished.

    Steps that do not apply to this user are skipped *and* their state is treated
    as already reached, so an invited colleague is not stopped forever at a
    payment step that was never meant for them.
    """
    progress = get_progress(user)
    if progress is None or progress.is_complete:
        return None
    for step in applicable_steps(user):
        if not progress.has_reached(step.completes):
            return step
    return None


def next_url(user):
    step = next_step(user)
    return step.url if step else reverse('monitor:onboarding_done')


def can_access(user, step_key):
    """Whether the user may open `step_key` right now.

    Going *back* to a completed step is allowed — the requirement is explicit
    that users should be able to review and change earlier answers. Jumping
    *forward* past an incomplete step is not: that is how someone would reach the
    plan step without having accepted the terms.
    """
    step = STEPS_BY_KEY.get(step_key)
    progress = get_progress(user)
    if step is None or progress is None:
        return False
    if not step.applies_to(user):
        return False
    if progress.is_complete:
        return True
    pending = next_step(user)
    if pending is None:
        return True
    if step.is_terminal:
        # Only reachable once nothing else is outstanding, which the branch above
        # has already established is not the case here.
        return False
    return STATE_ORDER[step.completes] <= STATE_ORDER[pending.completes]


def advance(user, state):
    """Record a state as reached and complete onboarding if nothing is left.

    Completion is derived rather than asserted: after marking a state we ask
    whether any applicable step is still outstanding, and only then mark the
    whole thing complete. That way a step becoming applicable later (a viewer
    promoted to org admin, say) is noticed instead of being skipped because a
    'complete' flag had already been set.
    """
    progress = get_progress(user, create=True)
    progress.mark(state)
    if next_step(user) is None and not progress.is_complete:
        progress.mark('complete')
    return progress


def skip_payment(user, reason='gateway disabled'):
    """Mark the payment step done without a card, recording why.

    Used when PAYMENTS_ENABLED is off: onboarding must still complete, and the
    record has to show that no payment method was collected rather than implying
    one was.
    """
    progress = get_progress(user, create=True)
    if not progress.payment_skipped:
        progress.payment_skipped = True
        progress.save(update_fields=['payment_skipped', 'updated_at'])
    return advance(user, 'payment_method_added')


# ── Presentation ─────────────────────────────────────────────────────────────

def stage_rail(user, current_step):
    """The seven-stage progress rail, with each stage marked done / current /
    upcoming. Built from the same step data the flow itself runs on, so the rail
    can never disagree with where the user actually is."""
    progress = get_progress(user)
    steps = applicable_steps(user)
    current_stage = current_step.stage if current_step else 7

    rail = []
    for number, label in STAGES:
        stage_steps = [s for s in steps if s.stage == number]
        if progress is None:
            done = True
        elif stage_steps:
            done = all(progress.has_reached(s.completes) for s in stage_steps)
        else:
            # Stage 1 (Account) has no page — reaching onboarding at all means
            # registration happened.
            done = True
        rail.append({
            'number': number,
            'label': label,
            'is_done': done and number < current_stage,
            'is_current': number == current_stage,
            'is_upcoming': number > current_stage,
        })
    return rail


def context_for(user, step_key):
    """Everything a wizard template needs about where it sits in the flow."""
    step = STEPS_BY_KEY.get(step_key)
    steps = applicable_steps(user)
    index = steps.index(step) if step in steps else 0
    return {
        'step': step,
        'stage_rail': stage_rail(user, step),
        'stage_count': len(STAGES),
        'back_url': steps[index - 1].url if index > 0 else None,
        'onboarding_progress': get_progress(user),
    }


def payments_enabled():
    """Whether a payment gateway is switched on for this deployment. Read through
    a function so the setting can be flipped in tests with override_settings."""
    return bool(getattr(settings, 'PAYMENTS_ENABLED', False))
