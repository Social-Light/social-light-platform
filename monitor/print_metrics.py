"""Print reach estimation.

Print coverage has no measured reach, so reach is estimated as
``circulation × readers-per-copy``. Circulation figures below are approximate —
replace with your audited/verified numbers as they become available.
"""
import re

# Approximate per-issue circulation for known print titles (normalised name → copies).
PRINT_CIRCULATION = {
    'mmegi': 25000,
    'thevoice': 30000,
    'dailynews': 50000,
    'botswanaguardian': 20000,
    'botswanagazette': 20000,
    'thebotswanagazette': 20000,
    'sundaystandard': 20000,
    'thepatriotonsunday': 15000,
    'thepatriot': 15000,
    'themonitor': 15000,
    'weekendpost': 15000,
    'echo': 10000,
}
DEFAULT_CIRCULATION = 10000   # fallback when the publication isn't listed
READERS_PER_COPY = 3          # pass-along / readership multiplier


def estimate_print_reach(publication):
    """Estimated readership for one print article: circulation × readers-per-copy."""
    key = re.sub(r'[^a-z0-9]', '', (publication or '').lower())
    return PRINT_CIRCULATION.get(key, DEFAULT_CIRCULATION) * READERS_PER_COPY
