from django.urls import path
from . import views
from . import subscription_views
from . import onboarding_views
from . import export_views
from . import assessment_views

app_name = 'monitor'

urlpatterns = [
    # Landing
    path('', views.home, name='home'),
    path('pricing/', subscription_views.pricing, name='pricing'),

    # ── Free assessment ──────────────────────────────────────────────────────
    # Public and pre-signup: this is the step before somebody becomes a user, so
    # it has a real URL of its own rather than living as a tab inside the landing
    # page. Marketing needs something it can link to from a campaign.
    path('assessment/', assessment_views.assessment_page, name='assessment'),
    path('api/assessment/', assessment_views.assessment_submit, name='assessment_submit'),
    path('api/assessment/<uuid:submission_id>/request/',
         assessment_views.assessment_action, name='assessment_action'),

    # Auth
    path('login/', views.login_view, name='login'),
    path('signup/', subscription_views.signup, name='signup'),
    path('logout/', views.logout_view, name='logout'),

    # ── Onboarding ───────────────────────────────────────────────────────────
    # One URL per step. The step a user may be on is decided by
    # monitor/onboarding.py, not by which of these they can guess.
    path('onboarding/verify/', onboarding_views.verify, name='onboarding_verify'),
    path('onboarding/verify/<str:token>/', onboarding_views.verify_confirm, name='onboarding_verify_confirm'),
    path('onboarding/profile/', onboarding_views.profile, name='onboarding_profile'),
    path('onboarding/organisation/', onboarding_views.agency, name='onboarding_agency'),
    path('onboarding/terms/', onboarding_views.terms, name='onboarding_terms'),
    path('onboarding/privacy/', onboarding_views.privacy, name='onboarding_privacy'),
    path('onboarding/disclaimer/', onboarding_views.disclaimer, name='onboarding_disclaimer'),
    path('onboarding/payment/', onboarding_views.payment, name='onboarding_payment'),
    path('onboarding/plan/', onboarding_views.plan, name='onboarding_plan'),
    path('onboarding/complete/', onboarding_views.done, name='onboarding_done'),
    path('onboarding/status/', onboarding_views.status, name='onboarding_status'),
    path('app/legal/', onboarding_views.reconsent, name='reconsent'),

    # Subscription / free trial
    path('app/billing/', subscription_views.billing, name='billing'),
    path('app/billing/request/', subscription_views.package_request, name='package_request'),

    # Organizations
    path('app/organizations/', views.organizations, name='organizations'),
    path('app/organizations/manage/', views.manage_organizations, name='manage_organizations'),

    # Settings
    path('app/settings/<uuid:org_id>/', views.settings_view, name='settings'),
    path('api/organizations/', views.organization_create, name='org_create'),
    path('api/organizations/<uuid:org_id>/', views.organization_update, name='org_update'),
    path('api/organizations/<uuid:org_id>/delete/', views.organization_delete, name='org_delete'),
    path('api/organizations/<uuid:org_id>/details/', views.org_details, name='org_details'),

    # Dashboard
    path('app/dashboard/<uuid:org_id>/', views.dashboard, name='dashboard'),

    # Analytics
    path('app/analytics/<uuid:org_id>/', views.analytics, name='analytics'),

    # Media: Online Articles
    path('app/media/online/<uuid:org_id>/', views.media_online, name='media_online'),
    path('api/<uuid:org_id>/online/', views.online_article_create, name='online_create'),
    path('api/<uuid:org_id>/online/upload/', views.online_article_csv_upload, name='online_csv_upload'),
    path('api/<uuid:org_id>/online/<int:article_id>/', views.online_article_update, name='online_update'),
    path('api/<uuid:org_id>/online/<int:article_id>/delete/', views.online_article_delete, name='online_delete'),

    # Media: Print
    path('app/media/print/<uuid:org_id>/', views.media_print, name='media_print'),
    path('api/<uuid:org_id>/print/', views.print_article_create, name='print_create'),
    path('api/<uuid:org_id>/print/upload/', views.print_article_csv_upload, name='print_csv_upload'),
    path('api/<uuid:org_id>/print/<int:article_id>/', views.print_article_update, name='print_update'),
    path('api/<uuid:org_id>/print/<int:article_id>/delete/', views.print_article_delete, name='print_delete'),

    # Media: Social
    path('app/media/social/<uuid:org_id>/', views.media_social, name='media_social'),
    path('api/<uuid:org_id>/social/', views.social_post_create, name='social_create'),
    path('api/<uuid:org_id>/social/<int:post_id>/', views.social_post_update, name='social_update'),
    path('api/<uuid:org_id>/social/<int:post_id>/delete/', views.social_post_delete, name='social_delete'),
    path('api/<uuid:org_id>/social/upload/', views.social_post_csv_upload, name='social_csv_upload'),

    # Media: Broadcast
    path('app/media/broadcast/<uuid:org_id>/', views.media_broadcast, name='media_broadcast'),
    path('api/<uuid:org_id>/broadcast/', views.broadcast_create, name='broadcast_create'),
    path('api/<uuid:org_id>/broadcast/<int:mention_id>/', views.broadcast_update, name='broadcast_update'),
    path('api/<uuid:org_id>/broadcast/<int:mention_id>/delete/', views.broadcast_delete, name='broadcast_delete'),
    path('api/<uuid:org_id>/broadcast/upload/', views.broadcast_csv_upload, name='broadcast_csv_upload'),

    # Competitors
    path('app/competitors/<uuid:org_id>/', views.competitors_view, name='competitors'),
    path('api/<uuid:org_id>/competitors/', views.competitor_create, name='competitor_create'),
    path('api/<uuid:org_id>/competitors/articles/upload/', views.competitor_article_csv_upload, name='competitor_article_csv_upload'),
    path('api/<uuid:org_id>/competitors/articles/<int:article_id>/delete/', views.competitor_article_delete, name='competitor_article_delete'),
    path('api/<uuid:org_id>/competitors/<int:comp_id>/', views.competitor_update, name='competitor_update'),
    path('api/<uuid:org_id>/competitors/<int:comp_id>/delete/', views.competitor_delete, name='competitor_delete'),

    # Reports
    path('app/reports/<uuid:org_id>/', views.reports_view, name='reports'),
    path('app/reports/<uuid:org_id>/full/', views.report_full, name='report_full'),
    path('api/<uuid:org_id>/report-ai/', views.report_ai_generate, name='report_ai_generate'),
    path('app/reports/<uuid:org_id>/sentiment/', views.report_sentiment, name='report_sentiment'),
    path('app/reports/<uuid:org_id>/source/', views.report_source, name='report_source'),
    path('app/reports/<uuid:org_id>/competitor/', views.report_competitor, name='report_competitor'),
    path('app/reports/<uuid:org_id>/competitor/pptx/', views.report_competitor_pptx, name='report_competitor_pptx'),
    path('api/<uuid:org_id>/reports/save/', views.report_save, name='report_save'),
    path('api/<uuid:org_id>/reports/<uuid:report_id>/delete/', views.report_delete, name='report_delete'),
    # Issue-focused ("saga") reports
    path('app/reports/<uuid:org_id>/issue/<uuid:report_id>/', views.report_issue, name='report_issue'),
    path('app/reports/<uuid:org_id>/issue/<uuid:report_id>/pdf/', views.report_issue_pdf, name='report_issue_pdf'),
    path('api/<uuid:org_id>/reports/issue/', views.report_issue_generate, name='report_issue_generate'),
    path('api/<uuid:org_id>/reports/issue/<uuid:report_id>/delete/', views.report_issue_delete, name='report_issue_delete'),

    # Campaign tracking & reporting
    path('app/campaigns/<uuid:org_id>/', views.campaigns_view, name='campaigns'),
    path('app/campaigns/<uuid:org_id>/<uuid:campaign_id>/', views.campaign_detail, name='campaign_detail'),
    path('api/<uuid:org_id>/campaigns/save/', views.campaign_save, name='campaign_save'),
    path('api/<uuid:org_id>/campaigns/<uuid:campaign_id>/delete/', views.campaign_delete, name='campaign_delete'),
    path('api/<uuid:org_id>/campaigns/<uuid:campaign_id>/report/', views.campaign_generate_report, name='campaign_generate_report'),

    # Alerts
    path('app/alerts/<uuid:org_id>/', views.alerts_view, name='alerts'),
    path('api/<uuid:org_id>/alerts/', views.alert_create, name='alert_create'),
    path('api/<uuid:org_id>/alerts/<int:alert_id>/', views.alert_update, name='alert_update'),
    path('api/<uuid:org_id>/alerts/<int:alert_id>/test/', views.alert_test_send, name='alert_test_send'),
    path('api/<uuid:org_id>/alerts/<int:alert_id>/delete/', views.alert_delete, name='alert_delete'),

    # Users
    path('app/users/<uuid:org_id>/', views.users_view, name='users'),
    path('api/<uuid:org_id>/users/', views.user_create, name='user_create'),
    path('api/<uuid:org_id>/users/<int:user_id>/', views.user_update, name='user_update'),
    path('api/<uuid:org_id>/users/<int:user_id>/delete/', views.user_delete, name='user_delete'),

    # Keywords
    path('api/<uuid:org_id>/keywords/', views.keyword_create, name='keyword_create'),
    path('api/<uuid:org_id>/keywords/<int:kw_id>/delete/', views.keyword_delete, name='keyword_delete'),

    # FAQs
    path('app/faqs/<uuid:org_id>/', views.faqs_view, name='faqs'),

    # Media Sources
    path('app/media/sources/<uuid:org_id>/', views.media_sources, name='media_sources'),
    path('api/<uuid:org_id>/sources/', views.media_source_create, name='media_source_create'),
    path('api/<uuid:org_id>/sources/upload/', views.media_source_csv_upload, name='media_source_csv_upload'),
    path('api/<uuid:org_id>/sources/<int:source_id>/', views.media_source_update, name='media_source_update'),
    path('api/<uuid:org_id>/sources/<int:source_id>/delete/', views.media_source_delete, name='media_source_delete'),

    # Profile & Password
    path('api/<uuid:org_id>/profile/', views.profile_update, name='profile_update'),
    path('api/<uuid:org_id>/profile/password/', views.password_change, name='password_change'),

    # Coverage / crawl-result export (paid: crawl_result_download)
    path('api/<uuid:org_id>/export/<str:media_type>/', export_views.coverage_export,
         name='coverage_export'),

    # Media Monitor webhook receiver
    path('api/<uuid:org_id>/webhook/media-monitor/', views.media_monitor_webhook, name='media_monitor_webhook'),
]
