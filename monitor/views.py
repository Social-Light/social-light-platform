import csv
import io
import json
import re
import calendar
from datetime import date, datetime, timedelta
from collections import defaultdict, Counter

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.db.models import Count, Sum, Q
from django.utils import timezone

from .models import (
    Organization, User, Keyword, Competitor,
    OnlineArticle, PrintArticle, SocialMediaPost, BroadcastMention, Alert, MediaSource,
    GeneratedReport,
    SENTIMENT_CHOICES, COVERAGE_CHOICES, PLATFORM_CHOICES, INDUSTRY_CHOICES, ROLE_CHOICES,
    SOURCE_TYPE_CHOICES,
)

COMPETITOR_SUGGESTIONS = {
    'Banking & Financial Services': [
        'Letshego Holdings Limited', 'Access Bank Botswana', 'Standard Chartered Botswana',
        'Stanbic Bank Botswana', 'Bank Gaborone', 'Orange Money', 'Mascom Myzaka',
        'BTC Smega', 'First Capital Bank Botswana', 'ABSA Botswana', 'BBS Bank',
        'Absa / Bank', 'Bank Baroda', 'Botswana Building Society',
        'Botswana Savings Bank (BSB)', 'National Development Bank (NDB)',
        'Botswana Development Corporation (BDCP)', 'PosoMoney',
    ],
    'Telecommunications': [
        'Mascom Wireless', 'Orange Botswana', 'BTC (Botswana Telecommunications Corporation)',
        'Liquid Telecom', 'Smartcom', 'Botswana Fibre Networks',
    ],
    'Retail': [
        'Choppies', 'Pick n Pay Botswana', 'Spar Botswana', 'Woolworths Botswana',
        'Mr Price Group', 'Edgars Botswana', 'Shoprite Botswana',
    ],
    'Mining & Metals': [
        'Debswana', 'BCL Mine', 'Lucara Diamond', 'Botswana Diamonds',
        'Sandfire Resources', 'African Copper',
    ],
    'Government': [
        'BURS', 'PEEPA', 'BITC', 'BOFINET', 'BPOPF', 'NBFIRA',
        'Bank of Botswana', 'Botswana Stock Exchange',
    ],
    'Healthcare': [
        'Princess Marina Hospital', 'Sidilega Private Hospital', 'Bokamoso Private Hospital',
        'Gaborone Private Hospital', 'NHP Healthcare', 'Botswana Medical Aid Society',
    ],
    'Education & Research': [
        'University of Botswana', 'Botho University', 'BIUST', 'ABM University',
        'Ba Isago University', 'Limkokwing University',
    ],
    'Technology': [
        'Botswana Innovation Hub', 'BNOC', 'EON Group', 'Fourth Dimension Technologies',
        'Payway', 'Seriti Technologies',
    ],
    'Energy': [
        'BPC (Botswana Power Corporation)', 'BERA', 'Orion Energy', 'Kgale Hill Utilities',
    ],
}

MEDIA_PAGE_SIZE = 30

def paginate_media(request, qs, page_size=MEDIA_PAGE_SIZE):
    page = request.GET.get('page', '1')
    try:
        page = int(page)
    except (TypeError, ValueError):
        page = 1
    page = max(page, 1)
    offset = (page - 1) * page_size
    total = qs.count()
    page_qs = qs[offset:offset + page_size]
    current_loaded = offset + page_qs.count()
    if current_loaded > total:
        current_loaded = total
    has_more = offset + page_size < total
    next_params = request.GET.copy()
    next_params['page'] = page + 1
    next_page_url = f"{request.path}?{next_params.urlencode()}"
    return page_qs, total, current_loaded, has_more, next_page_url


# ── Public ───────────────────────────────────────────────────────────────────

def home(request):
    if request.user.is_authenticated:
        return redirect('monitor:organizations')
    return render(request, 'monitor/landing.html')


# ── Auth ─────────────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('monitor:organizations')
    error = None
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        user = None
        try:
            db_user = User.objects.get(email__iexact=email)
            user = authenticate(request, username=db_user.username, password=password)
        except User.DoesNotExist:
            pass
        if user:
            login(request, user)
            next_url = request.GET.get('next', '')
            if next_url:
                return redirect(next_url)
            if user.role == 'platform_admin':
                return redirect('/app/organizations/?select=1')
            return redirect('/app/organizations/')
        error = 'Invalid email or password.'
    return render(request, 'monitor/login.html', {'error': error})


def logout_view(request):
    logout(request)
    return redirect('monitor:home')


# ── Organizations ─────────────────────────────────────────────────────────────

@login_required
def organizations(request):
    orgs = Organization.objects.all().order_by('name')
    return render(request, 'monitor/organizations.html', {
        'orgs': orgs,
        'industry_choices': INDUSTRY_CHOICES,
        'page': 'organizations',
    })


@login_required
def manage_organizations(request):
    orgs = Organization.objects.all().order_by('name')
    return render(request, 'monitor/manage_organizations.html', {
        'orgs': orgs,
        'industry_choices': INDUSTRY_CHOICES,
        'page': 'manage_organizations',
    })


@login_required
def settings_view(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    all_orgs = Organization.objects.all().order_by('name')
    return render(request, 'monitor/settings.html', {
        'org': org,
        'all_orgs': all_orgs,
        'page': 'settings',
    })


@login_required
@require_http_methods(['POST'])
def organization_create(request):
    # Accept multipart (with logo) or JSON
    if request.content_type and 'multipart' in request.content_type:
        data = request.POST
        get = lambda k, d='': data.get(k, d)
        org = Organization.objects.create(
            name=get('name').strip(),
            email=get('email').strip(),
            industry=get('industry'),
            country=get('country', 'Botswana'),
            status=get('status', 'active'),
            address=get('address').strip(),
            phone=get('phone').strip(),
            website=get('website').strip(),
            facebook_url=get('facebook_url').strip(),
            linkedin_url=get('linkedin_url').strip(),
            x_handle=get('x_handle').strip(),
            gradient_color1=get('gradient_color1', '#1d4ed8'),
            gradient_color2=get('gradient_color2', '#0f172a'),
        )
        if 'logo' in request.FILES:
            org.logo = request.FILES['logo']
            org.save()
    else:
        data = json.loads(request.body)
        org = Organization.objects.create(
            name=data.get('name', '').strip(),
            email=data.get('email', '').strip(),
            industry=data.get('industry', ''),
            country=data.get('country', 'Botswana'),
            status=data.get('status', 'active'),
        )
    return JsonResponse({'id': str(org.id), 'name': org.name})


@login_required
@require_http_methods(['PUT', 'POST'])
def organization_update(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    if request.content_type and 'multipart' in request.content_type:
        data = request.POST
        org.name = data.get('name', org.name).strip()
        org.email = data.get('email', org.email).strip()
        org.industry = data.get('industry', org.industry)
        org.country = data.get('country', org.country)
        org.status = data.get('status', org.status)
        org.address = data.get('address', org.address).strip()
        org.phone = data.get('phone', org.phone).strip()
        org.website = data.get('website', org.website).strip()
        org.facebook_url = data.get('facebook_url', org.facebook_url).strip()
        org.linkedin_url = data.get('linkedin_url', org.linkedin_url).strip()
        org.x_handle = data.get('x_handle', org.x_handle).strip()
        org.gradient_color1 = data.get('gradient_color1', org.gradient_color1)
        org.gradient_color2 = data.get('gradient_color2', org.gradient_color2)
        if 'logo' in request.FILES:
            org.logo = request.FILES['logo']
    else:
        data = json.loads(request.body)
        org.name = data.get('name', org.name).strip()
        org.email = data.get('email', org.email).strip()
        org.industry = data.get('industry', org.industry)
        org.country = data.get('country', org.country)
        org.status = data.get('status', org.status)
    org.save()
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['GET'])
def org_details(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    keywords = list(org.keywords.values('id', 'keyword', 'category').order_by('category', 'keyword'))
    competitors = list(org.competitors.values('id', 'name').order_by('name'))
    suggestions = COMPETITOR_SUGGESTIONS.get(org.industry, [])
    existing_names = {c['name'] for c in competitors}
    # Collect unique categories in a stable order (brand, personnel, campaign first, then custom)
    default_cats = ['brand', 'personnel', 'campaign']
    used_cats = list(dict.fromkeys(kw['category'] for kw in keywords))
    extra_cats = [c for c in used_cats if c not in default_cats]
    categories = default_cats + extra_cats
    return JsonResponse({
        'id': str(org.id),
        'name': org.name,
        'email': org.email,
        'industry': org.industry,
        'country': org.country,
        'status': org.status,
        'address': org.address,
        'phone': org.phone,
        'website': org.website,
        'facebook_url': org.facebook_url,
        'linkedin_url': org.linkedin_url,
        'x_handle': org.x_handle,
        'logo_url': org.logo.url if org.logo else '',
        'gradient_color1': org.gradient_color1,
        'gradient_color2': org.gradient_color2,
        'keywords': keywords,
        'keyword_categories': categories,
        'competitors': competitors,
        'suggestions': [s for s in suggestions if s not in existing_names],
    })


@login_required
@require_http_methods(['DELETE'])
def organization_delete(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    org.delete()
    return JsonResponse({'ok': True})


# ── Dashboard ─────────────────────────────────────────────────────────────────

@login_required
def dashboard(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    today = date.today()
    month_start = today.replace(day=1)

    total_online = org.online_articles.count()
    total_print = org.print_articles.count()
    total_social = org.social_posts.count()
    total_broadcast = org.broadcast_mentions.count()
    total_mentions = total_online + total_print + total_social + total_broadcast

    month_online = org.online_articles.filter(date_published__gte=month_start).count()
    month_print = org.print_articles.filter(date_published__gte=month_start).count()
    month_social = org.social_posts.filter(date_published__gte=month_start).count()
    month_broadcast = org.broadcast_mentions.filter(date_published__gte=month_start).count()
    monthly_mentions = month_online + month_print + month_social + month_broadcast

    keywords = org.keywords.all()

    # Monthly chart data for current year
    months = list(calendar.month_abbr)[1:]
    online_monthly = _monthly_counts(org.online_articles, today.year)
    print_monthly = _monthly_counts(org.print_articles, today.year)
    social_monthly = _monthly_counts(org.social_posts, today.year)
    broadcast_monthly = _monthly_counts(org.broadcast_mentions, today.year)

    # Keyword trend data (from keywords + article counts this month)
    keyword_trends = _keyword_trends(org, month_start, today)

    # Latest articles (8 each)
    latest_online = org.online_articles.all()[:8]
    latest_print = org.print_articles.all()[:8]
    latest_social = org.social_posts.all()[:8]
    latest_broadcast = org.broadcast_mentions.all()[:8]

    # Media types present
    media_types = []
    if total_online: media_types.append('Online')
    if total_broadcast: media_types.append('Broadcast')
    if total_social: media_types.append('Social')
    if total_print: media_types.append('Print')

    return render(request, 'monitor/dashboard.html', {
        'org': org,
        'page': 'dashboard',
        'total_mentions': total_mentions,
        'monthly_mentions': monthly_mentions,
        'total_keyphrases': keywords.count(),
        'media_types': media_types,
        'total_online': total_online,
        'total_print': total_print,
        'total_social': total_social,
        'total_broadcast': total_broadcast,
        'months_json': json.dumps(months),
        'online_monthly_json': json.dumps(online_monthly),
        'print_monthly_json': json.dumps(print_monthly),
        'social_monthly_json': json.dumps(social_monthly),
        'broadcast_monthly_json': json.dumps(broadcast_monthly),
        'keyword_trends_json': json.dumps(keyword_trends),
        'latest_online': latest_online,
        'latest_print': latest_print,
        'latest_social': latest_social,
        'latest_broadcast': latest_broadcast,
        'current_year': today.year,
    })


def _monthly_counts(queryset, year):
    counts = [0] * 12
    for item in queryset.filter(date_published__year=year).values('date_published__month').annotate(c=Count('id')):
        counts[item['date_published__month'] - 1] = item['c']
    return counts


def _keyword_trends(org, start, end):
    keywords = list(org.keywords.values_list('keyword', flat=True))
    result = []
    for kw in keywords[:10]:
        q = Q(headline__icontains=kw) | Q(summary__icontains=kw)
        count = (
            org.online_articles.filter(date_published__range=(start, end)).filter(q).count() +
            org.print_articles.filter(date_published__range=(start, end)).filter(q).count() +
            org.social_posts.filter(date_published__range=(start, end)).filter(q).count()
        )
        result.append({'label': kw, 'value': count})
    return result


# ── Analytics ─────────────────────────────────────────────────────────────────

@login_required
def analytics(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    media_type = request.GET.get('type', 'social')
    today = date.today()

    if media_type == 'online':
        qs = org.online_articles.all()
        title = 'Online Articles'
    elif media_type == 'print':
        qs = org.print_articles.all()
        title = 'Print Media'
    elif media_type == 'broadcast':
        qs = org.broadcast_mentions.all()
        title = 'Broadcast'
    else:
        qs = org.social_posts.all()
        title = 'Social Posts'

    total = qs.count()
    month_start = today.replace(day=1)
    this_month = qs.filter(date_published__gte=month_start).count()
    total_ave = qs.aggregate(s=Sum('ave'))['s'] or 0

    # Active topics from keywords
    active_topics = list(org.keywords.values_list('keyword', flat=True)[:6])

    # Yearly trend
    years = list(range(today.year - 4, today.year + 1))
    yearly_counts = []
    for y in years:
        yearly_counts.append(qs.filter(date_published__year=y).count())

    # Sentiment breakdown
    pos = qs.filter(sentiment='positive').count()
    neu = qs.filter(sentiment='neutral').count()
    neg = qs.filter(sentiment='negative').count()

    # Top sources
    source_field = 'source' if hasattr(qs.model, 'source') else 'platform'
    top_sources = list(
        qs.values(source_field).annotate(c=Count('id')).order_by('-c')[:10]
    )
    top_sources_labels = [row.get(source_field) or 'Unknown' for row in top_sources]
    top_sources_values = [row['c'] for row in top_sources]

    # Countries
    top_countries = list(
        qs.exclude(country='').values('country').annotate(c=Count('id')).order_by('-c')[:10]
    )
    color_palette = ['#ef4444', '#f59e0b', '#3b82f6', '#10b981', '#8b5cf6', '#06b6d4', '#a855f7', '#f43f5e', '#eab308', '#22c55e']
    for idx, row in enumerate(top_countries):
        row['color'] = color_palette[idx % len(color_palette)]

    return render(request, 'monitor/analytics.html', {
        'org': org,
        'page': 'analytics',
        'media_type': media_type,
        'title': title,
        'total': total,
        'this_month': this_month,
        'total_ave': total_ave,
        'active_topics': active_topics,
        'years_json': json.dumps(years),
        'yearly_counts_json': json.dumps(yearly_counts),
        'pos': pos,
        'neu': neu,
        'neg': neg,
        'top_sources': top_sources,
        'top_sources_labels_json': json.dumps(top_sources_labels),
        'top_sources_values_json': json.dumps(top_sources_values),
        'top_countries': top_countries,
        'top_countries_json': json.dumps([{'country': row['country'], 'c': row['c']} for row in top_countries]),
        'source_field': source_field,
        'mapbox_access_token': settings.MAPBOX_ACCESS_TOKEN,
    })


# ── Media: Online Articles ────────────────────────────────────────────────────

@login_required
def media_online(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    qs = org.online_articles.all()

    q = request.GET.get('q', '')
    sentiment = request.GET.get('sentiment', '')
    country = request.GET.get('country', '')
    coverage = request.GET.get('coverage', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if q:
        qs = qs.filter(Q(headline__icontains=q) | Q(source__icontains=q))
    if sentiment:
        qs = qs.filter(sentiment=sentiment)
    if country:
        qs = qs.filter(country=country)
    if coverage:
        qs = qs.filter(coverage=coverage)
    if date_from:
        qs = qs.filter(date_published__gte=date_from)
    if date_to:
        qs = qs.filter(date_published__lte=date_to)

    countries = list(org.online_articles.exclude(country='').values_list('country', flat=True).distinct().order_by('country'))
    articles, total, current_count, has_more, next_page_url = paginate_media(request, qs)

    return render(request, 'monitor/media_online.html', {
        'org': org,
        'page': 'media',
        'articles': articles,
        'total': total,
        'current_count': current_count,
        'has_more': has_more,
        'next_page_url': next_page_url,
        'q': q,
        'sentiment_choices': SENTIMENT_CHOICES,
        'coverage_choices': COVERAGE_CHOICES,
        'countries': countries,
        'selected_sentiment': sentiment,
        'selected_country': country,
        'selected_coverage': coverage,
        'date_from': date_from,
        'date_to': date_to,
    })


@login_required
@require_http_methods(['POST'])
def online_article_create(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    data = json.loads(request.body)
    article = OnlineArticle.objects.create(
        organization=org,
        source=data.get('source', '').strip(),
        headline=data.get('headline', '').strip(),
        summary=data.get('summary', '').strip(),
        url=data.get('url', '').strip(),
        date_published=data.get('date_published') or date.today(),
        country=data.get('country', '').strip(),
        sentiment=data.get('sentiment', 'neutral'),
        ave=float(data.get('ave', 0) or 0),
        coverage=data.get('coverage', 'Not Set'),
        reach=int(data.get('reach', 0) or 0),
        relevancy=float(data.get('relevancy', 0) or 0),
    )
    return JsonResponse({'id': article.id, 'headline': article.headline[:60]})


@login_required
@require_http_methods(['PUT'])
def online_article_update(request, org_id, article_id):
    org = get_object_or_404(Organization, id=org_id)
    article = get_object_or_404(OnlineArticle, id=article_id, organization=org)
    data = json.loads(request.body)
    for field in ['source', 'headline', 'summary', 'url', 'country', 'sentiment', 'coverage']:
        if field in data:
            setattr(article, field, data[field].strip() if isinstance(data[field], str) else data[field])
    if 'ave' in data:
        article.ave = float(data['ave'] or 0)
    if 'reach' in data:
        article.reach = int(data['reach'] or 0)
    if 'relevancy' in data:
        article.relevancy = float(data['relevancy'] or 0)
    if 'date_published' in data and data['date_published']:
        article.date_published = data['date_published']
    article.save()
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['DELETE'])
def online_article_delete(request, org_id, article_id):
    org = get_object_or_404(Organization, id=org_id)
    article = get_object_or_404(OnlineArticle, id=article_id, organization=org)
    article.delete()
    return JsonResponse({'ok': True})


# ── Media: Print Articles ─────────────────────────────────────────────────────

@login_required
def media_print(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    qs = org.print_articles.all()

    q = request.GET.get('q', '')
    sentiment = request.GET.get('sentiment', '')
    country = request.GET.get('country', '')
    section = request.GET.get('section', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if q:
        qs = qs.filter(Q(headline__icontains=q) | Q(source__icontains=q) | Q(author__icontains=q))
    if sentiment:
        qs = qs.filter(sentiment=sentiment)
    if country:
        qs = qs.filter(country=country)
    if section:
        qs = qs.filter(section__icontains=section)
    if date_from:
        qs = qs.filter(date_published__gte=date_from)
    if date_to:
        qs = qs.filter(date_published__lte=date_to)

    countries = list(org.print_articles.exclude(country='').values_list('country', flat=True).distinct().order_by('country'))
    sections = list(org.print_articles.exclude(section='').values_list('section', flat=True).distinct().order_by('section'))
    articles, total, current_count, has_more, next_page_url = paginate_media(request, qs)

    return render(request, 'monitor/media_print.html', {
        'org': org,
        'page': 'media',
        'articles': articles,
        'total': total,
        'current_count': current_count,
        'has_more': has_more,
        'next_page_url': next_page_url,
        'q': q,
        'sentiment_choices': SENTIMENT_CHOICES,
        'countries': countries,
        'sections': sections,
        'selected_sentiment': sentiment,
        'selected_country': country,
        'selected_section': section,
        'date_from': date_from,
        'date_to': date_to,
    })


@login_required
@require_http_methods(['POST'])
def print_article_create(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    data = json.loads(request.body)
    article = PrintArticle.objects.create(
        organization=org,
        source=data.get('source', '').strip(),
        headline=data.get('headline', '').strip(),
        summary=data.get('summary', '').strip(),
        author=data.get('author', '').strip(),
        section=data.get('section', '').strip(),
        url=data.get('url', '').strip(),
        date_published=data.get('date_published') or date.today(),
        country=data.get('country', '').strip(),
        sentiment=data.get('sentiment', 'neutral'),
        ave=float(data.get('ave', 0) or 0),
    )
    return JsonResponse({'id': article.id, 'headline': article.headline[:60]})


@login_required
@require_http_methods(['PUT'])
def print_article_update(request, org_id, article_id):
    org = get_object_or_404(Organization, id=org_id)
    article = get_object_or_404(PrintArticle, id=article_id, organization=org)
    data = json.loads(request.body)
    for field in ['source', 'headline', 'summary', 'author', 'section', 'url', 'country', 'sentiment']:
        if field in data:
            setattr(article, field, data[field].strip() if isinstance(data[field], str) else data[field])
    if 'ave' in data:
        article.ave = float(data['ave'] or 0)
    if 'date_published' in data and data['date_published']:
        article.date_published = data['date_published']
    article.save()
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['DELETE'])
def print_article_delete(request, org_id, article_id):
    org = get_object_or_404(Organization, id=org_id)
    article = get_object_or_404(PrintArticle, id=article_id, organization=org)
    article.delete()
    return JsonResponse({'ok': True})


_SENTIMENT_MAP = {
    '1': 'positive', 'positive': 'positive', 'pos': 'positive',
    '0': 'neutral', 'neutral': 'neutral', 'neu': 'neutral', '': 'neutral',
    '-1': 'negative', 'negative': 'negative', 'neg': 'negative',
    'mixed': 'mixed',
}


def _parse_csv_date(value):
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    # Try ISO 8601 with optional trailing Z (e.g. "2026-05-26T00:00:00.000Z")
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).date()
    except ValueError:
        pass
    for fmt in ('%Y-%m-%d', '%d/%m/%Y', '%m/%d/%Y'):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


@login_required
@require_http_methods(['POST'])
def print_article_csv_upload(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    upload = request.FILES.get('file')
    if not upload:
        return JsonResponse({'error': 'No file uploaded'}, status=400)

    try:
        raw = upload.read().decode('utf-8-sig')
    except UnicodeDecodeError:
        return JsonResponse({'error': 'File must be UTF-8 encoded CSV'}, status=400)

    reader = csv.DictReader(io.StringIO(raw))
    created = 0
    errors = []
    to_create = []

    for idx, row in enumerate(reader, start=2):  # row 1 is header
        headline = (row.get('headline') or '').strip()
        if not headline:
            errors.append(f'Row {idx}: missing headline')
            continue

        published = _parse_csv_date(row.get('publicationDate'))
        if not published:
            errors.append(f'Row {idx}: invalid or missing publicationDate')
            continue

        sentiment_raw = (row.get('sentiment') or '').strip().lower()
        sentiment = _SENTIMENT_MAP.get(sentiment_raw, 'neutral')

        try:
            ave_value = float((row.get('ave') or '0').strip() or 0)
        except ValueError:
            ave_value = 0

        to_create.append(PrintArticle(
            organization=org,
            source=(row.get('publication') or '').strip(),
            headline=headline,
            author=(row.get('byline') or '').strip(),
            section=(row.get('section') or '').strip(),
            url=(row.get('url') or '').strip(),
            date_published=published,
            country=(row.get('country') or '').strip(),
            sentiment=sentiment,
            ave=ave_value,
        ))

    if to_create:
        PrintArticle.objects.bulk_create(to_create)
        created = len(to_create)

    return JsonResponse({'created': created, 'errors': errors})


# ── Media: Social Posts ───────────────────────────────────────────────────────

@login_required
def media_social(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    qs = org.social_posts.all()

    q = request.GET.get('q', '')
    sentiment = request.GET.get('sentiment', '')
    country = request.GET.get('country', '')
    platform = request.GET.get('platform', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if q:
        qs = qs.filter(Q(headline__icontains=q) | Q(page_name__icontains=q))
    if sentiment:
        qs = qs.filter(sentiment=sentiment)
    if country:
        qs = qs.filter(country=country)
    if platform:
        qs = qs.filter(platform=platform)
    if date_from:
        qs = qs.filter(date_published__gte=date_from)
    if date_to:
        qs = qs.filter(date_published__lte=date_to)

    countries = list(org.social_posts.exclude(country='').values_list('country', flat=True).distinct().order_by('country'))
    posts, total, current_count, has_more, next_page_url = paginate_media(request, qs)

    return render(request, 'monitor/media_social.html', {
        'org': org,
        'page': 'media',
        'posts': posts,
        'total': total,
        'current_count': current_count,
        'has_more': has_more,
        'next_page_url': next_page_url,
        'q': q,
        'sentiment_choices': SENTIMENT_CHOICES,
        'platform_choices': PLATFORM_CHOICES,
        'countries': countries,
        'selected_sentiment': sentiment,
        'selected_country': country,
        'selected_platform': platform,
        'date_from': date_from,
        'date_to': date_to,
    })


@login_required
@require_http_methods(['POST'])
def social_post_create(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    data = json.loads(request.body)
    post = SocialMediaPost.objects.create(
        organization=org,
        platform=data.get('platform', 'Facebook'),
        page_name=data.get('page_name', '').strip(),
        headline=data.get('headline', '').strip(),
        summary=data.get('summary', '').strip(),
        url=data.get('url', '').strip(),
        date_published=data.get('date_published') or date.today(),
        country=data.get('country', '').strip(),
        sentiment=data.get('sentiment', 'neutral'),
        ave=float(data.get('ave', 0) or 0),
        rank=float(data.get('rank', 0) or 0),
        reach=int(data.get('reach', 0) or 0),
        relevancy=float(data.get('relevancy', 0) or 0),
    )
    return JsonResponse({'id': post.id})


@login_required
@require_http_methods(['POST'])
def social_post_update(request, org_id, post_id):
    org = get_object_or_404(Organization, id=org_id)
    post = get_object_or_404(SocialMediaPost, id=post_id, organization=org)
    data = json.loads(request.body)
    post.platform = data.get('platform', post.platform)
    post.page_name = data.get('page_name', post.page_name).strip()
    post.headline = data.get('headline', post.headline).strip()
    post.url = data.get('url', post.url).strip()
    post.date_published = data.get('date_published') or post.date_published
    post.country = data.get('country', post.country).strip()
    post.sentiment = data.get('sentiment', post.sentiment)
    post.ave = float(data.get('ave', post.ave) or 0)
    post.reach = int(data.get('reach', post.reach) or 0)
    post.relevancy = float(data.get('relevancy', post.relevancy) or 0)
    post.save()
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['DELETE'])
def social_post_delete(request, org_id, post_id):
    org = get_object_or_404(Organization, id=org_id)
    post = get_object_or_404(SocialMediaPost, id=post_id, organization=org)
    post.delete()
    return JsonResponse({'ok': True})


# ── Media: Broadcast ──────────────────────────────────────────────────────────

@login_required
def media_broadcast(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    qs = org.broadcast_mentions.all()

    q = request.GET.get('q', '')
    sentiment = request.GET.get('sentiment', '')
    country = request.GET.get('country', '')
    btype = request.GET.get('btype', '')
    date_from = request.GET.get('date_from', '')
    date_to = request.GET.get('date_to', '')

    if q:
        qs = qs.filter(Q(headline__icontains=q) | Q(source__icontains=q))
    if sentiment:
        qs = qs.filter(sentiment=sentiment)
    if country:
        qs = qs.filter(country=country)
    if btype:
        qs = qs.filter(broadcast_type=btype)
    if date_from:
        qs = qs.filter(date_published__gte=date_from)
    if date_to:
        qs = qs.filter(date_published__lte=date_to)

    countries = list(org.broadcast_mentions.exclude(country='').values_list('country', flat=True).distinct().order_by('country'))
    mentions, total, current_count, has_more, next_page_url = paginate_media(request, qs)

    return render(request, 'monitor/media_broadcast.html', {
        'org': org,
        'page': 'media',
        'mentions': mentions,
        'total': total,
        'current_count': current_count,
        'has_more': has_more,
        'next_page_url': next_page_url,
        'q': q,
        'sentiment_choices': SENTIMENT_CHOICES,
        'countries': countries,
        'selected_sentiment': sentiment,
        'selected_country': country,
        'selected_btype': btype,
        'date_from': date_from,
        'date_to': date_to,
    })


@login_required
@require_http_methods(['POST'])
def broadcast_create(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    data = json.loads(request.body)
    mention = BroadcastMention.objects.create(
        organization=org,
        source=data.get('source', '').strip(),
        headline=data.get('headline', '').strip(),
        summary=data.get('summary', '').strip(),
        url=data.get('url', '').strip(),
        date_published=data.get('date_published') or date.today(),
        country=data.get('country', '').strip(),
        sentiment=data.get('sentiment', 'neutral'),
        ave=float(data.get('ave', 0) or 0),
        duration=data.get('duration', '').strip(),
        broadcast_type=data.get('broadcast_type', 'RADIO'),
    )
    return JsonResponse({'id': mention.id})


@login_required
@require_http_methods(['POST'])
def broadcast_update(request, org_id, mention_id):
    org = get_object_or_404(Organization, id=org_id)
    mention = get_object_or_404(BroadcastMention, id=mention_id, organization=org)
    data = json.loads(request.body)
    mention.source = data.get('source', mention.source).strip()
    mention.headline = data.get('headline', mention.headline).strip()
    mention.url = data.get('url', mention.url).strip()
    mention.broadcast_type = data.get('broadcast_type', mention.broadcast_type)
    mention.duration = data.get('duration', mention.duration).strip()
    mention.date_published = data.get('date_published') or mention.date_published
    mention.country = data.get('country', mention.country).strip()
    mention.sentiment = data.get('sentiment', mention.sentiment)
    mention.ave = float(data.get('ave', mention.ave) or 0)
    mention.save()
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['DELETE'])
def broadcast_delete(request, org_id, mention_id):
    org = get_object_or_404(Organization, id=org_id)
    mention = get_object_or_404(BroadcastMention, id=mention_id, organization=org)
    mention.delete()
    return JsonResponse({'ok': True})


# ── Competitors ───────────────────────────────────────────────────────────────

@login_required
def competitors_view(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    competitors = list(org.competitors.all())
    today = date.today()
    month_start = today.replace(day=1)

    q_search = request.GET.get('q', '')
    sentiment_filter = request.GET.get('sentiment', '')

    comp_monthly_counts = []
    comp_overall_counts = []
    online_articles = []
    broadcast_articles = []
    print_articles = []

    for comp in competitors:
        name_q = Q(headline__icontains=comp.name) | Q(summary__icontains=comp.name)

        m_count = (
            org.online_articles.filter(date_published__gte=month_start).filter(name_q).count() +
            org.broadcast_mentions.filter(date_published__gte=month_start).filter(name_q).count() +
            org.print_articles.filter(date_published__gte=month_start).filter(name_q).count()
        )
        o_count = (
            org.online_articles.filter(name_q).count() +
            org.broadcast_mentions.filter(name_q).count() +
            org.print_articles.filter(name_q).count()
        )
        comp_monthly_counts.append({'name': comp.name, 'count': m_count})
        comp_overall_counts.append({'name': comp.name, 'count': o_count})

        art_qs = org.online_articles.filter(name_q)
        bc_qs = org.broadcast_mentions.filter(name_q)
        pr_qs = org.print_articles.filter(name_q)

        if q_search:
            sq = Q(headline__icontains=q_search) | Q(source__icontains=q_search)
            art_qs = art_qs.filter(sq)
            bc_qs = bc_qs.filter(sq)
            pr_qs = pr_qs.filter(sq)
        if sentiment_filter:
            art_qs = art_qs.filter(sentiment=sentiment_filter)
            bc_qs = bc_qs.filter(sentiment=sentiment_filter)
            pr_qs = pr_qs.filter(sentiment=sentiment_filter)

        for art in art_qs.values('id', 'headline', 'source', 'sentiment', 'reach', 'ave', 'date_published', 'country', 'url'):
            art['competitor_name'] = comp.name
            online_articles.append(art)
        for art in bc_qs.values('id', 'headline', 'source', 'sentiment', 'ave', 'date_published', 'country', 'url'):
            art['reach'] = 0
            art['competitor_name'] = comp.name
            broadcast_articles.append(art)
        for art in pr_qs.values('id', 'headline', 'source', 'sentiment', 'ave', 'date_published', 'country', 'url'):
            art['reach'] = 0
            art['competitor_name'] = comp.name
            print_articles.append(art)

    online_articles.sort(key=lambda x: x['date_published'], reverse=True)
    broadcast_articles.sort(key=lambda x: x['date_published'], reverse=True)
    print_articles.sort(key=lambda x: x['date_published'], reverse=True)

    # Deduplicate by article id (first competitor match wins)
    seen = set()
    deduped_online = []
    for a in online_articles:
        if a['id'] not in seen:
            seen.add(a['id'])
            deduped_online.append(a)

    seen = set()
    deduped_bc = []
    for a in broadcast_articles:
        if a['id'] not in seen:
            seen.add(a['id'])
            deduped_bc.append(a)

    seen = set()
    deduped_print = []
    for a in print_articles:
        if a['id'] not in seen:
            seen.add(a['id'])
            deduped_print.append(a)

    # Yearly line chart
    months = list(calendar.month_abbr)[1:]
    if competitors:
        any_comp_q = Q()
        for comp in competitors:
            any_comp_q |= Q(headline__icontains=comp.name) | Q(summary__icontains=comp.name)
        online_yearly = _monthly_counts(org.online_articles.filter(any_comp_q), today.year)
        broadcast_yearly = _monthly_counts(org.broadcast_mentions.filter(any_comp_q), today.year)
        print_yearly = _monthly_counts(org.print_articles.filter(any_comp_q), today.year)
    else:
        online_yearly = broadcast_yearly = print_yearly = [0] * 12

    return render(request, 'monitor/competitors.html', {
        'org': org,
        'page': 'competitors',
        'competitors': competitors,
        'comp_monthly_json': json.dumps(comp_monthly_counts),
        'comp_overall_json': json.dumps(comp_overall_counts),
        'months_json': json.dumps(months),
        'online_yearly_json': json.dumps(online_yearly),
        'broadcast_yearly_json': json.dumps(broadcast_yearly),
        'print_yearly_json': json.dumps(print_yearly),
        'online_articles': deduped_online[:200],
        'broadcast_articles': deduped_bc[:200],
        'print_articles': deduped_print[:200],
        'online_count': len(deduped_online),
        'broadcast_count': len(deduped_bc),
        'print_count': len(deduped_print),
        'q': q_search,
        'selected_sentiment': sentiment_filter,
        'sentiment_choices': SENTIMENT_CHOICES,
    })


@login_required
@require_http_methods(['POST'])
def competitor_create(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    data = json.loads(request.body)
    comp = Competitor.objects.create(
        organization=org,
        name=data.get('name', '').strip(),
        website=data.get('website', '').strip(),
        notes=data.get('notes', '').strip(),
    )
    return JsonResponse({'id': comp.id, 'name': comp.name})


@login_required
@require_http_methods(['DELETE'])
def competitor_delete(request, org_id, comp_id):
    org = get_object_or_404(Organization, id=org_id)
    comp = get_object_or_404(Competitor, id=comp_id, organization=org)
    comp.delete()
    return JsonResponse({'ok': True})


# ── Reports ───────────────────────────────────────────────────────────────────

def _fmt_date(d):
    return d.strftime('%B') + ' ' + str(d.day) + ', ' + str(d.year)


def _type_stats(qs, src_field, has_reach=True):
    import re as _re
    from collections import defaultdict, Counter
    vol = qs.count()
    ave = float(qs.aggregate(s=Sum('ave'))['s'] or 0)
    reach = float(qs.aggregate(s=Sum('reach'))['s'] or 0) if has_reach else 0
    pos = qs.filter(sentiment='positive').count()
    neu = qs.filter(sentiment='neutral').count()
    neg = qs.filter(sentiment='negative').count()
    total_s = pos + neu + neg
    pos_pct = round(pos / total_s * 100) if total_s else 0
    neu_pct = round(neu / total_s * 100) if total_s else 0
    neg_pct = 100 - pos_pct - neu_pct if total_s else 0

    daily = defaultdict(lambda: {'pos': 0, 'neu': 0, 'neg': 0})
    for row in qs.values('date_published', 'sentiment'):
        d = str(row['date_published'])
        s = row['sentiment']
        if s == 'positive': daily[d]['pos'] += 1
        elif s == 'negative': daily[d]['neg'] += 1
        else: daily[d]['neu'] += 1
    dates = sorted(daily.keys())

    src_map = {}
    if src_field:
        for row in qs.exclude(**{src_field: ''}).values(src_field).annotate(
            total=Count('id'),
            pos=Count('id', filter=Q(sentiment='positive')),
            neu=Count('id', filter=Q(sentiment='neutral')),
            neg=Count('id', filter=Q(sentiment='negative')),
        ).order_by('-total')[:8]:
            src_map[row[src_field]] = {k: row[k] for k in ('total', 'pos', 'neu', 'neg')}

    risks = [{'headline': i.headline[:100], 'source': getattr(i, src_field, '') if src_field else '',
              'ave': float(i.ave), 'date': i.date_published.strftime('%d %b %Y')}
             for i in qs.filter(sentiment='negative').order_by('-ave')[:5]]
    opps  = [{'headline': i.headline[:100], 'source': getattr(i, src_field, '') if src_field else '',
              'ave': float(i.ave), 'date': i.date_published.strftime('%d %b %Y')}
             for i in qs.filter(sentiment='positive').order_by('-ave')[:5]]
    issues = [{'label': i.headline[:55], 'ave': float(i.ave), 'sentiment': i.sentiment}
              for i in qs.order_by('-ave')[:8]]

    STOP = {'the','a','an','and','or','but','in','on','at','to','for','of','with','by','from',
            'is','was','are','were','be','been','have','has','had','do','does','did','will',
            'would','could','should','may','might','that','this','it','its','not','no','as',
            'up','out','so','if','after','over','about','into','than','more','also','said',
            'new','says','their','they','who','what','when','where','how'}
    counter = Counter()
    for h in qs.values_list('headline', flat=True):
        counter.update(w for w in _re.findall(r'\b[a-zA-Z]{4,}\b', h.lower()) if w not in STOP)
    words = counter.most_common(30)

    return {
        'vol': vol, 'ave': ave, 'reach': reach,
        'pos': pos, 'neu': neu, 'neg': neg,
        'pos_pct': pos_pct, 'neu_pct': neu_pct, 'neg_pct': neg_pct,
        'trend': json.dumps({'labels': dates,
                             'pos': [daily[d]['pos'] for d in dates],
                             'neu': [daily[d]['neu'] for d in dates],
                             'neg': [daily[d]['neg'] for d in dates]}),
        'sources_json': json.dumps([{'name': n, **v} for n, v in src_map.items()]),
        'key_events_json': json.dumps([]),
        'risks': risks,
        'opps': opps,
        'issues_json': json.dumps(issues),
        'words_json': json.dumps(words),
        'max_freq': words[0][1] if words else 1,
    }


@login_required
def report_full(request, org_id):
    import re as _re
    from collections import defaultdict, Counter
    org = get_object_or_404(Organization, id=org_id)
    today = date.today()

    date_from_str = request.GET.get('date_from', '')
    date_to_str = request.GET.get('date_to', '')
    try:
        date_from = date.fromisoformat(date_from_str) if date_from_str else today.replace(day=1)
    except ValueError:
        date_from = today.replace(day=1)
    try:
        date_to = date.fromisoformat(date_to_str) if date_to_str else today
    except ValueError:
        date_to = today

    month_label = f"{_fmt_date(date_from)} – {_fmt_date(date_to)}"
    kw = dict(date_published__gte=date_from, date_published__lte=date_to)

    oa = org.online_articles.filter(**kw)
    pa = org.print_articles.filter(**kw)
    sp = org.social_posts.filter(**kw)
    bm = org.broadcast_mentions.filter(**kw)

    oa_c, pa_c, sp_c, bm_c = oa.count(), pa.count(), sp.count(), bm.count()
    total_vol = oa_c + pa_c + sp_c + bm_c

    def _sum(qs, field):
        return float(qs.aggregate(s=Sum(field))['s'] or 0)

    total_ave = _sum(oa, 'ave') + _sum(pa, 'ave') + _sum(sp, 'ave') + _sum(bm, 'ave')
    total_reach = _sum(oa, 'reach') + _sum(sp, 'reach')

    def _sc(qs):
        return (
            qs.filter(sentiment='positive').count(),
            qs.filter(sentiment='neutral').count(),
            qs.filter(sentiment='negative').count(),
        )

    counts = [_sc(q) for q in [oa, pa, sp, bm]]
    pos_total = sum(c[0] for c in counts)
    neu_total = sum(c[1] for c in counts)
    neg_total = sum(c[2] for c in counts)
    total_sent = pos_total + neu_total + neg_total
    pos_pct = round(pos_total / total_sent * 100) if total_sent else 0
    neu_pct = round(neu_total / total_sent * 100) if total_sent else 0
    neg_pct = 100 - pos_pct - neu_pct

    # Sentiment trend by date
    daily = defaultdict(lambda: {'pos': 0, 'neu': 0, 'neg': 0})
    for q in [oa, pa, sp, bm]:
        for row in q.values('date_published', 'sentiment'):
            d = str(row['date_published'])
            s = row['sentiment']
            if s == 'positive':
                daily[d]['pos'] += 1
            elif s == 'negative':
                daily[d]['neg'] += 1
            else:
                daily[d]['neu'] += 1
    sorted_dates = sorted(daily.keys())
    trend_data = json.dumps({
        'labels': sorted_dates,
        'pos': [daily[d]['pos'] for d in sorted_dates],
        'neu': [daily[d]['neu'] for d in sorted_dates],
        'neg': [daily[d]['neg'] for d in sorted_dates],
    })

    # Top sources
    src_map = {}
    for qs, key_field in [(sp, 'platform'), (oa, 'source'), (pa, 'source'), (bm, 'source')]:
        for row in qs.exclude(**{key_field: ''}).values(key_field).annotate(
            total=Count('id'),
            pos=Count('id', filter=Q(sentiment='positive')),
            neu=Count('id', filter=Q(sentiment='neutral')),
            neg=Count('id', filter=Q(sentiment='negative')),
        ).order_by('-total'):
            name = row[key_field]
            if name in src_map:
                for k in ('total', 'pos', 'neu', 'neg'):
                    src_map[name][k] += row[k]
            else:
                src_map[name] = {k: row[k] for k in ('total', 'pos', 'neu', 'neg')}
    top_sources = sorted(src_map.items(), key=lambda x: x[1]['total'], reverse=True)[:10]
    top_sources_json = json.dumps([{'name': n, **v} for n, v in top_sources])

    # Risks (top negative) and opportunities (top positive)
    risks, opportunities = [], []
    for q, media_type, src_field in [
        (sp, 'Social Media', 'platform'),
        (oa, 'Online', 'source'),
        (pa, 'Print', 'source'),
        (bm, 'Broadcast', 'source'),
    ]:
        for item in q.filter(sentiment='negative').order_by('-ave')[:4]:
            risks.append({'headline': item.headline[:110], 'source': getattr(item, src_field, '') or media_type,
                          'ave': float(item.ave), 'media_type': media_type, 'date': item.date_published})
        for item in q.filter(sentiment='positive').order_by('-ave')[:4]:
            opportunities.append({'headline': item.headline[:110], 'source': getattr(item, src_field, '') or media_type,
                                   'ave': float(item.ave), 'media_type': media_type, 'date': item.date_published})
    risks = sorted(risks, key=lambda x: x['ave'], reverse=True)[:6]
    opportunities = sorted(opportunities, key=lambda x: x['ave'], reverse=True)[:6]

    # Top journalists
    journalists = list(pa.exclude(author='').values('author').annotate(
        total=Count('id'),
        pos=Count('id', filter=Q(sentiment='positive')),
        neu=Count('id', filter=Q(sentiment='neutral')),
        neg=Count('id', filter=Q(sentiment='negative')),
        ave_total=Sum('ave'),
    ).order_by('-total')[:10])

    # Word cloud
    STOP = {'the','a','an','and','or','but','in','on','at','to','for','of','with','by','from',
            'is','was','are','were','be','been','have','has','had','do','does','did','will',
            'would','could','should','may','might','that','this','it','its','not','no','as',
            'up','out','so','if','after','over','about','into','than','more','also','said',
            'new','says','their','they','who','what','when','where','how','been','just'}
    counter = Counter()
    for q in [oa, pa, sp, bm]:
        for h in q.values_list('headline', flat=True):
            words = _re.findall(r'\b[a-zA-Z]{4,}\b', h.lower())
            counter.update(w for w in words if w not in STOP)
    top_words = counter.most_common(40)
    max_freq = top_words[0][1] if top_words else 1

    # Issue impact — top stories by AVE with sentiment
    issue_items = []
    for q, media_type, src_field in [(sp, 'Social', 'platform'), (oa, 'Online', 'source'),
                                      (pa, 'Print', 'source'), (bm, 'Broadcast', 'source')]:
        for item in q.order_by('-ave')[:5]:
            issue_items.append({
                'label': item.headline[:60],
                'ave': float(item.ave),
                'sentiment': item.sentiment,
                'media_type': media_type,
            })
    issue_items = sorted(issue_items, key=lambda x: x['ave'], reverse=True)[:10]

    # Executive narrative
    if total_vol == 0:
        exec_narrative = (f"No media mentions were recorded for {org.name} during "
                          f"{_fmt_date(date_from)} to {_fmt_date(date_to)}.")
    else:
        tone = ('predominantly positive' if pos_pct >= 50 else
                'predominantly negative' if neg_pct >= 50 else 'mixed in sentiment')
        exec_narrative = (
            f"Media coverage of {org.name} during the reporting period was {tone}. "
            f"The organisation recorded a total of {total_vol:,} media mentions across all channels, "
            f"generating an estimated BWP {total_ave:,.0f} in Advertising Value Equivalency (AVE) "
            f"and a potential audience reach of {int(total_reach):,}. "
            f"Positive sentiment accounted for {pos_pct}% of total coverage, "
            f"with {neu_pct}% neutral and {neg_pct}% negative mentions."
        )

    # ── Parse selected modules from query param ──────────────────────────────
    modules_param = request.GET.get('modules', '')
    report_mode   = request.GET.get('type', 'full')
    ALL_MOD_IDS = ['media_summary', 'sentiment_trend', 'reputational_risks',
                   'reputational_opportunities', 'issue_impact', 'top_sources',
                   'word_cloud', 'kpi_performance']

    VALID_TYPES = {'social', 'online', 'broadcast', 'print'}
    if report_mode == 'custom' and modules_param:
        selected_set = set()
        for item in modules_param.split(','):
            item = item.strip()
            if ':' in item:
                selected_set.add(item)
            elif item in VALID_TYPES:
                for m in ALL_MOD_IDS:
                    selected_set.add(f'{item}:{m}')
    else:
        selected_set = {f'{t}:{m}' for t in ('social', 'online', 'broadcast', 'print')
                        for m in ALL_MOD_IDS}

    def _sel(type_key, mod_id):
        return f'{type_key}:{mod_id}' in selected_set

    # ── Per-media-type stats ──────────────────────────────────────────────────
    type_map = {
        'social':    {'qs': sp, 'src': 'platform', 'reach': True,  'label': 'Social Media',    'color': '#1d4ed8'},
        'online':    {'qs': oa, 'src': 'source',   'reach': True,  'label': 'Online Media',    'color': '#0f766e'},
        'broadcast': {'qs': bm, 'src': 'source',   'reach': False, 'label': 'Broadcast Media', 'color': '#9333ea'},
        'print':     {'qs': pa, 'src': 'source',   'reach': False, 'label': 'Print Media',     'color': '#c2410c'},
    }
    sections = {}
    for key, cfg in type_map.items():
        stats = _type_stats(cfg['qs'], cfg['src'], cfg['reach'])
        stats.update({'label': cfg['label'], 'color': cfg['color'], 'key': key})
        # Which modules are selected for this type
        stats['sel_mods'] = [m for m in ALL_MOD_IDS if _sel(key, m)]
        sections[key] = stats

    # Journalists (print only)
    journalists = list(pa.exclude(author='').values('author').annotate(
        total=Count('id'),
        pos=Count('id', filter=Q(sentiment='positive')),
        neu=Count('id', filter=Q(sentiment='neutral')),
        neg=Count('id', filter=Q(sentiment='negative')),
        ave_total=Sum('ave'),
    ).order_by('-total')[:10])

    # Module bar text (all selected modules, flagging those with no data)
    mod_bar_items = []
    for key in ('social', 'online', 'broadcast', 'print'):
        s = sections[key]
        for m in s['sel_mods']:
            label = next((x['label'] for x in _MODULE_LIST if x['id'] == m), m)
            has_data = s['vol'] > 0
            mod_bar_items.append({'name': f'{s["label"]}: {label}', 'has_data': has_data})

    return render(request, 'monitor/report_full.html', {
        'org': org,
        'page': 'reports',
        'date_from': date_from,
        'date_to': date_to,
        'month_label': month_label,
        'total_vol': total_vol,
        'total_ave': total_ave,
        'total_reach': total_reach,
        'oa_c': oa_c, 'pa_c': pa_c, 'sp_c': sp_c, 'bm_c': bm_c,
        'pos_total': pos_total, 'neu_total': neu_total, 'neg_total': neg_total,
        'pos_pct': pos_pct, 'neu_pct': neu_pct, 'neg_pct': neg_pct,
        'exec_narrative': exec_narrative,
        'sections': sections,
        'sections_order': ['social', 'online', 'broadcast', 'print'],
        'journalists': journalists,
        'mod_bar_items': mod_bar_items,
        'all_mod_ids': ALL_MOD_IDS,
    })


_MEDIA_TYPES_DEF = [
    {'key': 'social',    'label': 'Social Media'},
    {'key': 'online',    'label': 'Online Media'},
    {'key': 'broadcast', 'label': 'Broadcast Media'},
    {'key': 'print',     'label': 'Print Media'},
]

_MODULE_LIST = [
    {'id': 'media_summary',              'label': 'Media Summary'},
    {'id': 'sentiment_trend',            'label': 'Sentiment Trend'},
    {'id': 'reputational_risks',         'label': 'Reputational Risks'},
    {'id': 'reputational_opportunities', 'label': 'Reputational Opportunities'},
    {'id': 'issue_impact',               'label': 'Issue Impact'},
    {'id': 'top_sources',                'label': 'Top Media Sources'},
    {'id': 'word_cloud',                 'label': 'Word Cloud'},
    {'id': 'kpi_performance',            'label': 'KPI Performance'},
]


@login_required
def reports_view(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    generated_reports = org.generated_reports.select_related('created_by').all()
    country_set = set()
    for qs in [org.online_articles, org.print_articles, org.social_posts, org.broadcast_mentions]:
        for c in qs.exclude(country='').values_list('country', flat=True).distinct():
            country_set.add(c)
    return render(request, 'monitor/reports.html', {
        'org': org,
        'page': 'reports',
        'generated_reports': generated_reports,
        'media_types_def': _MEDIA_TYPES_DEF,
        'module_list': _MODULE_LIST,
        'country_choices': sorted(country_set),
    })


@login_required
def report_save(request, org_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    org = get_object_or_404(Organization, id=org_id)
    import json as _json
    from datetime import date as _date
    data = _json.loads(request.body)
    report_type = data.get('report_type', 'Custom Report')
    modules = data.get('modules', [])
    date_from_str = data.get('date_from', '')
    date_to_str = data.get('date_to', '')
    date_from = _date.fromisoformat(date_from_str) if date_from_str else None
    date_to = _date.fromisoformat(date_to_str) if date_to_str else None

    submitted_countries = data.get('countries', [])
    if submitted_countries:
        scope = sorted(submitted_countries)
    else:
        countries = set()
        qs_kwargs = {}
        if date_from:
            qs_kwargs['date_published__gte'] = date_from
        if date_to:
            qs_kwargs['date_published__lte'] = date_to
        for qs in [org.online_articles, org.print_articles, org.social_posts, org.broadcast_mentions]:
            for c in qs.filter(**qs_kwargs).exclude(country='').values_list('country', flat=True).distinct():
                countries.add(c)
        scope = sorted(countries)

    report = GeneratedReport.objects.create(
        organization=org,
        title=f'{org.name} Report',
        report_type=report_type,
        modules=modules,
        scope=scope,
        created_by=request.user,
        date_from=date_from,
        date_to=date_to,
    )
    return JsonResponse({'id': str(report.id)})


@login_required
def report_delete(request, org_id, report_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    org = get_object_or_404(Organization, id=org_id)
    report = get_object_or_404(GeneratedReport, id=report_id, organization=org)
    report.delete()
    return JsonResponse({'ok': True})


@login_required
def report_sentiment(request, org_id):
    from collections import defaultdict
    org = get_object_or_404(Organization, id=org_id)
    today = date.today()

    date_from_str = request.GET.get('date_from', '')
    date_to_str   = request.GET.get('date_to', '')
    try:
        date_from = date.fromisoformat(date_from_str) if date_from_str else today - timedelta(days=30)
    except ValueError:
        date_from = today - timedelta(days=30)
    try:
        date_to = date.fromisoformat(date_to_str) if date_to_str else today
    except ValueError:
        date_to = today

    period_days = (date_to - date_from).days or 1
    kw = dict(date_published__gte=date_from, date_published__lte=date_to)

    oa = org.online_articles.filter(**kw)
    pa = org.print_articles.filter(**kw)
    sp = org.social_posts.filter(**kw)
    bm = org.broadcast_mentions.filter(**kw)
    all_qs = [oa, pa, sp, bm]

    pos = sum(q.filter(sentiment='positive').count() for q in all_qs)
    neu = sum(q.filter(sentiment='neutral').count() for q in all_qs)
    neg = sum(q.filter(sentiment='negative').count() for q in all_qs)
    total = pos + neu + neg

    pos_pct = round(pos / total * 100) if total else 0
    neu_pct = round(neu / total * 100) if total else 0
    neg_pct = 100 - pos_pct - neu_pct if total else 0

    score = round((pos * 5 + neu * 3 + neg * 1) / total, 2) if total else 0
    if score >= 3.5:
        sentiment_label, sentiment_color = 'Positive', '#22c55e'
    elif score >= 2.5:
        sentiment_label, sentiment_color = 'Neutral', '#f59e0b'
    else:
        sentiment_label, sentiment_color = 'Negative', '#ef4444'

    daily = defaultdict(lambda: {'pos': 0, 'neu': 0, 'neg': 0})
    for q in all_qs:
        for row in q.values('date_published', 'sentiment'):
            d = str(row['date_published'])
            s = row['sentiment']
            if s == 'positive':   daily[d]['pos'] += 1
            elif s == 'negative': daily[d]['neg'] += 1
            else:                 daily[d]['neu'] += 1

    sorted_dates = sorted(daily.keys())
    timeline_score = []
    for d in sorted_dates:
        dp = daily[d]
        t = dp['pos'] + dp['neu'] + dp['neg']
        timeline_score.append(round((dp['pos'] * 5 + dp['neu'] * 3 + dp['neg'] * 1) / t, 2) if t else 0)

    prev_kw = dict(date_published__gte=date_from - timedelta(days=period_days), date_published__lte=date_from)
    prev_total = (org.online_articles.filter(**prev_kw).count() +
                  org.print_articles.filter(**prev_kw).count() +
                  org.social_posts.filter(**prev_kw).count() +
                  org.broadcast_mentions.filter(**prev_kw).count())
    mentions_change = round((total - prev_total) / prev_total * 100, 1) if prev_total else 0

    top_positive, top_negative = [], []
    for q, src in [(oa, 'source'), (pa, 'source'), (sp, 'platform'), (bm, 'source')]:
        for item in q.filter(sentiment='positive').order_by('-ave')[:3]:
            top_positive.append({'headline': item.headline[:100], 'source': getattr(item, src, '') or '',
                                  'ave': float(item.ave), 'date': item.date_published.strftime('%d %b %Y')})
        for item in q.filter(sentiment='negative').order_by('-ave')[:3]:
            top_negative.append({'headline': item.headline[:100], 'source': getattr(item, src, '') or '',
                                  'ave': float(item.ave), 'date': item.date_published.strftime('%d %b %Y')})
    top_positive = sorted(top_positive, key=lambda x: x['ave'], reverse=True)[:5]
    top_negative = sorted(top_negative, key=lambda x: x['ave'], reverse=True)[:5]

    ctx = {
        'org': org, 'page': 'reports',
        'date_from': date_from,
        'date_to': date_to,
        'date_from_str': date_from.isoformat(),
        'date_to_str': date_to.isoformat(),
        'period_label': f"{date_from.strftime('%d %b %Y')} – {date_to.strftime('%d %b %Y')}",
        'score': score,
        'sentiment_label': sentiment_label,
        'sentiment_color': sentiment_color,
        'pos': pos, 'neu': neu, 'neg': neg,
        'pos_pct': pos_pct, 'neu_pct': neu_pct, 'neg_pct': neg_pct,
        'total': total,
        'mentions_change': mentions_change,
        'timeline_json': json.dumps({
            'labels': sorted_dates,
            'pos':   [daily[d]['pos'] for d in sorted_dates],
            'neu':   [daily[d]['neu'] for d in sorted_dates],
            'neg':   [daily[d]['neg'] for d in sorted_dates],
            'score': timeline_score,
        }),
        'top_positive': top_positive,
        'top_negative': top_negative,
    }
    if request.GET.get('format') == 'pdf':
        return render(request, 'monitor/report_sentiment_pdf.html', ctx)
    return render(request, 'monitor/report_sentiment.html', ctx)


@login_required
def report_source(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    today = date.today()

    date_from_str = request.GET.get('date_from', '')
    date_to_str   = request.GET.get('date_to', '')
    try:
        date_from = date.fromisoformat(date_from_str) if date_from_str else today - timedelta(days=30)
    except ValueError:
        date_from = today - timedelta(days=30)
    try:
        date_to = date.fromisoformat(date_to_str) if date_to_str else today
    except ValueError:
        date_to = today

    kw = dict(date_published__gte=date_from, date_published__lte=date_to)

    PALETTE = ['#0ea5e9','#f97316','#a855f7','#ef4444','#22c55e',
               '#eab308','#06b6d4','#ec4899','#84cc16','#f43f5e',
               '#8b5cf6','#14b8a6']

    def _section(qs, field, label, accent):
        rows = list(
            qs.exclude(**{field: ''}).values(field)
              .annotate(count=Count('id')).order_by('-count')
        )
        grand = sum(r['count'] for r in rows) or 1
        sources = [
            {'name': r[field],
             'count': r['count'],
             'pct': round(r['count'] / grand * 100, 1),
             'color': PALETTE[i % len(PALETTE)]}
            for i, r in enumerate(rows[:14])
        ]
        return {
            'label': label,
            'accent': accent,
            'total': grand,
            'sources': sources,
            'chart_json': json.dumps({
                'labels': [s['name'] for s in sources],
                'counts': [s['count'] for s in sources],
                'colors': [s['color'] for s in sources],
            }),
        }

    sections = [
        _section(org.social_posts.filter(**kw),       'platform', 'Social Media',    '#1d4ed8'),
        _section(org.online_articles.filter(**kw),    'source',   'Online Media',     '#0f766e'),
        _section(org.broadcast_mentions.filter(**kw), 'source',   'Broadcast Media',  '#9333ea'),
        _section(org.print_articles.filter(**kw),     'source',   'Print Media',      '#c2410c'),
    ]

    ctx = {
        'org': org, 'page': 'reports',
        'date_from': date_from,
        'date_to': date_to,
        'date_from_str': date_from.isoformat(),
        'date_to_str': date_to.isoformat(),
        'period_label': f"{date_from.strftime('%d %b %Y')} – {date_to.strftime('%d %b %Y')}",
        'sections': sections,
        'sections_json': json.dumps([
            {'label': s['label'], 'chart': s['chart_json']} for s in sections
        ]),
    }
    if request.GET.get('format') == 'pdf':
        return render(request, 'monitor/report_source_pdf.html', ctx)
    return render(request, 'monitor/report_source.html', ctx)


# ── Alerts ────────────────────────────────────────────────────────────────────

@login_required
def alerts_view(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    alerts = org.alerts.all()
    return render(request, 'monitor/alerts.html', {
        'org': org,
        'page': 'alerts',
        'alerts': alerts,
    })


@login_required
@require_http_methods(['POST'])
def alert_create(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    data = json.loads(request.body)
    alert = Alert.objects.create(
        organization=org,
        name=data.get('name', '').strip(),
        keywords=data.get('keywords', '').strip(),
        email=data.get('email', '').strip(),
        frequency=data.get('frequency', 'daily'),
        email_subject=data.get('email_subject', '').strip(),
        start_date=data.get('start_date') or None,
    )
    return JsonResponse({'id': alert.id, 'name': alert.name})


@login_required
@require_http_methods(['DELETE'])
def alert_delete(request, org_id, alert_id):
    org = get_object_or_404(Organization, id=org_id)
    alert = get_object_or_404(Alert, id=alert_id, organization=org)
    alert.delete()
    return JsonResponse({'ok': True})


# ── Users ─────────────────────────────────────────────────────────────────────

@login_required
def users_view(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    users = User.objects.filter(organization=org).order_by('first_name', 'last_name')
    all_users = User.objects.all().order_by('first_name', 'last_name')
    return render(request, 'monitor/users.html', {
        'org': org,
        'page': 'users',
        'users': all_users,
        'role_choices': ROLE_CHOICES,
    })


@login_required
@require_http_methods(['POST'])
def user_create(request, org_id):
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_encode
    from django.utils.encoding import force_bytes
    from django.core.mail import send_mail
    from django.template.loader import render_to_string

    org = get_object_or_404(Organization, id=org_id)
    data = json.loads(request.body)
    email = data.get('email', '').strip()
    first_name = data.get('first_name', '').strip()
    last_name = data.get('last_name', '').strip()
    username = email.lower() if email else first_name.lower()
    if User.objects.filter(username=username).exists():
        return JsonResponse({'error': 'A user with this email already exists.'}, status=400)
    role = data.get('role', 'viewer')
    user = User.objects.create_user(
        username=username,
        email=email,
        first_name=first_name,
        last_name=last_name,
        organization=None if role == 'platform_admin' else org,
        role=role,
    )
    user.set_unusable_password()
    user.save()

    if email:
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        token = default_token_generator.make_token(user)
        protocol = 'https' if request.is_secure() else 'http'
        domain = request.get_host()
        body = render_to_string('monitor/welcome_email.txt', {
            'full_name': user.get_full_name() or username,
            'uid': uid,
            'token': token,
            'protocol': protocol,
            'domain': domain,
        })
        send_mail(
            subject='Welcome to Social Light — Set Your Password',
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[email],
            fail_silently=True,
        )

    return JsonResponse({'id': user.id, 'name': user.get_full_name()})


@login_required
@require_http_methods(['PUT'])
def user_update(request, org_id, user_id):
    org = get_object_or_404(Organization, id=org_id)
    user = get_object_or_404(User, id=user_id)
    data = json.loads(request.body)
    for field in ['first_name', 'last_name', 'email', 'role']:
        if field in data:
            setattr(user, field, data[field].strip() if data[field] else '')
    if 'organization_id' in data:
        try:
            user.organization = Organization.objects.get(id=data['organization_id'])
        except Organization.DoesNotExist:
            pass
    user.save()
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['DELETE'])
def user_delete(request, org_id, user_id):
    user = get_object_or_404(User, id=user_id)
    if user != request.user:
        user.delete()
    return JsonResponse({'ok': True})


# ── Keywords ──────────────────────────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def keyword_create(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    data = json.loads(request.body)
    keyword = data.get('keyword', '').strip()
    category = data.get('category', 'brand').strip() or 'brand'
    if not keyword:
        return JsonResponse({'error': 'Keyword required'}, status=400)
    kw, created = Keyword.objects.get_or_create(
        organization=org,
        keyword=keyword,
        category=category,
    )
    return JsonResponse({'id': kw.id, 'keyword': kw.keyword, 'category': kw.category, 'created': created})


@login_required
@require_http_methods(['DELETE'])
def keyword_delete(request, org_id, kw_id):
    org = get_object_or_404(Organization, id=org_id)
    kw = get_object_or_404(Keyword, id=kw_id, organization=org)
    kw.delete()
    return JsonResponse({'ok': True})


# ── FAQs ──────────────────────────────────────────────────────────────────────

@login_required
def faqs_view(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    faqs_data = [
        {'q': 'What is media monitoring?', 'a': 'Media monitoring is the process of reading, watching, and listening to editorial content in newspapers, magazines, online news, blogs, broadcast media, and social media platforms to identify and track information that is relevant to your organisation.'},
        {'q': 'How is AVE calculated?', 'a': 'Advertising Value Equivalency (AVE) estimates the cost of the media coverage if you had paid for advertising in the same space or time. It is calculated based on the publication\'s advertising rate card and the size/duration of the coverage.'},
        {'q': 'What does sentiment mean?', 'a': 'Sentiment analysis categorises media mentions as Positive (favourable coverage), Neutral (factual/balanced coverage), or Negative (unfavourable coverage) based on the tone and content of the article.'},
        {'q': 'How do I add keywords for tracking?', 'a': 'Navigate to your organisation\'s dashboard and use the keywords section to add terms you want to track. The system will then flag articles containing those keywords.'},
        {'q': 'Can I export data to a report?', 'a': 'Yes. Navigate to the Reports section and click "Create Report". You can select date ranges, media types, and report formats to generate a downloadable summary.'},
        {'q': 'What media types are tracked?', 'a': 'Social Light tracks four media types: Online Articles (digital publications and news websites), Print Media (newspapers and magazines), Social Media Posts (Facebook, Twitter, Instagram, LinkedIn), and Broadcast (TV and radio mentions).'},
        {'q': 'How do I switch between organisations?', 'a': 'Click "Switch Organisations" in the top header bar. This will take you to the organisations list where you can select a different organisation to view.'},
        {'q': 'What is reach?', 'a': 'Reach refers to the estimated number of people who could have seen or read a particular piece of coverage, based on the publication\'s circulation or platform\'s audience size.'},
    ]
    return render(request, 'monitor/faqs.html', {
        'org': org,
        'page': 'faqs',
        'faqs_data': faqs_data,
    })


# ── Reports ───────────────────────────────────────────────────────────────────

def _ordinal(n):
    """Return 1st, 2nd, 3rd, 4th, … for integer n."""
    if 11 <= (n % 100) <= 13:
        return f'{n}th'
    return f'{n}{["th","st","nd","rd","th","th","th","th","th","th"][n % 10]}'


_HOT_STOP = {
    'the','a','an','and','or','but','in','on','at','to','for','of','with',
    'by','from','as','is','was','are','were','be','been','being','have',
    'has','had','do','does','did','will','would','could','should','may',
    'might','shall','can','its','it','this','that','these','those',
    'we','you','he','she','they','our','your','his','her','their','my',
    'not','no','nor','so','yet','both','either','each','few','more',
    'most','other','some','such','after','before','up','out','about',
    'into','through','over','under','between','during','against','among',
    'also','just','then','than','when','where','how','what','which','who',
    'new','says','said','say','all','per','also','one','two','three',
    'amid','after','than','says','amid','were','been','over','will',
}


def _hot_topics(texts, top_n=20):
    counts = Counter()
    for text in texts:
        if not text:
            continue
        for word in re.findall(r'[a-zA-Z]{3,}', text.lower()):
            if word not in _HOT_STOP:
                counts[word] += 1
    total = sum(counts.values()) or 1
    return [
        {'word': w.title(), 'count': c, 'pct': round(c / total * 100, 1)}
        for w, c in counts.most_common(top_n)
    ]


@login_required
def report_competitor(request, org_id):
    org = get_object_or_404(Organization, id=org_id)

    # ── Date range ────────────────────────────────────────────────────────────
    today = date.today()
    default_from = today.replace(day=1)
    raw_from = request.GET.get('date_from', '')
    raw_to = request.GET.get('date_to', '')
    try:
        df = date.fromisoformat(raw_from)
    except ValueError:
        df = default_from
    try:
        dt = date.fromisoformat(raw_to)
    except ValueError:
        dt = today

    month_label = df.strftime('%B %Y')

    # ── Gather org data ───────────────────────────────────────────────────────
    bc_qs = BroadcastMention.objects.filter(organization=org, date_published__range=(df, dt))
    art_qs = OnlineArticle.objects.filter(organization=org, date_published__range=(df, dt))
    print_qs = PrintArticle.objects.filter(organization=org, date_published__range=(df, dt))
    soc_qs = SocialMediaPost.objects.filter(organization=org, date_published__range=(df, dt))

    org_bc_count = bc_qs.count()
    org_art_count = art_qs.count()
    org_print_count = print_qs.count()
    org_soc_count = soc_qs.count()
    org_total = org_bc_count + org_art_count + org_print_count + org_soc_count

    org_art_ave = art_qs.aggregate(s=Sum('ave'))['s'] or 0
    org_art_reach = art_qs.aggregate(s=Sum('reach'))['s'] or 0

    # ── Competitor data ───────────────────────────────────────────────────────
    comp_names = list(org.competitors.values_list('name', flat=True))
    comp_orgs = {o.name: o for o in Organization.objects.filter(name__in=comp_names)}

    # Per-competitor counts
    comp_data = {}
    for cname in comp_names:
        co = comp_orgs.get(cname)
        if co:
            c_bc = BroadcastMention.objects.filter(organization=co, date_published__range=(df, dt)).count()
            c_art = OnlineArticle.objects.filter(organization=co, date_published__range=(df, dt)).count()
            c_print = PrintArticle.objects.filter(organization=co, date_published__range=(df, dt)).count()
            c_soc = SocialMediaPost.objects.filter(organization=co, date_published__range=(df, dt)).count()
            c_ave = OnlineArticle.objects.filter(organization=co, date_published__range=(df, dt)).aggregate(s=Sum('ave'))['s'] or 0
        else:
            c_bc = c_art = c_print = c_soc = c_ave = 0
        comp_data[cname] = {'bc': c_bc, 'art': c_art, 'print': c_print, 'soc': c_soc, 'ave': c_ave}

    # ── Build row lists (sorted desc by count) ────────────────────────────────
    def _make_rows(org_count, comp_key):
        rows = [{'name': org.name, 'count': org_count, 'is_org': True}]
        for cname, cd in comp_data.items():
            rows.append({'name': cname, 'count': cd[comp_key], 'is_org': False})
        rows.sort(key=lambda r: r['count'], reverse=True)
        return rows

    broadcast_rows = _make_rows(org_bc_count, 'bc')
    article_rows = _make_rows(org_art_count, 'art')
    print_rows = _make_rows(org_print_count, 'print')
    social_rows = _make_rows(org_soc_count, 'soc')

    # ── SOV table ─────────────────────────────────────────────────────────────
    all_rows = [{'name': org.name, 'count': org_total, 'is_org': True}]
    for cname, cd in comp_data.items():
        all_rows.append({
            'name': cname,
            'count': cd['bc'] + cd['art'] + cd['print'] + cd['soc'],
            'is_org': False,
        })
    all_rows.sort(key=lambda r: r['count'], reverse=True)
    grand_total = sum(r['count'] for r in all_rows) or 1

    STATUS_MAP = {1: 'Market Leader', 2: None, 3: 'Competitive', 4: 'Growing'}
    sov_table = []
    for i, row in enumerate(all_rows, start=1):
        if i == 1:
            status = 'Market Leader'
        elif i == 2:
            status = 'Strong Position' if row['is_org'] else 'Strong Contender'
        elif i == 3:
            status = 'Competitive'
        elif i == 4:
            status = 'Growing'
        else:
            status = 'Active'
        sov_table.append({
            'rank': _ordinal(i),
            'name': row['name'],
            'count': row['count'],
            'share': round(row['count'] / grand_total * 100, 1),
            'status': status,
            'is_org': row['is_org'],
        })

    # ── Publisher volume (top 8 print sources) ────────────────────────────────
    pub_qs = (
        PrintArticle.objects
        .filter(organization=org, date_published__range=(df, dt))
        .values('source')
        .annotate(count=Count('id'))
        .order_by('-count')[:8]
    )
    pub_data = [{'source': p['source'], 'count': p['count']} for p in pub_qs]

    # ── Print detail (last 10) ─────────────────────────────────────────────────
    print_detail = list(
        PrintArticle.objects
        .filter(organization=org, date_published__range=(df, dt))
        .order_by('-date_published')[:10]
        .values('headline', 'source', 'date_published', 'sentiment', 'ave')
    )

    # ── Print sentiment ───────────────────────────────────────────────────────
    print_pos = print_qs.filter(sentiment='positive').count()
    print_neu = print_qs.filter(sentiment='neutral').count()
    print_neg = print_qs.filter(sentiment='negative').count()

    # ── Broadcast sentiment ───────────────────────────────────────────────────
    pos_bc = bc_qs.filter(sentiment='positive').count()
    neu_bc = bc_qs.filter(sentiment='neutral').count()
    neg_bc = bc_qs.filter(sentiment='negative').count()
    bc_total_sent = pos_bc + neu_bc + neg_bc or 1
    pos_pct = round(pos_bc / bc_total_sent * 100)
    neu_pct = round(neu_bc / bc_total_sent * 100)
    neg_pct = round(neg_bc / bc_total_sent * 100)

    # ── Competitor averages ───────────────────────────────────────────────────
    if comp_data:
        comp_avg_art = round(sum(cd['art'] for cd in comp_data.values()) / len(comp_data), 1)
        comp_avg_soc = round(sum(cd['soc'] for cd in comp_data.values()) / len(comp_data), 1)
        comp_avg_ave = round(sum(cd['ave'] for cd in comp_data.values()) / len(comp_data), 0)
    else:
        comp_avg_art = comp_avg_soc = comp_avg_ave = 0

    # ── Hot topics from competitor content ────────────────────────────────────
    comp_texts = []
    for cname in comp_names:
        co = comp_orgs.get(cname)
        if co:
            comp_texts += list(OnlineArticle.objects.filter(organization=co, date_published__range=(df, dt)).values_list('headline', flat=True))
            comp_texts += list(PrintArticle.objects.filter(organization=co, date_published__range=(df, dt)).values_list('headline', flat=True))
            comp_texts += list(BroadcastMention.objects.filter(organization=co, date_published__range=(df, dt)).values_list('headline', flat=True))
            comp_texts += list(SocialMediaPost.objects.filter(organization=co, date_published__range=(df, dt)).values_list('headline', flat=True))
    hot_topics = _hot_topics(comp_texts, top_n=20)
    hot_topics_json = json.dumps(hot_topics)

    # ── JSON for charts ───────────────────────────────────────────────────────
    bc_json = json.dumps([{'label': r['name'], 'value': r['count'], 'is_org': r['is_org']} for r in broadcast_rows])
    art_json = json.dumps([{'label': r['name'], 'value': r['count'], 'is_org': r['is_org']} for r in article_rows])
    soc_json = json.dumps([{'label': r['name'], 'value': r['count'], 'is_org': r['is_org']} for r in social_rows])
    print_json = json.dumps([{'label': r['name'], 'value': r['count'], 'is_org': r['is_org']} for r in print_rows])
    pub_json = json.dumps([{'label': p['source'], 'value': p['count']} for p in pub_data])

    context = {
        'org': org,
        'page': 'reports',
        'date_from': df,
        'date_to': dt,
        'month_label': month_label,
        # Counts
        'org_bc_count': org_bc_count,
        'org_art_count': org_art_count,
        'org_print_count': org_print_count,
        'org_soc_count': org_soc_count,
        'org_total': org_total,
        'org_art_ave': org_art_ave,
        'org_art_reach': org_art_reach,
        # Row data
        'broadcast_rows': broadcast_rows,
        'article_rows': article_rows,
        'print_rows': print_rows,
        'social_rows': social_rows,
        'sov_table': sov_table,
        # Publisher
        'pub_data': pub_data,
        # Print detail
        'print_detail': print_detail,
        # Print sentiment
        'print_pos': print_pos,
        'print_neu': print_neu,
        'print_neg': print_neg,
        # Broadcast sentiment
        'pos_bc': pos_bc,
        'neu_bc': neu_bc,
        'neg_bc': neg_bc,
        'pos_pct': pos_pct,
        'neu_pct': neu_pct,
        'neg_pct': neg_pct,
        # Competitor averages
        'comp_avg_art': comp_avg_art,
        'comp_avg_soc': comp_avg_soc,
        'comp_avg_ave': comp_avg_ave,
        # Hot topics
        'hot_topics': hot_topics,
        'hot_topics_json': hot_topics_json,
        # Chart JSON
        'bc_json': bc_json,
        'art_json': art_json,
        'soc_json': soc_json,
        'print_json': print_json,
        'pub_json': pub_json,
    }
    return render(request, 'monitor/report_competitor.html', context)


@login_required
def report_competitor_pptx(request, org_id):
    from io import BytesIO
    from pptx import Presentation
    from pptx.util import Inches, Pt, Emu
    from pptx.dml.color import RGBColor
    from pptx.enum.text import PP_ALIGN
    from pptx.chart.data import ChartData
    from pptx.enum.chart import XL_CHART_TYPE

    org = get_object_or_404(Organization, id=org_id)

    # ── Date range ────────────────────────────────────────────────────────────
    today = date.today()
    default_from = today.replace(day=1)
    raw_from = request.GET.get('date_from', '')
    raw_to = request.GET.get('date_to', '')
    try:
        df = date.fromisoformat(raw_from)
    except ValueError:
        df = default_from
    try:
        dt = date.fromisoformat(raw_to)
    except ValueError:
        dt = today

    month_label = df.strftime('%B %Y')

    # ── Gather org data ───────────────────────────────────────────────────────
    bc_qs = BroadcastMention.objects.filter(organization=org, date_published__range=(df, dt))
    art_qs = OnlineArticle.objects.filter(organization=org, date_published__range=(df, dt))
    print_qs = PrintArticle.objects.filter(organization=org, date_published__range=(df, dt))
    soc_qs = SocialMediaPost.objects.filter(organization=org, date_published__range=(df, dt))

    org_bc_count = bc_qs.count()
    org_art_count = art_qs.count()
    org_print_count = print_qs.count()
    org_soc_count = soc_qs.count()
    org_total = org_bc_count + org_art_count + org_print_count + org_soc_count

    org_art_ave = art_qs.aggregate(s=Sum('ave'))['s'] or 0
    org_art_reach = art_qs.aggregate(s=Sum('reach'))['s'] or 0

    # ── Competitor data ───────────────────────────────────────────────────────
    comp_names = list(org.competitors.values_list('name', flat=True))
    comp_orgs = {o.name: o for o in Organization.objects.filter(name__in=comp_names)}

    comp_data = {}
    for cname in comp_names:
        co = comp_orgs.get(cname)
        if co:
            c_bc = BroadcastMention.objects.filter(organization=co, date_published__range=(df, dt)).count()
            c_art = OnlineArticle.objects.filter(organization=co, date_published__range=(df, dt)).count()
            c_print = PrintArticle.objects.filter(organization=co, date_published__range=(df, dt)).count()
            c_soc = SocialMediaPost.objects.filter(organization=co, date_published__range=(df, dt)).count()
        else:
            c_bc = c_art = c_print = c_soc = 0
        comp_data[cname] = {'bc': c_bc, 'art': c_art, 'print': c_print, 'soc': c_soc}

    def _make_rows(org_count, comp_key):
        rows = [{'name': org.name, 'count': org_count, 'is_org': True}]
        for cname, cd in comp_data.items():
            rows.append({'name': cname, 'count': cd[comp_key], 'is_org': False})
        rows.sort(key=lambda r: r['count'], reverse=True)
        return rows

    broadcast_rows = _make_rows(org_bc_count, 'bc')
    article_rows = _make_rows(org_art_count, 'art')

    # Broadcast sentiment
    pos_bc = bc_qs.filter(sentiment='positive').count()
    neu_bc = bc_qs.filter(sentiment='neutral').count()
    neg_bc = bc_qs.filter(sentiment='negative').count()
    bc_total_sent = pos_bc + neu_bc + neg_bc or 1
    pos_pct = round(pos_bc / bc_total_sent * 100)
    neu_pct = round(neu_bc / bc_total_sent * 100)
    neg_pct = round(neg_bc / bc_total_sent * 100)

    # Print detail
    print_detail = list(
        PrintArticle.objects
        .filter(organization=org, date_published__range=(df, dt))
        .order_by('-date_published')[:5]
        .values('headline', 'source', 'date_published', 'sentiment', 'ave')
    )

    # ── Helpers ───────────────────────────────────────────────────────────────
    NAVY = RGBColor(0x1d, 0x2b, 0x45)
    BLUE = RGBColor(0x1d, 0x4e, 0xd8)
    WHITE = RGBColor(0xFF, 0xFF, 0xFF)
    LIGHT_GRAY = RGBColor(0xC0, 0xC8, 0xD8)
    GREEN = RGBColor(0x16, 0xA3, 0x4A)
    ORANGE = RGBColor(0xEA, 0x58, 0x0C)

    def add_text_box(slide, text, left, top, width, height,
                     font_size=12, bold=False, color=None, bg_color=None,
                     align=PP_ALIGN.LEFT, italic=False):
        txBox = slide.shapes.add_textbox(
            Inches(left), Inches(top), Inches(width), Inches(height)
        )
        tf = txBox.text_frame
        tf.word_wrap = True
        p = tf.paragraphs[0]
        p.alignment = align
        run = p.add_run()
        run.text = text
        run.font.size = Pt(font_size)
        run.font.bold = bold
        run.font.italic = italic
        if color:
            run.font.color.rgb = color
        if bg_color:
            fill = txBox.fill
            fill.solid()
            fill.fore_color.rgb = bg_color
        return txBox

    def add_colored_box(slide, left, top, width, height, bg_color):
        shape = slide.shapes.add_shape(
            1,  # MSO_SHAPE_TYPE.RECTANGLE
            Inches(left), Inches(top), Inches(width), Inches(height)
        )
        shape.fill.solid()
        shape.fill.fore_color.rgb = bg_color
        shape.line.fill.background()
        return shape

    # ── Presentation setup ────────────────────────────────────────────────────
    prs = Presentation()
    prs.slide_width = Emu(9144000)   # 10 inches
    prs.slide_height = Emu(5143500)  # 7.5 inches (16:9 approx)

    blank_layout = prs.slide_layouts[6]  # Blank

    # ── Slide 1: Title ────────────────────────────────────────────────────────
    slide1 = prs.slides.add_slide(blank_layout)
    bg = slide1.background
    fill = bg.fill
    fill.solid()
    fill.fore_color.rgb = NAVY

    add_text_box(slide1, org.name,
                 0.5, 1.5, 9.0, 1.2,
                 font_size=40, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
    add_text_box(slide1, 'Competitor Analysis Report',
                 0.5, 2.8, 9.0, 0.7,
                 font_size=20, color=WHITE, align=PP_ALIGN.CENTER)
    add_text_box(slide1, f'Media Monitoring Report | {month_label}',
                 0.5, 3.6, 9.0, 0.5,
                 font_size=14, color=LIGHT_GRAY, align=PP_ALIGN.CENTER)

    # ── Slide 2: KPIs ─────────────────────────────────────────────────────────
    slide2 = prs.slides.add_slide(blank_layout)
    add_text_box(slide2, 'Key Performance Indicators',
                 0.4, 0.2, 9.0, 0.6,
                 font_size=24, bold=True, color=NAVY)

    kpi_items = [
        ('Total Broadcast Mentions', str(org_bc_count), RGBColor(0x3B, 0x82, 0xF6)),
        ('Org Online Articles', str(org_art_count), NAVY),
        (f'Positive Sentiment', f'{pos_pct}%', GREEN),
        ('Online Article AVE (BWP)', f'{org_art_ave:,.0f}', ORANGE),
    ]
    box_w = 2.0
    box_h = 1.5
    gap = 0.2
    start_x = 0.4
    top = 1.2
    for i, (label, value, color) in enumerate(kpi_items):
        bx = start_x + i * (box_w + gap)
        box = add_colored_box(slide2, bx, top, box_w, box_h, color)
        add_text_box(slide2, label.upper(),
                     bx + 0.1, top + 0.1, box_w - 0.2, 0.4,
                     font_size=8, bold=True, color=WHITE)
        add_text_box(slide2, value,
                     bx + 0.1, top + 0.55, box_w - 0.2, 0.7,
                     font_size=28, bold=True, color=WHITE)

    # ── Slide 3: Broadcast chart ──────────────────────────────────────────────
    slide3 = prs.slides.add_slide(blank_layout)
    add_text_box(slide3, 'Broadcast & Radio Performance',
                 0.4, 0.2, 9.0, 0.6,
                 font_size=20, bold=True, color=NAVY)

    if broadcast_rows:
        chart_data = ChartData()
        chart_data.categories = [r['name'] for r in broadcast_rows]
        chart_data.add_series('Mentions', [r['count'] for r in broadcast_rows])
        chart = slide3.shapes.add_chart(
            XL_CHART_TYPE.BAR_CLUSTERED,
            Inches(0.4), Inches(1.0), Inches(9.0), Inches(5.5),
            chart_data,
        ).chart
        chart.has_legend = False

    # ── Slide 4: Online & Digital ─────────────────────────────────────────────
    slide4 = prs.slides.add_slide(blank_layout)
    add_text_box(slide4, 'Online & Digital Performance',
                 0.4, 0.2, 9.0, 0.6,
                 font_size=20, bold=True, color=NAVY)

    if article_rows:
        chart_data2 = ChartData()
        chart_data2.categories = [r['name'] for r in article_rows]
        chart_data2.add_series('Online Articles', [r['count'] for r in article_rows])
        chart2 = slide4.shapes.add_chart(
            XL_CHART_TYPE.BAR_CLUSTERED,
            Inches(0.4), Inches(1.0), Inches(9.0), Inches(5.5),
            chart_data2,
        ).chart
        chart2.has_legend = False

    # ── Slide 5: Print table ──────────────────────────────────────────────────
    slide5 = prs.slides.add_slide(blank_layout)
    add_text_box(slide5, 'Newspaper Coverage Analysis',
                 0.4, 0.2, 9.0, 0.6,
                 font_size=20, bold=True, color=NAVY)

    if print_detail:
        rows_count = min(len(print_detail), 5) + 1  # +1 header
        cols = 5
        tbl = slide5.shapes.add_table(
            rows_count, cols,
            Inches(0.4), Inches(1.0), Inches(9.2), Inches(0.45 * rows_count)
        ).table
        headers = ['Headline', 'Publisher', 'Date', 'Sentiment', 'AVE']
        for ci, h in enumerate(headers):
            cell = tbl.cell(0, ci)
            cell.text = h
            cell.fill.solid()
            cell.fill.fore_color.rgb = NAVY
            p = cell.text_frame.paragraphs[0]
            run = p.add_run() if not p.runs else p.runs[0]
            run.font.bold = True
            run.font.color.rgb = WHITE
            run.font.size = Pt(10)

        for ri, row in enumerate(print_detail[:5], start=1):
            vals = [
                (row.get('headline') or '')[:60],
                row.get('source') or '',
                str(row.get('date') or ''),
                row.get('sentiment') or '',
                f"BWP {row.get('ave') or 0:,.0f}",
            ]
            for ci, v in enumerate(vals):
                cell = tbl.cell(ri, ci)
                cell.text = v
                p = cell.text_frame.paragraphs[0]
                run = p.add_run() if not p.runs else p.runs[0]
                run.font.size = Pt(9)
                if ri % 2 == 0:
                    cell.fill.solid()
                    cell.fill.fore_color.rgb = RGBColor(0xF9, 0xFA, 0xFB)

    # ── Slide 6: Sentiment ────────────────────────────────────────────────────
    slide6 = prs.slides.add_slide(blank_layout)
    add_text_box(slide6, 'Overall Sentiment Analysis',
                 0.4, 0.2, 9.0, 0.6,
                 font_size=20, bold=True, color=NAVY)

    sent_items = [
        ('Positive', f'{pos_pct}%', str(pos_bc), GREEN),
        ('Neutral', f'{neu_pct}%', str(neu_bc), RGBColor(0x64, 0x74, 0x8B)),
        ('Negative', f'{neg_pct}%', str(neg_bc), RGBColor(0xDC, 0x26, 0x26)),
    ]
    box_w2 = 2.7
    for i, (label, pct, count, color) in enumerate(sent_items):
        bx = 0.4 + i * (box_w2 + 0.3)
        # Header box
        hdr = add_colored_box(slide6, bx, 1.1, box_w2, 0.5, color)
        add_text_box(slide6, label,
                     bx + 0.1, 1.15, box_w2 - 0.2, 0.4,
                     font_size=14, bold=True, color=WHITE, align=PP_ALIGN.CENTER)
        # Body box
        add_colored_box(slide6, bx, 1.6, box_w2, 1.4, WHITE)
        add_text_box(slide6, pct,
                     bx + 0.1, 1.65, box_w2 - 0.2, 0.7,
                     font_size=28, bold=True, color=color, align=PP_ALIGN.CENTER)
        add_text_box(slide6, f'{count} mentions',
                     bx + 0.1, 2.35, box_w2 - 0.2, 0.4,
                     font_size=10, color=RGBColor(0x6B, 0x72, 0x80), align=PP_ALIGN.CENTER)

    # Summary paragraph
    add_text_box(
        slide6,
        f'{org.name} received {org_total} total media mentions during {month_label}. '
        f'Positive sentiment accounted for {pos_pct}% of broadcast coverage, '
        f'demonstrating overall favourable media positioning.',
        0.4, 3.3, 9.0, 1.2,
        font_size=11, color=RGBColor(0x37, 0x41, 0x51),
    )

    # ── Return PPTX ───────────────────────────────────────────────────────────
    buf = BytesIO()
    prs.save(buf)
    buf.seek(0)
    filename = f"{org.name.replace(' ', '_')}_Competitor_Analysis.pptx"
    response = HttpResponse(
        buf.read(),
        content_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


# ── Media Sources ─────────────────────────────────────────────────────────────

@login_required
def media_sources(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    all_orgs = Organization.objects.all().order_by('name')
    sources = MediaSource.objects.filter(organization=org).order_by('name')
    return render(request, 'monitor/media_sources.html', {
        'org': org,
        'all_orgs': all_orgs,
        'sources': sources,
        'source_type_choices': SOURCE_TYPE_CHOICES,
        'page': 'media_sources',
    })


@login_required
@require_http_methods(['POST'])
def media_source_create(request, org_id):
    org = get_object_or_404(Organization, id=org_id)
    data = json.loads(request.body)
    source = MediaSource.objects.create(
        organization=org,
        name=data.get('name', '').strip(),
        source_type=data.get('source_type', 'online'),
        url=data.get('url', '').strip(),
        handle=data.get('handle', '').strip(),
        country=data.get('country', '').strip(),
        reach=int(data.get('reach', 0) or 0),
    )
    return JsonResponse({
        'id': source.id,
        'name': source.name,
        'source_type': source.source_type,
        'url': source.url,
        'handle': source.handle,
        'country': source.country,
        'reach': source.reach,
    })


@login_required
@require_http_methods(['PUT'])
def media_source_update(request, org_id, source_id):
    org = get_object_or_404(Organization, id=org_id)
    source = get_object_or_404(MediaSource, id=source_id, organization=org)
    data = json.loads(request.body)
    source.name = data.get('name', source.name).strip()
    source.source_type = data.get('source_type', source.source_type)
    source.url = data.get('url', source.url).strip()
    source.handle = data.get('handle', source.handle).strip()
    source.country = data.get('country', source.country).strip()
    source.reach = int(data.get('reach', source.reach) or 0)
    source.save()
    return JsonResponse({'ok': True})


@login_required
@require_http_methods(['DELETE'])
def media_source_delete(request, org_id, source_id):
    org = get_object_or_404(Organization, id=org_id)
    source = get_object_or_404(MediaSource, id=source_id, organization=org)
    source.delete()
    return JsonResponse({'ok': True})


# ── Media Monitor Webhook ─────────────────────────────────────────────────────

@csrf_exempt
@require_http_methods(['POST'])
def media_monitor_webhook(request, org_id):
    """
    Receive an article-match webhook from media-monitor and store it as
    an OnlineArticle for the given organisation.

    Expected payload:
        {
          "alert_name": "...",
          "matched_keywords": ["kw1", ...],
          "article": {
            "title": "...", "url": "...", "source_domain": "...",
            "summary": "...", "published_at": "2024-01-01T...",
            "sentiment": "positive|neutral|negative", "language": "en"
          }
        }

    Security: requests must include X-Webhook-Secret matching
    settings.MEDIA_MONITOR_WEBHOOK_SECRET (ignored when secret is empty).
    """
    from django.conf import settings as _settings

    secret = _settings.MEDIA_MONITOR_WEBHOOK_SECRET
    if secret and request.headers.get('X-Webhook-Secret') != secret:
        return JsonResponse({'error': 'Forbidden'}, status=403)

    org = get_object_or_404(Organization, id=org_id)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, ValueError):
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    art = body.get('article', {})
    title = (art.get('title') or '').strip()
    if not title:
        return JsonResponse({'error': 'Missing title'}, status=400)

    url_val       = (art.get('url') or '').strip()
    source        = (art.get('source_domain') or '').strip()
    summary       = (art.get('summary') or '').strip()
    country       = (art.get('country') or '').strip()
    raw_sentiment = (art.get('sentiment') or 'neutral').lower()
    sentiment     = raw_sentiment if raw_sentiment in ('positive', 'neutral', 'negative') else 'neutral'

    raw_date = art.get('published_at')
    pub_date = None
    if raw_date:
        try:
            from datetime import datetime
            pub_date = datetime.fromisoformat(raw_date).date()
        except (ValueError, TypeError):
            pass
    if pub_date is None:
        pub_date = date.today()

    if url_val and OnlineArticle.objects.filter(organization=org, url=url_val).exists():
        return JsonResponse({'ok': True, 'duplicate': True})

    article = OnlineArticle.objects.create(
        organization  = org,
        source        = source or 'media-monitor',
        headline      = title[:500],
        summary       = summary,
        url           = url_val,
        date_published= pub_date,
        country       = country,
        sentiment     = sentiment,
    )
    return JsonResponse({'ok': True, 'id': article.id})


# ── Profile & Password ────────────────────────────────────────────────────────

@login_required
@require_http_methods(['POST'])
def profile_update(request, org_id):
    data = json.loads(request.body)
    user = request.user
    user.first_name = data.get('first_name', user.first_name).strip()
    user.last_name = data.get('last_name', user.last_name).strip()
    email = data.get('email', '').strip()
    if email and email != user.email:
        if User.objects.filter(email=email).exclude(pk=user.pk).exists():
            return JsonResponse({'error': 'Email already in use.'}, status=400)
        user.email = email
    user.save()
    return JsonResponse({'ok': True, 'full_name': user.get_full_name(), 'initials': user.get_initials()})


@login_required
@require_http_methods(['POST'])
def password_change(request, org_id):
    data = json.loads(request.body)
    user = request.user
    current = data.get('current_password', '')
    new_pw = data.get('new_password', '')
    confirm = data.get('confirm_password', '')
    if not user.check_password(current):
        return JsonResponse({'error': 'Current password is incorrect.'}, status=400)
    if len(new_pw) < 8:
        return JsonResponse({'error': 'New password must be at least 8 characters.'}, status=400)
    if new_pw != confirm:
        return JsonResponse({'error': 'Passwords do not match.'}, status=400)
    user.set_password(new_pw)
    user.save()
    from django.contrib.auth import update_session_auth_hash
    update_session_auth_hash(request, user)
    return JsonResponse({'ok': True})
