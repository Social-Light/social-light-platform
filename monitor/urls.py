from django.urls import path
from . import views

app_name = 'monitor'

urlpatterns = [
    # Landing
    path('', views.home, name='home'),

    # Auth
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

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
    path('api/<uuid:org_id>/online/<int:article_id>/', views.online_article_update, name='online_update'),
    path('api/<uuid:org_id>/online/<int:article_id>/delete/', views.online_article_delete, name='online_delete'),

    # Media: Print
    path('app/media/print/<uuid:org_id>/', views.media_print, name='media_print'),
    path('api/<uuid:org_id>/print/', views.print_article_create, name='print_create'),
    path('api/<uuid:org_id>/print/<int:article_id>/', views.print_article_update, name='print_update'),
    path('api/<uuid:org_id>/print/<int:article_id>/delete/', views.print_article_delete, name='print_delete'),

    # Media: Social
    path('app/media/social/<uuid:org_id>/', views.media_social, name='media_social'),
    path('api/<uuid:org_id>/social/', views.social_post_create, name='social_create'),
    path('api/<uuid:org_id>/social/<int:post_id>/delete/', views.social_post_delete, name='social_delete'),

    # Media: Broadcast
    path('app/media/broadcast/<uuid:org_id>/', views.media_broadcast, name='media_broadcast'),
    path('api/<uuid:org_id>/broadcast/', views.broadcast_create, name='broadcast_create'),
    path('api/<uuid:org_id>/broadcast/<int:mention_id>/delete/', views.broadcast_delete, name='broadcast_delete'),

    # Competitors
    path('app/competitors/<uuid:org_id>/', views.competitors_view, name='competitors'),
    path('api/<uuid:org_id>/competitors/', views.competitor_create, name='competitor_create'),
    path('api/<uuid:org_id>/competitors/<int:comp_id>/delete/', views.competitor_delete, name='competitor_delete'),

    # Reports
    path('app/reports/<uuid:org_id>/', views.reports_view, name='reports'),
    path('app/reports/<uuid:org_id>/competitor/', views.report_competitor, name='report_competitor'),
    path('app/reports/<uuid:org_id>/competitor/pptx/', views.report_competitor_pptx, name='report_competitor_pptx'),

    # Alerts
    path('app/alerts/<uuid:org_id>/', views.alerts_view, name='alerts'),
    path('api/<uuid:org_id>/alerts/', views.alert_create, name='alert_create'),
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
    path('api/<uuid:org_id>/sources/<int:source_id>/', views.media_source_update, name='media_source_update'),
    path('api/<uuid:org_id>/sources/<int:source_id>/delete/', views.media_source_delete, name='media_source_delete'),

    # Profile & Password
    path('api/<uuid:org_id>/profile/', views.profile_update, name='profile_update'),
    path('api/<uuid:org_id>/profile/password/', views.password_change, name='password_change'),
]
