"""
Seed the whole platform so every model in monitor/models.py is populated and the
shape of the system is visible end to end: organisations → their users →
their tracking config (keywords, competitors, campaigns, media sources) →
their coverage (online, print, social, broadcast, competitor) → the things
built on top of coverage (reports, analyses, issue reports, alerts, events).

Everything is owned by an Organization. The only rows that are not are the
platform-admin Users, who sit above all organisations.

Organisation UUIDs are derived from the org name with uuid5, so the crawler's
`seed_system` (media-monitor) produces the *same* ids for the same orgs and the
two systems line up even when seeded into separate local databases.

    python manage.py seed_system            # idempotent top-up
    python manage.py seed_system --reset    # wipe seeded orgs/users first
"""
import random
import uuid
from datetime import date, timedelta
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from monitor.models import (
    Alert,
    BroadcastMention,
    Campaign,
    Competitor,
    CompetitorArticle,
    Event,
    GeneratedReport,
    IssueReport,
    Keyword,
    MediaSource,
    OnlineArticle,
    Organization,
    PrintArticle,
    ReportAnalysis,
    SocialMediaPost,
    User,
)

ORG_NAMESPACE = uuid.UUID('6f1e6a30-2a3c-5f1e-9c4b-3f2a1d0e5b77')
SEED_PASSWORD = 'Sociallight#2026'
TODAY = date(2026, 8, 18)

SENTIMENTS = ['positive', 'positive', 'neutral', 'neutral', 'negative', 'mixed']


def org_uuid(name):
    """Stable UUID for an organisation name, shared with the crawler's seed."""
    return uuid.uuid5(ORG_NAMESPACE, f'sociallight:org:{name}')


# ─────────────────────────────────────────────────────────────────────────────
# The three tenants
# ─────────────────────────────────────────────────────────────────────────────

ORGS = [
    {
        'name': 'First National Bank of Botswana',
        'existing_names': ['First National Bank of Botswana', 'FNB Botswana', 'FNBB'],
        'short': 'FNBB',
        'email': 'comms@fnbbotswana.co.bw',
        'industry': 'Banking & Financial Services',
        'country': 'Botswana',
        'address': 'Plot 54362, First Place, CBD, Gaborone',
        'phone': '+267 370 6000',
        'website': 'https://www.fnbbotswana.co.bw',
        'facebook_url': 'https://facebook.com/FNBBotswana',
        'linkedin_url': 'https://linkedin.com/company/fnb-botswana',
        'x_handle': '@FNBBotswana',
        'gradient_color1': '#0f766e',
        'gradient_color2': '#0f172a',
        'keywords': {
            'brand': ['FNBB', 'FNB Botswana', 'First National Bank Botswana', 'eWallet', 'FNB Foundation'],
            'personnel': ['Steven Bogatsu', 'Bopelokgale Soko', 'Mpho Kadibi'],
            'campaign': ['#HelpYouSucceed', 'FNBB Acacia Awards', 'Bank Better'],
        },
        'competitors': [
            ('Absa Bank Botswana', 'Absa, Absa BW, Barclays Botswana', 'https://www.absa.co.bw'),
            ('Stanbic Bank Botswana', 'Stanbic, Standard Bank Botswana', 'https://www.stanbicbank.co.bw'),
            ('Standard Chartered Botswana', 'StanChart, Standard Chartered Bank Botswana', 'https://www.sc.com/bw'),
            ('Access Bank Botswana', 'Access Bank, BancABC Botswana', 'https://botswana.accessbankplc.com'),
        ],
        'sources': [
            ('Mmegi Online', 'online', 'https://www.mmegi.bw', '', 120000),
            ('Botswana Guardian', 'print', 'https://www.botswanaguardian.co.bw', '', 45000),
            ('Business Weekly & Review', 'online', 'https://www.businessweekly.co.bw', '', 60000),
            ('FNBB Facebook', 'social', 'https://facebook.com/FNBBotswana', '@FNBBotswana', 210000),
            ('Btv Main News', 'broadcast', 'https://www.btv.gov.bw', '', 350000),
        ],
        'campaigns': [
            ('Help You Succeed 2026', ['#HelpYouSucceed', 'Help You Succeed', 'FNBB SME'], -120, 60),
            ('Acacia Innovation Awards', ['#AcaciaAwards', 'Acacia Awards', 'FNBB innovation'], -60, 30),
        ],
        'issue': ('Digital banking outage of March 2026',
                  'eWallet outage OR banking app downtime OR service disruption'),
        'headlines': [
            'FNB Botswana posts a {pct}% rise in half-year profit',
            'FNBB and Africa’s Eden Tourism sign a strategic tourism partnership',
            'eWallet transaction volumes climb as FNBB widens agent network',
            'FNBB Foundation commits P{amt} million to youth skills programmes',
            'Analysts weigh FNBB’s cost-to-income ratio against regional peers',
            'FNB Botswana rolls out new SME lending product for the CBD corridor',
            'Bank of Botswana rate decision puts pressure on FNBB margins',
            'FNBB customers report delays on the mobile banking app',
        ],
    },
    {
        'name': 'Debswana Diamond Company',
        'existing_names': ['Debswana Diamond Company', 'Debswana'],
        'short': 'Debswana',
        'email': 'corporateaffairs@debswana.bw',
        'industry': 'Mining & Metals',
        'country': 'Botswana',
        'address': 'Debswana House, The Mall, Gaborone',
        'phone': '+267 361 4200',
        'website': 'https://www.debswana.com',
        'facebook_url': 'https://facebook.com/DebswanaDiamondCompany',
        'linkedin_url': 'https://linkedin.com/company/debswana',
        'x_handle': '@DebswanaDiamond',
        'gradient_color1': '#1d4ed8',
        'gradient_color2': '#0f172a',
        'keywords': {
            'brand': ['Debswana', 'Jwaneng Mine', 'Orapa Mine', 'Cut-9', 'Damtshaa'],
            'personnel': ['Andrew Motsomi', 'Lynette Armstrong', 'Koreteng Serema'],
            'campaign': ['#DebswanaCares', 'Citizen Economic Empowerment', 'Tokafala'],
        },
        'competitors': [
            ('Lucara Diamond Corp', 'Lucara, Karowe Mine', 'https://www.lucaradiamond.com'),
            ('Khoemacau Copper Mining', 'Khoemacau, KCM Botswana', 'https://www.khoemacau.com'),
            ('Alrosa', 'ALROSA PJSC', 'https://www.alrosa.ru'),
        ],
        'sources': [
            ('Mining Weekly', 'online', 'https://www.miningweekly.com', '', 180000),
            ('Sunday Standard', 'print', 'https://www.sundaystandard.info', '', 38000),
            ('Rough & Polished', 'online', 'https://rough-polished.com', '', 42000),
            ('Debswana LinkedIn', 'social', 'https://linkedin.com/company/debswana', 'debswana', 95000),
            ('RB2 Business Hour', 'broadcast', '', '', 150000),
        ],
        'campaigns': [
            ('Cut-9 Community Programme', ['Cut-9', 'Jwaneng expansion', '#DebswanaCares'], -180, 90),
        ],
        'issue': ('Jwaneng Cut-9 expansion and community resettlement',
                  'Cut-9 OR Jwaneng expansion OR resettlement compensation'),
        'headlines': [
            'Debswana lifts diamond output {pct}% on Jwaneng Cut-9 ramp-up',
            'De Beers and Botswana finalise the diamond sales agreement',
            'Debswana commits P{amt} million to Jwaneng community projects',
            'Weak rough diamond demand tests Debswana’s production plan',
            'Orapa mine upgrade to extend life of mine beyond 2050',
            'Debswana suppliers raise concerns over payment cycles',
            'Citizen economic empowerment spend at Debswana reaches new high',
        ],
    },
    {
        'name': 'Botswana Telecommunications Corporation',
        'existing_names': ['Botswana Telecommunications Corporation', 'BTC'],
        'short': 'BTC',
        'email': 'media@btc.bw',
        'industry': 'Telecommunications',
        'country': 'Botswana',
        'address': 'Megaleng House, Khama Crescent, Gaborone',
        'phone': '+267 395 8000',
        'website': 'https://www.btc.bw',
        'facebook_url': 'https://facebook.com/BTCBotswana',
        'linkedin_url': 'https://linkedin.com/company/btc-botswana',
        'x_handle': '@BTCBotswana',
        'gradient_color1': '#7c3aed',
        'gradient_color2': '#0f172a',
        'keywords': {
            'brand': ['BTC', 'Botswana Telecommunications', 'Smega', 'BTC Fibre', 'beMOBILE'],
            'personnel': ['Anthony Masunga', 'Kabelo Binns'],
            'campaign': ['#BTCFibre', 'Smega Wallet', 'Connect Botswana'],
        },
        'competitors': [
            ('Mascom Wireless', 'Mascom, MyZaka', 'https://www.mascom.bw'),
            ('Orange Botswana', 'Orange BW, Orange Money', 'https://www.orange.co.bw'),
        ],
        'sources': [
            ('The Voice', 'online', 'https://www.thevoicebw.com', '', 70000),
            ('Daily News Botswana', 'print', 'https://www.dailynews.gov.bw', '', 55000),
            ('TechCabal', 'online', 'https://techcabal.com', '', 130000),
            ('BTC X', 'social', 'https://x.com/BTCBotswana', '@BTCBotswana', 64000),
        ],
        'campaigns': [
            ('BTC Fibre Rollout', ['#BTCFibre', 'BTC Fibre', 'fibre to the home'], -90, 120),
            ('Smega Merchant Drive', ['Smega', 'Smega Wallet', '#SmegaPay'], -45, 45),
        ],
        'issue': ('BTC network downtime and customer compensation',
                  'BTC outage OR network downtime OR service credit'),
        'headlines': [
            'BTC extends fibre coverage to {amt} more Gaborone suburbs',
            'Smega wallet adds merchant payments as mobile money competition heats up',
            'BTC reports a {pct}% increase in broadband subscribers',
            'BOCRA reviews quality-of-service complaints against operators',
            'BTC shares slip on the Botswana Stock Exchange after results',
            'Undersea cable fault disrupts BTC internet services nationwide',
            'BTC and government partner on rural connectivity rollout',
        ],
    },
]

ONLINE_SOURCES = [
    ('Mmegi Online', 'https://www.mmegi.bw/favicon.ico'),
    ('Sunday Standard', 'https://www.sundaystandard.info/favicon.ico'),
    ('Business Weekly & Review', ''),
    ('The Voice', ''),
    ('Botswana Gazette', ''),
    ('Mining Weekly', ''),
    ('TechCabal', ''),
    ('Reuters Africa', ''),
]
PRINT_SOURCES = ['Mmegi', 'Botswana Guardian', 'Daily News', 'Sunday Standard', 'The Patriot', 'Business Day (SA)']
BROADCAST_SOURCES = [('Btv', 'TV'), ('RB1', 'RADIO'), ('RB2', 'RADIO'), ('Duma FM', 'RADIO'),
                     ('Yarona FM', 'RADIO'), ('eNCA', 'TV'), ('Gabz FM Podcast', 'PODCAST')]
SOCIAL_PAGES = [
    ('Facebook', 'Botswana Youth Magazine'),
    ('Facebook', 'The Business Weekly & Review'),
    ('X', 'Botswana Business News'),
    ('LinkedIn', 'Africa Business Insider'),
    ('Instagram', 'Gaborone Daily'),
    ('YouTube', 'Botswana Business TV'),
    ('TikTok', 'BW Money Talk'),
]
COUNTRIES = ['Botswana', 'Botswana', 'Botswana', 'South Africa', 'Namibia', 'Zimbabwe', 'Zambia']


class Command(BaseCommand):
    help = 'Seed every platform model with a coherent multi-tenant demo dataset.'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Delete seeded organisations (cascades to all their data) '
                                 'and non-superuser accounts before seeding.')
        parser.add_argument('--months', type=int, default=12,
                            help='How many months of back coverage to generate (default 12).')

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20260818)
        self.months = options['months']
        self.reused = []

        if options['reset']:
            names = [o['name'] for o in ORGS]
            deleted, _ = Organization.objects.filter(name__in=names).delete()
            User.objects.filter(is_superuser=False).delete()
            self.stdout.write(self.style.WARNING(f'  reset: removed {deleted} rows'))

        platform_admins = self.seed_platform_admins()
        summary = []

        for spec in ORGS:
            org = self.seed_organization(spec)
            users = self.seed_org_users(org, spec)
            self.seed_keywords(org, spec)
            competitors = self.seed_competitors(org, spec)
            self.seed_media_sources(org, spec)
            campaigns = self.seed_campaigns(org, spec)

            online = self.seed_online(org, spec)
            self.seed_print(org, spec)
            social = self.seed_social(org, spec)
            self.seed_broadcast(org, spec)
            self.seed_competitor_articles(org, spec, competitors)

            author = users['org_admin']
            self.seed_reports(org, spec, author, online, social, campaigns)
            self.seed_alerts(org, spec, users)
            self.seed_events(org, spec)

            summary.append(org)

        self.report(platform_admins, summary)

    # ── Users ────────────────────────────────────────────────────────────────

    def seed_platform_admins(self):
        """Platform admins have no organisation — they see every tenant.

        An account that already exists keeps its email, role and organisation;
        only its password is reset to SEED_PASSWORD so the credentials this
        command prints are the ones that actually work. Accounts this command
        does not name (real user accounts) are never touched.
        """
        people = [
            ('tony', 'tony@sociallightbw.com', 'Tony', 'Mautsu', True),
            ('oratile', 'oratile@sociallightbw.com', 'Oratile', 'Busang', False),
            ('lettah', 'lettah@sociallightbw.com', 'Lettah', 'Koshwane', False),
        ]
        created = []
        for username, email, first, last, is_super in people:
            user = User.objects.filter(username=username).first()
            if not user:
                maker = User.objects.create_superuser if is_super else User.objects.create_user
                user = maker(username=username, email=email, password=SEED_PASSWORD,
                             first_name=first, last_name=last, role='platform_admin')
                user.organization = None
                user.is_staff = True
                user.save()
            else:
                user.set_password(SEED_PASSWORD)
                user.save(update_fields=['password'])
            created.append(user)
        return created

    def seed_org_users(self, org, spec):
        short = spec['short'].lower()
        domain = spec['website'].replace('https://www.', '').replace('https://', '')
        people = [
            ('org_admin', f'{short}.admin', f'admin@{domain}', 'Kagiso', 'Molefe'),
            ('viewer', f'{short}.analyst', f'analyst@{domain}', 'Naledi', 'Seleka'),
            ('viewer', f'{short}.comms', f'comms@{domain}', 'Thato', 'Ramotswe'),
        ]
        out = {}
        for role, username, email, first, last in people:
            user, _created = User.objects.get_or_create(
                username=username,
                defaults={'email': email, 'first_name': first, 'last_name': last,
                          'role': role, 'organization': org},
            )
            user.set_password(SEED_PASSWORD)
            user.save(update_fields=['password'])
            out.setdefault(role, user)
            out[username] = user
        return out

    # ── Organisation-owned configuration ─────────────────────────────────────

    def seed_organization(self, spec):
        """Reuse an organisation that is already in the database rather than
        creating a second one under a variant of the same name.

        A database that predates this command may hold the same tenant under a
        shorter name ('Debswana' for 'Debswana Diamond Company'). Matching on
        `existing_names` first keeps one organisation per real-world entity; its
        original name and id are left alone, and only blank profile fields are
        filled in. Only when nothing matches is a new organisation created, with
        the deterministic uuid5 id the crawler also derives.
        """
        fields = ('email', 'industry', 'country', 'address', 'phone', 'website',
                  'facebook_url', 'linkedin_url', 'x_handle')

        existing = None
        for name in spec.get('existing_names', [spec['name']]):
            existing = Organization.objects.filter(name__iexact=name).first()
            if existing:
                break

        if existing:
            # Top up empty profile fields only — never overwrite real values.
            changed = [f for f in fields if not getattr(existing, f) and spec.get(f)]
            for field in changed:
                setattr(existing, field, spec[field])
            if changed:
                existing.save(update_fields=changed)
            self.reused.append((existing, spec['name']))
            return existing

        org, _ = Organization.objects.get_or_create(
            id=org_uuid(spec['name']),
            defaults={k: spec[k] for k in ('name',) + fields + ('gradient_color1', 'gradient_color2')},
        )
        return org

    def seed_keywords(self, org, spec):
        for category, words in spec['keywords'].items():
            for word in words:
                Keyword.objects.get_or_create(organization=org, keyword=word, category=category)

    def seed_competitors(self, org, spec):
        out = []
        for name, aliases, website in spec['competitors']:
            competitor, _ = Competitor.objects.get_or_create(
                organization=org, name=name,
                defaults={'aliases': aliases, 'website': website,
                          'notes': f'Tracked as a direct competitor of {spec["short"]}.'},
            )
            out.append(competitor)
        return out

    def seed_media_sources(self, org, spec):
        for name, source_type, url, handle, reach in spec['sources']:
            MediaSource.objects.get_or_create(
                organization=org, name=name, source_type=source_type,
                defaults={'url': url, 'handle': handle, 'country': 'Botswana', 'reach': reach},
            )

    def seed_campaigns(self, org, spec):
        out = []
        for name, terms, start_offset, length in spec['campaigns']:
            start = TODAY + timedelta(days=start_offset)
            campaign, _ = Campaign.objects.get_or_create(
                organization=org, name=name,
                defaults={
                    'description': f'{name} — coverage matched on {", ".join(terms)}.',
                    'terms': terms,
                    'date_from': start,
                    'date_to': start + timedelta(days=length),
                },
            )
            out.append(campaign)
        return out

    # ── Coverage ─────────────────────────────────────────────────────────────

    def _dates(self, per_month):
        """Yield dates spread back over the requested number of months."""
        for month in range(self.months):
            base = TODAY - timedelta(days=month * 30)
            for _ in range(random.randint(max(1, per_month - 3), per_month + 3)):
                yield base - timedelta(days=random.randint(0, 29))

    def _headline(self, spec, index):
        template = spec['headlines'][index % len(spec['headlines'])]
        return template.format(pct=random.randint(3, 28), amt=random.randint(2, 45))

    def seed_online(self, org, spec):
        created = []
        for i, when in enumerate(self._dates(6)):
            source, logo = random.choice(ONLINE_SOURCES)
            headline = self._headline(spec, i)
            reach = random.randint(4_000, 320_000)
            article, _ = OnlineArticle.objects.get_or_create(
                organization=org, headline=headline, date_published=when,
                defaults={
                    'source': source,
                    'source_logo': logo,
                    'summary': f'{source} reports on {spec["short"]}: {headline}',
                    'url': f'https://{source.split()[0].lower()}.example/{org.id.hex[:6]}/{i}',
                    'country': random.choice(COUNTRIES),
                    'sentiment': random.choice(SENTIMENTS),
                    'ave': Decimal(str(round(reach * random.uniform(0.35, 0.9), 2))),
                    'coverage': random.choice(['Earned', 'Earned', 'Incidental', 'Advocated', 'Not Set']),
                    'reach': reach,
                    'relevancy': round(random.uniform(0.4, 1.0), 2),
                },
            )
            created.append(article)
        return created

    def seed_print(self, org, spec):
        for i, when in enumerate(self._dates(3)):
            headline = self._headline(spec, i + 2)
            reach = random.randint(8_000, 140_000)
            PrintArticle.objects.get_or_create(
                organization=org, headline=headline, date_published=when,
                defaults={
                    'source': random.choice(PRINT_SOURCES),
                    'summary': f'Print coverage of {spec["short"]}.',
                    'author': random.choice(['B. Kgosi', 'L. Tebogo', 'M. Dintwe', 'Staff Reporter']),
                    'section': random.choice(['Main', 'Business', 'Finance', 'Markets', 'Opinion']),
                    'country': random.choice(COUNTRIES),
                    'sentiment': random.choice(SENTIMENTS),
                    'ave': Decimal(str(round(reach * random.uniform(0.5, 1.4), 2))),
                    'reach': reach,
                    'relevancy': round(random.uniform(0.3, 1.0), 2),
                },
            )

    def seed_social(self, org, spec):
        created = []
        for i, when in enumerate(self._dates(9)):
            platform, page = random.choice(SOCIAL_PAGES)
            headline = self._headline(spec, i + 1)
            reach = random.randint(500, 420_000)
            post, _ = SocialMediaPost.objects.get_or_create(
                organization=org, headline=headline, date_published=when,
                defaults={
                    'platform': platform,
                    'page_name': page,
                    'summary': f'{page} on {platform}: {headline}',
                    'url': f'https://{platform.lower()}.example/{org.id.hex[:6]}/{i}',
                    'country': random.choice(COUNTRIES),
                    'sentiment': random.choice(SENTIMENTS),
                    'ave': Decimal(str(round(reach * random.uniform(0.2, 0.6), 2))),
                    'rank': round(random.uniform(1.0, 5.0), 2),
                    'reach': reach,
                    'relevancy': round(random.uniform(0.3, 1.0), 2),
                },
            )
            created.append(post)
        return created

    def seed_broadcast(self, org, spec):
        for i, when in enumerate(self._dates(2)):
            source, kind = random.choice(BROADCAST_SOURCES)
            headline = self._headline(spec, i + 3)
            BroadcastMention.objects.get_or_create(
                organization=org, headline=headline, date_published=when,
                defaults={
                    'source': source,
                    'summary': f'{source} bulletin mentioning {spec["short"]}.',
                    'country': random.choice(['Botswana', 'Botswana', 'South Africa']),
                    'sentiment': random.choice(SENTIMENTS),
                    'ave': Decimal(str(round(random.uniform(15_000, 240_000), 2))),
                    'relevancy': round(random.uniform(0.3, 1.0), 2),
                    'duration': f'{random.randint(0, 6)}:{random.randint(5, 59):02d}',
                    'broadcast_type': kind,
                },
            )

    def seed_competitor_articles(self, org, spec, competitors):
        for i, when in enumerate(self._dates(4)):
            competitor = random.choice(competitors)
            reach = random.randint(3_000, 260_000)
            headline = f'{competitor.name} {random.choice(["expands", "reports results for", "faces scrutiny over", "partners on"])} its {random.choice(["retail", "regional", "digital", "community"])} business'
            CompetitorArticle.objects.get_or_create(
                organization=org, competitor=competitor,
                headline=headline, date_published=when,
                defaults={
                    'company_name': competitor.name,
                    'summary': f'Coverage of {competitor.name}, tracked against {spec["short"]}.',
                    'url': f'https://news.example/{competitor.pk}/{i}',
                    'source': random.choice([s[0] for s in ONLINE_SOURCES]),
                    'country': random.choice(COUNTRIES),
                    'matched_keywords': competitor.match_terms()[0],
                    'sentiment_score': round(random.uniform(-1, 1), 3),
                    'sentiment': random.choice(SENTIMENTS),
                    'reach': reach,
                    'cpm': round(random.uniform(4, 40), 2),
                    'ave': Decimal(str(round(reach * random.uniform(0.3, 0.8), 2))),
                    'rank': round(random.uniform(1, 5), 2),
                    'coverage_type': random.choice(['Earned', 'Incidental', 'Not Set']),
                },
            )

    # ── Derived: reports, analyses, issue reports ────────────────────────────

    def seed_reports(self, org, spec, author, online, social, campaigns):
        window_from = TODAY - timedelta(days=30)

        GeneratedReport.objects.get_or_create(
            organization=org, title=f'{spec["short"]} monthly media review',
            defaults={
                'report_type': 'Monthly Review',
                'modules': ['articles:summary', 'posts:sentiment', 'printmedia:ave', 'broadcast:reach'],
                'scope': ['sentiment', 'reach', 'ave', 'share-of-voice'],
                'created_by': author,
                'date_from': window_from,
                'date_to': TODAY,
            },
        )
        GeneratedReport.objects.get_or_create(
            organization=org, title=f'{spec["short"]} competitor share of voice',
            defaults={
                'report_type': 'Competitor Analysis',
                'modules': ['articles:competitors', 'posts:competitors'],
                'scope': ['share-of-voice', 'sentiment'],
                'created_by': author,
                'date_from': TODAY - timedelta(days=90),
                'date_to': TODAY,
            },
        )

        ReportAnalysis.objects.get_or_create(
            organization=org, date_from=window_from, date_to=TODAY,
            defaults={'payload': {
                'esg': {
                    'environment': f'{spec["short"]} coverage skews to operational stories with limited environmental framing.',
                    'social': 'Community and sponsorship coverage carries the most positive sentiment in the period.',
                    'governance': 'Governance mentions are concentrated in results reporting and regulator commentary.',
                },
                'stakeholders': [
                    {'group': 'Customers', 'sentiment': 'positive', 'note': 'Product and service stories dominate.'},
                    {'group': 'Regulators', 'sentiment': 'neutral', 'note': 'Routine compliance and licensing coverage.'},
                    {'group': 'Investors', 'sentiment': 'mixed', 'note': 'Results coverage split on margin outlook.'},
                ],
                'sectorial': {
                    'leaders': [c[0] for c in spec['competitors'][:2]],
                    'commentary': f'{spec["short"]} holds the largest share of voice in its sector this period.',
                },
                'generated_by': 'seed_system',
            }},
        )

        issue_title, issue_query = spec['issue']
        online_ids = [a.pk for a in online[:14]]
        social_ids = [p.pk for p in social[:10]]
        IssueReport.objects.get_or_create(
            organization=org, title=issue_title,
            defaults={
                'kind': 'issue',
                'issue_query': issue_query,
                'date_from': TODAY - timedelta(days=60),
                'date_to': TODAY,
                'candidate_ids': {'online': online_ids, 'social': social_ids, 'print': [], 'broadcast': []},
                'selected_ids': {'online': online_ids[:8], 'social': social_ids[:5], 'print': [], 'broadcast': []},
                'payload': {
                    'executive_summary': f'Coverage of "{issue_title}" peaked mid-window and then decayed.',
                    'timeline': [
                        {'date': str(TODAY - timedelta(days=52)), 'event': 'First reports appear online.'},
                        {'date': str(TODAY - timedelta(days=41)), 'event': 'Organisation issues a public statement.'},
                        {'date': str(TODAY - timedelta(days=22)), 'event': 'Coverage volume returns to baseline.'},
                    ],
                    'framing': ['operational risk', 'customer impact', 'regulatory response'],
                    'risks': ['Reputational spillover into unrelated product coverage.'],
                    'recommendations': ['Publish a resolution update.', 'Brief business desks directly.'],
                },
                'created_by': author,
            },
        )

        for campaign in campaigns:
            IssueReport.objects.get_or_create(
                organization=org, title=f'{campaign.name} campaign report',
                defaults={
                    'kind': 'campaign',
                    'issue_query': ' OR '.join(campaign.terms),
                    'date_from': campaign.date_from or (TODAY - timedelta(days=60)),
                    'date_to': campaign.date_to or TODAY,
                    'candidate_ids': {'online': online_ids[:6], 'social': social_ids[:6],
                                      'print': [], 'broadcast': []},
                    'selected_ids': {'online': online_ids[:4], 'social': social_ids[:4],
                                     'print': [], 'broadcast': []},
                    'payload': {
                        'executive_summary': f'{campaign.name} generated steady social pickup against modest earned coverage.',
                        'framing': campaign.terms,
                        'recommendations': ['Sustain social cadence into the next flight.'],
                    },
                    'created_by': author,
                },
            )

    # ── Notification layer ───────────────────────────────────────────────────

    def seed_alerts(self, org, spec, users):
        admin = users['org_admin']
        recipients = ','.join(sorted({admin.email, users[f'{spec["short"].lower()}.comms'].email}))
        brand_terms = ', '.join(spec['keywords']['brand'][:4])

        Alert.objects.get_or_create(
            organization=org, name=f'{spec["short"]} brand mentions',
            defaults={
                'keywords': brand_terms,
                'email': admin.email,
                'recipients': recipients,
                'frequency': 'daily',
                'email_subject': f'{spec["short"]} — daily media digest',
                'start_date': TODAY - timedelta(days=90),
                'categories': ['mention'],
            },
        )
        Alert.objects.get_or_create(
            organization=org, name=f'{spec["short"]} competitor watch',
            defaults={
                'keywords': ', '.join(c[0] for c in spec['competitors']),
                'email': admin.email,
                'recipients': recipients,
                'frequency': 'weekly',
                'email_subject': f'{spec["short"]} — weekly competitor roundup',
                'start_date': TODAY - timedelta(days=180),
                'categories': ['mention', 'report'],
            },
        )
        Alert.objects.get_or_create(
            organization=org, name=f'{spec["short"]} executive briefing',
            defaults={
                'keywords': ', '.join(spec['keywords']['personnel']),
                'email': admin.email,
                'recipients': admin.email,
                'frequency': 'immediate',
                'email_subject': f'{spec["short"]} — executive mention',
                'start_date': TODAY - timedelta(days=30),
                'categories': [],
                'is_active': False,
            },
        )

    def seed_events(self, org, spec):
        report = GeneratedReport.objects.filter(organization=org).first()
        issue = IssueReport.objects.filter(organization=org, kind='issue').first()
        rows = [
            ('report', 'report_generated', f'{spec["short"]} monthly media review is ready',
             'The monthly review for the last 30 days has been generated.', report),
            ('report', 'issue_report_created', f'Issue report: {spec["issue"][0]}',
             'An issue-focused report was created from selected coverage.', issue),
            ('mention', 'coverage_captured', f'New coverage captured for {spec["short"]}',
             'The crawler pushed newly matched online coverage into the platform.', None),
            ('system', 'keywords_updated', f'{spec["short"]} tracking keywords updated',
             'Brand, personnel and campaign keyword sets were refreshed.', None),
        ]
        for category, event_type, title, summary, obj in rows:
            defaults = {'summary': summary, 'url': ''}
            if obj is not None:
                from django.contrib.contenttypes.models import ContentType
                defaults['content_type'] = ContentType.objects.get_for_model(obj)
                defaults['object_id'] = str(obj.pk)
            Event.objects.get_or_create(
                organization=org, category=category, event_type=event_type,
                title=title, defaults=defaults,
            )

    # ── Output ───────────────────────────────────────────────────────────────

    def report(self, platform_admins, orgs):
        self.stdout.write('')
        self.stdout.write(self.style.SUCCESS('Platform seeded.'))
        if self.reused:
            self.stdout.write('  Reused organisations already in this database '
                              '(no duplicate created):')
            for org, wanted in self.reused:
                note = '' if org.name == wanted else f'  <- matched "{wanted}"'
                self.stdout.write(f'    {org.name}  [{org.id}]{note}')
        self.stdout.write(f'  Platform admins (no organisation): {len(platform_admins)}')
        for user in platform_admins:
            self.stdout.write(f'    {user.email}  role={user.role}  superuser={user.is_superuser}')
        for org in orgs:
            self.stdout.write('')
            self.stdout.write(f'  {org.name}  [{org.id}]')
            self.stdout.write(
                f'    users={org.members.count()} keywords={org.keywords.count()} '
                f'competitors={org.competitors.count()} sources={org.media_sources.count()} '
                f'campaigns={org.campaigns.count()}'
            )
            self.stdout.write(
                f'    online={org.online_articles.count()} print={org.print_articles.count()} '
                f'social={org.social_posts.count()} broadcast={org.broadcast_mentions.count()} '
                f'competitor_articles={org.competitor_articles.count()}'
            )
            self.stdout.write(
                f'    reports={org.generated_reports.count()} analyses={org.report_analyses.count()} '
                f'issue_reports={org.issue_reports.count()} alerts={org.alerts.count()} '
                f'events={org.events.count()}'
            )
            for member in org.members.order_by('role', 'username'):
                self.stdout.write(f'      login {member.email:34} {member.role}')
        self.stdout.write('')
        self.stdout.write(f'  Every seeded account logs in with the password: {SEED_PASSWORD}')
