from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import date, timedelta
import random

from monitor.models import (
    Organization, User, Keyword, Competitor,
    OnlineArticle, PrintArticle, SocialMediaPost, BroadcastMention, Alert
)


class Command(BaseCommand):
    help = 'Seed the database with demo data matching the Social Light screenshots'

    def handle(self, *args, **options):
        self.stdout.write('Seeding demo data...')

        # ── Admin user ────────────────────────────────
        if not User.objects.filter(username='tony').exists():
            User.objects.create_superuser(
                username='tony',
                email='tony@sociallightbw.com',
                password='sociallight123',
                first_name='Tony',
                last_name='Mautsu',
                role='platform_admin',
            )
            self.stdout.write('  Created user: tony / sociallight123')

        # ── Organizations ────────────────────────────
        orgs_data = [
            {'name': 'First National Bank of Botswana', 'email': 'oratile@inq.inc', 'industry': 'Banking & Financial Services', 'country': 'Botswana'},
            {'name': 'Debswana', 'email': 'oratile@test.com', 'industry': 'Mining & Metals', 'country': 'Botswana'},
            {'name': 'Khoemacau Copper Mining', 'email': 'support@sociallightbw.com', 'industry': 'Mining & Metals', 'country': 'Botswana'},
            {'name': 'South African Universities of Technology', 'email': 'support@sociallightbw.com', 'industry': 'Education & Research', 'country': 'South Africa'},
        ]

        orgs = {}
        for od in orgs_data:
            org, _ = Organization.objects.get_or_create(name=od['name'], defaults=od)
            orgs[od['name']] = org
            self.stdout.write(f'  Org: {org.name}')

        fnbb = orgs['First National Bank of Botswana']

        # ── Additional users ──────────────────────────
        extra_users = [
            {'username': 'oratile', 'email': 'gaotlolweame@gmail.com', 'first_name': 'Oratile', 'last_name': 'Busang', 'role': 'platform_admin'},
            {'username': 'lettah', 'email': 'lettah@inq.co.bw', 'first_name': 'Lettah', 'last_name': 'Koshwane', 'role': 'platform_admin'},
            {'username': 'arnold', 'email': 'support@sociallightbw.com', 'first_name': 'Arnold', 'last_name': 'Aobakwe Tshikinya', 'role': 'platform_admin'},
            {'username': 'tshepo', 'email': 'design@sociallightbw.com', 'first_name': 'Tshepo', 'last_name': 'Mafule', 'role': 'platform_admin'},
        ]
        for ud in extra_users:
            if not User.objects.filter(username=ud['username']).exists():
                User.objects.create_user(password='changeme123', **ud)

        # ── Keywords for FNBB ──────────────────────────
        keywords = ['FNBB', 'FNB Botswana', 'First National Bank', 'ABSA', 'Stanbic', 'BancABC', 'BBS', 'BSB', 'Barclays', 'Standard Chartered', 'Letshego', 'NDB', 'BIFM', 'Pula', 'BWP']
        for kw in keywords:
            Keyword.objects.get_or_create(organization=fnbb, keyword=kw)

        # ── Competitors for FNBB ───────────────────────
        comps = [
            {'name': 'Absa Bank Botswana', 'website': 'https://absabotswana.co.bw'},
            {'name': 'Stanbic Bank Botswana', 'website': 'https://stanbicbank.co.bw'},
            {'name': 'Standard Chartered Botswana', 'website': 'https://sc.com/bw'},
            {'name': 'BancABC Botswana', 'website': 'https://bancabc.co.bw'},
        ]
        for cd in comps:
            Competitor.objects.get_or_create(organization=fnbb, name=cd['name'], defaults=cd)

        # ── Online Articles ────────────────────────────
        online_articles = [
            {
                'source': 'Botswana Gazette', 'headline': '*FNB Botswana and Africa\'s Eden Tourism announce Strategic Collaboration to Support Tourism',
                'url': '', 'date_published': date(2026, 4, 16), 'country': 'Botswana',
                'sentiment': 'positive', 'ave': 41987.40, 'coverage': 'Earned', 'reach': 79976,
            },
            {
                'source': 'Botswana Gazette', 'headline': '*Botswana is experiencing unprecedented heat wave conditions',
                'url': '', 'date_published': date(2026, 4, 16), 'country': 'Botswana',
                'sentiment': 'positive', 'ave': 28000.00, 'coverage': 'Not Set', 'reach': 80000,
            },
            {
                'source': 'Business Weekly', 'headline': '*Access Bank Botswana says growing customer base amid competitive landscape',
                'url': '', 'date_published': date(2026, 4, 23), 'country': 'Botswana',
                'sentiment': 'positive', 'ave': 12600.00, 'coverage': 'Advocated', 'reach': 30000,
            },
            {
                'source': 'Businessday', 'headline': '*Tariffs on Chinese cars unlikely to trigger domestic market price hikes',
                'url': '', 'date_published': date(2026, 4, 30), 'country': 'South Africa',
                'sentiment': 'neutral', 'ave': 210000.00, 'coverage': 'Earned', 'reach': 400000,
            },
            {
                'source': 'News & All Media', 'headline': '*FNBB and Africa\'s Eden Tourism forge partnership',
                'url': '', 'date_published': date(2026, 5, 4), 'country': 'Botswana',
                'sentiment': 'positive', 'ave': 3500.00, 'coverage': 'Incidental', 'reach': 20000,
            },
        ]

        for months_back in range(12):
            count = random.randint(2, 20)
            base_date = date(2026, 5, 1) - timedelta(days=months_back * 30)
            for i in range(count):
                d = base_date - timedelta(days=random.randint(0, 28))
                OnlineArticle.objects.get_or_create(
                    organization=fnbb,
                    headline=f'FNBB coverage item {months_back}-{i}',
                    date_published=d,
                    defaults={
                        'source': random.choice(['Botswana Gazette', 'Daily News', 'Mmegi', 'Sunday Standard']),
                        'country': random.choice(['Botswana', 'South Africa', 'Zimbabwe']),
                        'sentiment': random.choice(['positive', 'neutral', 'negative']),
                        'ave': round(random.uniform(1000, 50000), 2),
                        'coverage': random.choice(['Earned', 'Incidental', 'Advocated', 'Not Set']),
                        'reach': random.randint(1000, 200000),
                    }
                )

        for data in online_articles:
            OnlineArticle.objects.get_or_create(
                organization=fnbb,
                headline=data['headline'],
                date_published=data['date_published'],
                defaults=data,
            )

        # ── Print Articles ─────────────────────────────
        print_articles = [
            {
                'source': 'Monitor (Daily) Uganda', 'headline': '* 500 employees affected as Letshego Uganda undergoes restructuring',
                'date_published': date(2026, 5, 5), 'country': 'Unknown',
                'sentiment': 'neutral', 'ave': 55553.85, 'section': 'Main',
            },
            {
                'source': 'Monitor (Daily) Uganda', 'headline': '"ABSA..." bank posts strong quarterly results',
                'date_published': date(2026, 5, 5), 'country': 'Unknown',
                'sentiment': 'neutral', 'ave': 55553.85, 'section': 'Main',
            },
            {
                'source': 'Cape Times', 'headline': '"Manufacturing activity returns to positive territory in April"',
                'date_published': date(2026, 5, 5), 'country': 'Unknown',
                'sentiment': 'neutral', 'ave': 56381.56, 'section': 'BusinessReport',
            },
            {
                'source': 'Daily Nation (Kenya)', 'headline': '* StanChart puts Chiromo head office on sale',
                'date_published': date(2026, 5, 5), 'country': 'Unknown',
                'sentiment': 'neutral', 'ave': 14977.35, 'section': 'Main',
            },
            {
                'source': 'New Vision (Uganda)', 'headline': '"STANBIC BANK\'S ROLE IN UGANDA\'S ECONOMIC GROWTH"',
                'date_published': date(2026, 5, 5), 'country': 'Unknown',
                'sentiment': 'neutral', 'ave': 95270.97, 'section': 'Main',
            },
        ]

        for months_back in range(12):
            count = random.randint(1, 8)
            base_date = date(2026, 5, 1) - timedelta(days=months_back * 30)
            for i in range(count):
                d = base_date - timedelta(days=random.randint(0, 28))
                PrintArticle.objects.get_or_create(
                    organization=fnbb,
                    headline=f'Print coverage {months_back}-{i}',
                    date_published=d,
                    defaults={
                        'source': random.choice(['Sunday Standard', 'Botswana Guardian', 'Mmegi', 'Voice']),
                        'country': random.choice(['Botswana', 'South Africa', 'Kenya', 'Uganda']),
                        'sentiment': random.choice(['positive', 'neutral', 'negative']),
                        'ave': round(random.uniform(5000, 100000), 2),
                        'section': random.choice(['Main', 'Business', 'Finance', 'Markets']),
                    }
                )

        for data in print_articles:
            PrintArticle.objects.get_or_create(
                organization=fnbb,
                headline=data['headline'],
                date_published=data['date_published'],
                defaults=data,
            )

        # ── Social Media Posts ─────────────────────────
        social_posts = [
            {
                'platform': 'Facebook', 'page_name': 'National Arts Council of Botswana',
                'headline': '*FNBB FOUNDATION supports arts and culture initiative',
                'date_published': date(2026, 5, 6), 'country': 'Botswana',
                'sentiment': 'positive', 'ave': 9450.00, 'rank': 3.54, 'reach': 27000,
            },
            {
                'platform': 'Facebook', 'page_name': 'The People\'s Daily News Online',
                'headline': '*STANBIC BANK partners with SMEs in Botswana',
                'date_published': date(2026, 5, 6), 'country': 'Botswana',
                'sentiment': 'positive', 'ave': 66850.00, 'rank': 3.79, 'reach': 191000,
            },
            {
                'platform': 'Facebook', 'page_name': 'The Business Weekly & Review',
                'headline': '*FNB Botswana launches new digital banking features',
                'date_published': date(2026, 5, 6), 'country': 'Botswana',
                'sentiment': 'positive', 'ave': 63350.00, 'rank': 3.77, 'reach': 181000,
            },
        ]

        for months_back in range(12):
            count = random.randint(5, 80)
            base_date = date(2026, 5, 1) - timedelta(days=months_back * 30)
            for i in range(count):
                d = base_date - timedelta(days=random.randint(0, 28))
                SocialMediaPost.objects.get_or_create(
                    organization=fnbb,
                    headline=f'Social post {months_back}-{i}',
                    date_published=d,
                    defaults={
                        'platform': random.choice(['Facebook', 'X', 'Instagram', 'LinkedIn']),
                        'page_name': random.choice(['FNBB Official', 'Business Daily', 'Financial Times BW']),
                        'country': random.choice(['Botswana', 'South Africa', 'Zimbabwe', 'Kenya']),
                        'sentiment': random.choice(['positive', 'neutral', 'negative']),
                        'ave': round(random.uniform(500, 70000), 2),
                        'rank': round(random.uniform(1, 5), 2),
                        'reach': random.randint(1000, 300000),
                    }
                )

        for data in social_posts:
            SocialMediaPost.objects.get_or_create(
                organization=fnbb,
                headline=data['headline'],
                date_published=data['date_published'],
                defaults=data,
            )

        # ── Broadcast ──────────────────────────────────
        for months_back in range(12):
            count = random.randint(0, 5)
            base_date = date(2026, 5, 1) - timedelta(days=months_back * 30)
            for i in range(count):
                d = base_date - timedelta(days=random.randint(0, 28))
                BroadcastMention.objects.get_or_create(
                    organization=fnbb,
                    headline=f'Broadcast mention {months_back}-{i}',
                    date_published=d,
                    defaults={
                        'source': random.choice(['BTV', 'RB1', 'RB2', 'eTV', 'SABC']),
                        'country': random.choice(['Botswana', 'South Africa']),
                        'sentiment': random.choice(['positive', 'neutral']),
                        'ave': round(random.uniform(10000, 200000), 2),
                        'duration': f'{random.randint(1,5)}:{random.randint(10,59):02d}',
                    }
                )

        # ── Alerts ─────────────────────────────────────
        Alert.objects.get_or_create(
            organization=fnbb, name='FNBB brand mentions',
            defaults={'keywords': 'FNBB, FNB Botswana, First National Bank', 'email': 'tony@sociallightbw.com', 'frequency': 'daily'},
        )

        counts = {
            'online': OnlineArticle.objects.filter(organization=fnbb).count(),
            'print': PrintArticle.objects.filter(organization=fnbb).count(),
            'social': SocialMediaPost.objects.filter(organization=fnbb).count(),
            'broadcast': BroadcastMention.objects.filter(organization=fnbb).count(),
        }

        self.stdout.write(self.style.SUCCESS(
            f'\nDone! FNBB data: {counts["online"]} online, {counts["print"]} print, '
            f'{counts["social"]} social, {counts["broadcast"]} broadcast articles.\n'
            f'Login: tony / sociallight123'
        ))
