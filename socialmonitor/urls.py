from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', include('monitor.urls')),

    # Password reset flow
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='monitor/password_reset.html',
        email_template_name='monitor/password_reset_email.txt',
        subject_template_name='monitor/password_reset_subject.txt',
        success_url='/password-reset/sent/',
    ), name='password_reset'),

    path('password-reset/sent/', auth_views.PasswordResetDoneView.as_view(
        template_name='monitor/password_reset_done.html',
    ), name='password_reset_done'),

    path('reset/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='monitor/password_reset_confirm.html',
        success_url='/reset/complete/',
    ), name='password_reset_confirm'),

    path('reset/complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='monitor/password_reset_complete.html',
    ), name='password_reset_complete'),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
