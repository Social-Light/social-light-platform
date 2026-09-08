from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.shortcuts import redirect, render
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from .entitlements import FEATURES, feature_choices
from .models import (AgencyDeclaration, AssessmentSubmission, CommodityQuote, ConsentRecord,
                     EmailVerificationToken, Event, LegalDocument, OnboardingProgress,
                     Organization, Package, Payment, PaymentMethod, Publication, Sector,
                     SectorStory, SubscriptionRequest, User)

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'industry', 'country', 'status', 'plan_status', 'package',
                    'trial_ends_at', 'created_at')
    list_filter = ('plan_status', 'status', 'industry', 'country', 'created_at')
    search_fields = ('name', 'email', 'country')
    readonly_fields = ('id', 'created_at', 'subscription_activated_at', 'entitlement_overview',
                       'payment_overview')
    actions = ('assign_plan', 'start_free_trial', 'extend_trial_14_days')
    fieldsets = (
        ('Organization Info', {
            'fields': ('id', 'name', 'address', 'country', 'email', 'phone', 'website')
        }),
        ('Subscription', {
            'fields': ('plan_status', 'package', 'trial_started_at', 'trial_ends_at',
                       'subscription_activated_at'),
            'description': ('Set <b>plan_status</b> to "Paid subscription" and pick a package to '
                            'restore access after a trial ends. Organisations created before '
                            'self-signup are "Paid subscription" and unaffected.'),
        }),
        ('What this organisation can do', {
            'fields': ('entitlement_overview',),
            'classes': ('collapse',),
        }),
        ('Payments & settlement', {
            'fields': ('payment_overview',),
            'classes': ('collapse',),
            'description': ('Read-only. No card details are held here or anywhere else in this '
                            'system — only the payment provider’s token, which is not shown.'),
        }),
        ('Social Media', {
            'fields': ('facebook_url', 'linkedin_url', 'x_handle')
        }),
        ('Branding', {
            'fields': ('gradient_color1', 'gradient_color2', 'logo')
        }),
        ('Timestamps', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )

    # ── Manual plan assignment ───────────────────────────────────────────────
    @admin.action(description='Assign a plan manually')
    def assign_plan(self, request, queryset):
        """Put the selected organisations on a plan without a payment gateway.

        This is the path the business needs when PAYMENTS_ENABLED is off: the fee
        is settled by invoice or transfer, and an administrator then activates
        the plan here. It calls the same ``activate_package`` the gateway path
        would, so the organisation ends up in exactly the same state either way.
        """
        packages = Package.objects.filter(is_active=True)

        if 'apply' in request.POST:
            package = packages.filter(pk=request.POST.get('package')).first()
            if package is None:
                self.message_user(request, 'Choose a plan.', level=messages.ERROR)
            else:
                for org in queryset:
                    org.activate_package(package)
                self.message_user(
                    request,
                    f'Activated {package.name} for {queryset.count()} organisation(s).')
                return redirect(request.get_full_path())

        return render(request, 'admin/monitor/assign_plan.html', {
            **self.admin_site.each_context(request),
            'title': 'Assign a plan',
            'organizations': queryset,
            'packages': packages,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'payments_enabled': getattr(settings, 'PAYMENTS_ENABLED', False),
        })

    # ── Read-only overviews ──────────────────────────────────────────────────
    @admin.display(description='Entitlements in force')
    def entitlement_overview(self, obj):
        from .entitlements import entitlements_for_organization

        if obj.pk is None:
            return '—'
        granted = sorted(entitlements_for_organization(obj))
        source = {
            'trial': 'free trial (full access)',
            'active': obj.package.name if obj.package else 'paid, no package set (full access)',
        }.get(obj.effective_plan_status, 'free tier')
        rows = ''.join(
            f'<li>{FEATURES[c].label}</li>' for c in granted if c in FEATURES) or '<li>none</li>'
        return format_html(
            '<p style="margin:0 0 6px;">Status: <b>{}</b> — from {}</p><ul style="margin:0;">{}</ul>',
            obj.effective_plan_status, source, format_html(rows))

    @admin.display(description='Payments')
    def payment_overview(self, obj):
        if obj.pk is None:
            return '—'
        payments = obj.payments.all()[:10]
        if not payments:
            return 'No payments recorded.'
        rows = ''.join(
            '<tr><td style="padding:2px 12px 2px 0;">{}</td><td style="padding:2px 12px 2px 0;">{} {}</td>'
            '<td style="padding:2px 12px 2px 0;">{}</td><td style="padding:2px 0;">{}</td></tr>'.format(
                p.paid_at.strftime('%d %b %Y') if p.paid_at else '—',
                p.currency, p.amount, p.get_status_display(), p.settlement_status)
            for p in payments)
        return format_html(
            '<table><tr><th style="text-align:left;padding-right:12px;">Paid</th>'
            '<th style="text-align:left;padding-right:12px;">Amount</th>'
            '<th style="text-align:left;padding-right:12px;">Status</th>'
            '<th style="text-align:left;">Settlement</th></tr>{}</table>', format_html(rows))

    @admin.action(description='Start a fresh free trial')
    def start_free_trial(self, request, queryset):
        for org in queryset:
            org.start_trial()
            org.save(update_fields=['plan_status', 'trial_started_at', 'trial_ends_at'])
        self.message_user(request, f'Started a free trial for {queryset.count()} organisation(s).')

    @admin.action(description='Extend trial by 14 days')
    def extend_trial_14_days(self, request, queryset):
        from datetime import timedelta
        for org in queryset:
            base = org.trial_ends_at if org.trial_ends_at and org.trial_ends_at > timezone.now() else timezone.now()
            org.trial_ends_at = base + timedelta(days=14)
            org.plan_status = 'trial'
            org.save(update_fields=['plan_status', 'trial_ends_at'])
        self.message_user(request, f'Extended {queryset.count()} trial(s) by 14 days.')


class PackageAdminForm(forms.ModelForm):
    """Entitlements as tick boxes rather than hand-typed JSON.

    The stored value is still a JSON list of codes; this only changes how it is
    edited. Typing entitlement codes by hand into a textarea is how a client ends
    up silently losing a feature to a typo, and this is the field that decides
    what people are allowed to do.
    """
    entitlements = forms.MultipleChoiceField(
        choices=feature_choices, required=False,
        widget=forms.CheckboxSelectMultiple,
        help_text='What this tier unlocks. This is what the application actually enforces — '
                  'the marketing bullets above are display text only and grant nothing.')

    class Meta:
        model = Package
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        current = self.instance.entitlement_codes if self.instance and self.instance.pk else []
        self.fields['entitlements'].initial = current

    def clean_entitlements(self):
        return list(self.cleaned_data.get('entitlements') or [])


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    form = PackageAdminForm
    list_display = ('name', 'price_display', 'billing_period', 'entitlement_count',
                    'is_featured', 'is_active', 'is_public', 'sort_order')
    list_editable = ('is_featured', 'is_active', 'is_public', 'sort_order')
    list_filter = ('is_active', 'is_public', 'is_featured', 'billing_period')
    search_fields = ('name', 'tagline')
    prepopulated_fields = {'slug': ('name',)}
    fieldsets = (
        (None, {'fields': ('name', 'slug', 'eyebrow', 'tagline', 'sort_order')}),
        ('Price', {
            'fields': ('price', 'currency', 'billing_period', 'price_override', 'price_note'),
            'description': ('Fill in <b>price override</b> (e.g. "Custom") for a tier that is quoted '
                            'rather than listed — it replaces the figure and hides the period. '
                            'Use <b>price note</b> for the annual line, e.g. "Billed annually · $588 per year".'),
        }),
        ('Card contents', {
            'fields': ('features', 'excluded_features', 'exclusion_note',
                       'highlight_title', 'highlight_body'),
            'description': ('<b>Features</b> and <b>excluded features</b> are JSON lists of bullet '
                            'strings, e.g. ["50 keywords", "5 competitors"]. Exclusions render greyed '
                            'out with a dash.'),
        }),
        ('Entitlements', {
            'fields': ('entitlements',),
            'description': ('<b>This is the field that controls access.</b> The application asks '
                            '<code>user.has_feature("&lt;code&gt;")</code> and rejects the request at '
                            'the API when the answer is no — hiding a button is not what stops '
                            'anyone. Changing these ticks changes what every organisation on this '
                            'tier can do, immediately.'),
        }),
        ('Presentation', {'fields': ('accent_color', 'is_dark', 'cta_label', 'contact_only')}),
        ('Visibility', {
            'fields': ('is_featured', 'is_active', 'is_public'),
            'description': ('<b>Active</b> means the tier can be chosen or assigned. '
                            '<b>Public</b> means it also appears on the published price list — '
                            'untick it for a tier that is assignable but not advertised.'),
        }),
    )

    @admin.display(description='Entitlements')
    def entitlement_count(self, obj):
        total = len(FEATURES)
        granted = len(obj.entitlement_codes)
        colour = '#dc2626' if granted == 0 else '#166534'
        return format_html('<span style="color:{}">{} of {}</span>', colour, granted, total)


@admin.register(SubscriptionRequest)
class SubscriptionRequestAdmin(admin.ModelAdmin):
    list_display = ('organization', 'package', 'contact_name', 'contact_email', 'status', 'created_at')
    list_filter = ('status', 'package', 'created_at')
    search_fields = ('organization__name', 'contact_name', 'contact_email', 'note')
    readonly_fields = ('id', 'created_at', 'organization', 'package', 'requested_by',
                       'contact_name', 'contact_email', 'contact_phone', 'note')
    actions = ('approve_and_activate', 'mark_declined')

    @admin.action(description='Approve — activate the requested package')
    def approve_and_activate(self, request, queryset):
        activated = 0
        for req in queryset.select_related('organization', 'package'):
            if not req.package:
                continue
            req.organization.activate_package(req.package)
            req.status = 'approved'
            req.handled_at = timezone.now()
            req.handled_by = request.user
            req.save(update_fields=['status', 'handled_at', 'handled_by'])
            activated += 1
        self.message_user(request, f'Activated {activated} subscription(s).')

    @admin.action(description='Decline')
    def mark_declined(self, request, queryset):
        queryset.update(status='declined', handled_at=timezone.now(), handled_by=request.user)

@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('title', 'organization', 'category', 'event_type', 'created_at')
    list_filter = ('category', 'event_type', 'created_at')
    search_fields = ('title', 'summary', 'organization__name')
    readonly_fields = ('created_at',)


class AgencyDeclarationInline(admin.StackedInline):
    model = AgencyDeclaration
    extra = 0
    can_delete = False
    readonly_fields = ('authorised_at', 'authorisation_ip', 'created_at', 'updated_at')
    fieldsets = (
        (None, {'fields': ('account_type', 'organization')}),
        ('Organisation represented', {
            'fields': ('organisation_name', 'position', 'organisation_email',
                       'organisation_phone', 'registration_number'),
        }),
        ('Authority', {
            'fields': ('is_authorised_representative', 'authorised_at', 'authorisation_ip'),
            'description': 'What the user confirmed about their authority to act for the organisation.',
        }),
        ('Further verification', {
            'fields': ('verification_status', 'supporting_document', 'verification_note'),
            'description': ('Not required by the current onboarding rules. These fields exist so a '
                            'document requirement can be introduced later without a schema change.'),
        }),
    )


class OnboardingProgressInline(admin.StackedInline):
    model = OnboardingProgress
    extra = 0
    can_delete = False
    readonly_fields = ('history', 'started_at', 'completed_at', 'updated_at')
    fields = ('state', 'is_legacy', 'payment_skipped', 'history',
              'started_at', 'completed_at', 'updated_at')


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('email', 'first_name', 'last_name', 'role', 'verified_flag',
                    'onboarding_state', 'organization', 'is_active', 'date_joined')
    list_filter = ('role', 'email_verified', 'is_active', 'onboarding__state',
                   'onboarding__is_legacy', 'date_joined')
    search_fields = ('email', 'first_name', 'last_name', 'phone', 'organization__name')
    readonly_fields = ('id', 'date_joined', 'last_login', 'email_verified_at', 'consent_overview')
    inlines = (AgencyDeclarationInline, OnboardingProgressInline)
    actions = ('mark_email_verified', 'resend_verification_email')
    fieldsets = (
        ('Account', {
            'fields': ('id', 'email', 'password')
        }),
        ('Personal Info', {
            'fields': ('first_name', 'last_name', 'phone', 'job_title', 'country')
        }),
        ('Verification', {
            'fields': ('email_verified', 'email_verified_at'),
            'description': ('Accounts that predate email verification were marked verified on '
                            'migration. Unticking this sends the account back to the verify step '
                            'the next time it loads a page.'),
        }),
        ('Consent', {
            'fields': ('consent_overview',),
            'description': ('Read-only. Consent records are append-only and cannot be edited from '
                            'anywhere in this application — see Consent records for the full trail.'),
        }),
        ('Permissions', {
            'fields': ('role', 'organization', 'is_active', 'is_staff', 'is_superuser',
                       'groups', 'user_permissions')
        }),
        ('Timestamps', {
            'fields': ('date_joined', 'last_login'),
            'classes': ('collapse',)
        }),
    )

    @admin.display(description='Verified', boolean=True, ordering='email_verified')
    def verified_flag(self, obj):
        return obj.email_verified

    @admin.display(description='Onboarding', ordering='onboarding__state')
    def onboarding_state(self, obj):
        progress = obj.onboarding_progress
        if progress is None:
            return '—'
        label = progress.get_state_display()
        return f'{label} (legacy)' if progress.is_legacy else label

    @admin.display(description='Consent status')
    def consent_overview(self, obj):
        from .legal import consent_summary

        if obj.pk is None:
            return '—'
        rows = []
        for row in consent_summary(obj):
            if row['decision'] is None:
                detail = 'not recorded'
            else:
                detail = (f"{row['decision']} v{row['accepted_version']} on "
                          f"{row['accepted_at']:%d %b %Y %H:%M}"
                          + (f" from {row['ip_address']}" if row['ip_address'] else ''))
                if not row['is_current'] and row['current_version']:
                    detail += f" — current version is v{row['current_version']}"
            rows.append(f'<li><b>{row["label"]}:</b> {detail}</li>')
        return format_html('<ul style="margin:0;">{}</ul>', format_html(''.join(rows)))

    @admin.action(description='Mark email address as verified')
    def mark_email_verified(self, request, queryset):
        from .verification import mark_verified

        for user in queryset:
            mark_verified(user)
        self.message_user(request, f'Marked {queryset.count()} account(s) as verified.')

    @admin.action(description='Send the verification email again')
    def resend_verification_email(self, request, queryset):
        from .verification import send_verification_email

        sent, failures = 0, []
        for user in queryset.exclude(email=''):
            _token, error = send_verification_email(request, user)
            if error:
                failures.append(f'{user.email}: {error["detail"]}')
            else:
                sent += 1
        if sent:
            self.message_user(request, f'Sent {sent} verification email(s).')
        for failure in failures[:5]:
            self.message_user(request, failure, level=messages.ERROR)


# ── Public marketing content ─────────────────────────────────────────────────

class SectorStoryInline(admin.TabularInline):
    model = SectorStory
    extra = 1
    fields = ('display_order', 'title', 'summary', 'source_label', 'published_on', 'url', 'is_published')
    ordering = ('display_order', '-published_on')


@admin.register(Sector)
class SectorAdmin(admin.ModelAdmin):
    list_display = ('name', 'display_order', 'story_count', 'shows_ticker', 'is_published')
    list_editable = ('display_order', 'shows_ticker', 'is_published')
    prepopulated_fields = {'slug': ('name',)}
    inlines = (SectorStoryInline,)

    @admin.display(description='Published stories')
    def story_count(self, obj):
        return obj.stories.filter(is_published=True).count()


@admin.register(SectorStory)
class SectorStoryAdmin(admin.ModelAdmin):
    list_display = ('title', 'sector', 'source_label', 'published_on', 'display_order', 'is_published')
    list_editable = ('display_order', 'is_published')
    list_filter = ('sector', 'is_published', 'published_on')
    search_fields = ('title', 'summary', 'source_label')
    date_hierarchy = 'published_on'


@admin.register(CommodityQuote)
class CommodityQuoteAdmin(admin.ModelAdmin):
    list_display = ('name', 'price_display', 'change_percent', 'display_order', 'is_published', 'updated_at')
    list_editable = ('price_display', 'change_percent', 'display_order', 'is_published')
    search_fields = ('name',)


@admin.register(Publication)
class PublicationAdmin(admin.ModelAdmin):
    list_display = ('title', 'kind', 'published_on', 'display_order', 'is_published')
    list_editable = ('display_order', 'is_published')
    list_filter = ('kind', 'is_published')
    search_fields = ('title', 'blurb')
    date_hierarchy = 'published_on'


# ══════════════════════════════════════════════════════════════════════════════
#  Legal documents, consent, onboarding and payments
# ══════════════════════════════════════════════════════════════════════════════

@admin.register(LegalDocument)
class LegalDocumentAdmin(admin.ModelAdmin):
    """Where the legal wording is maintained.

    Replacing draft text with approved wording is done here, not in the code.
    While a version has no acceptances against it, edit it in place. Once anyone
    has accepted it, create a *new* version instead — editing it would change
    what the existing consent records claim people agreed to.
    """
    list_display = ('doc_type', 'version', 'title', 'review_status', 'is_published',
                    'requires_acceptance', 'effective_from', 'acceptance_count')
    list_filter = ('doc_type', 'review_status', 'is_published', 'requires_acceptance', 'jurisdiction')
    search_fields = ('title', 'body', 'summary')
    readonly_fields = ('created_at', 'updated_at', 'acceptance_count')
    actions = ('publish_documents', 'unpublish_documents', 'mark_approved')
    fieldsets = (
        (None, {'fields': ('doc_type', 'version', 'title')}),
        ('Shown to the user', {
            'fields': ('summary', 'body', 'consent_label'),
            'description': ('<b>Summary</b> appears above the document as plain-language context. '
                            '<b>Body</b> is the document itself — separate paragraphs with a blank '
                            'line. <b>Consent label</b> is the exact wording beside the tick box.'),
        }),
        ('Legal review', {
            'fields': ('jurisdiction', 'review_status', 'review_note'),
            'description': ('Text shipped with the product is a <b>draft</b> and is labelled as such '
                            'on the consent page until this is set to approved. Do not mark a '
                            'version approved until a legal practitioner has actually settled it.'),
        }),
        ('Publication', {
            'fields': ('is_published', 'requires_acceptance', 'effective_from', 'acceptance_count'),
            'description': ('Publishing a later version does not affect earlier acceptances — users '
                            'are simply asked to accept the new one.'),
        }),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    @admin.display(description='Acceptances')
    def acceptance_count(self, obj):
        if obj.pk is None:
            return 0
        return obj.consents.filter(decision='accepted').count()

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.consents.exists():
            # People have already agreed to this exact text. Freeze it and make
            # them publish a new version instead.
            readonly += ['doc_type', 'version', 'body', 'consent_label']
        return readonly

    @admin.action(description='Publish')
    def publish_documents(self, request, queryset):
        queryset.update(is_published=True)
        self.message_user(request, f'Published {queryset.count()} document version(s).')

    @admin.action(description='Unpublish')
    def unpublish_documents(self, request, queryset):
        queryset.update(is_published=False)

    @admin.action(description='Mark as approved by legal counsel')
    def mark_approved(self, request, queryset):
        queryset.update(review_status='approved')
        self.message_user(
            request,
            'Marked as approved. Only do this once a legal practitioner has actually settled the '
            'wording — the consent page stops labelling it a draft.',
            level=messages.WARNING)


@admin.register(ConsentRecord)
class ConsentRecordAdmin(admin.ModelAdmin):
    """The consent audit trail. Deliberately read-only in every direction.

    These records are evidence of what a person agreed to. Nothing in this
    application may edit or delete one — a change of mind is recorded as a new
    record, which is enforced by ConsentRecord.save() as well as here.
    """
    list_display = ('occurred_at', 'user', 'doc_type', 'version', 'decision', 'source',
                    'organization', 'ip_address')
    list_filter = ('doc_type', 'decision', 'source', 'version', 'occurred_at')
    search_fields = ('user__email', 'user__first_name', 'user__last_name', 'organization__name',
                     'ip_address')
    date_hierarchy = 'occurred_at'
    readonly_fields = ('user', 'organization', 'document', 'doc_type', 'version', 'decision',
                       'source', 'occurred_at', 'ip_address', 'user_agent')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AgencyDeclaration)
class AgencyDeclarationAdmin(admin.ModelAdmin):
    list_display = ('user', 'account_type', 'organisation_name', 'position',
                    'is_authorised_representative', 'verification_status', 'created_at')
    list_filter = ('account_type', 'is_authorised_representative', 'verification_status', 'created_at')
    search_fields = ('user__email', 'organisation_name', 'position', 'registration_number')
    readonly_fields = ('authorised_at', 'authorisation_ip', 'created_at', 'updated_at')
    actions = ('mark_verified', 'mark_pending_verification')

    @admin.action(description='Mark declaration as verified')
    def mark_verified(self, request, queryset):
        queryset.update(verification_status='verified')

    @admin.action(description='Mark declaration as awaiting verification')
    def mark_pending_verification(self, request, queryset):
        queryset.update(verification_status='pending')


@admin.register(OnboardingProgress)
class OnboardingProgressAdmin(admin.ModelAdmin):
    list_display = ('user', 'state', 'is_legacy', 'payment_skipped', 'started_at', 'completed_at')
    list_filter = ('state', 'is_legacy', 'payment_skipped', 'completed_at')
    search_fields = ('user__email', 'user__first_name', 'user__last_name')
    readonly_fields = ('history', 'started_at', 'completed_at', 'updated_at')

    def has_add_permission(self, request):
        return False


@admin.register(EmailVerificationToken)
class EmailVerificationTokenAdmin(admin.ModelAdmin):
    """Visible for support ("did their link arrive, and did they use it?").

    The token value itself is neither listed nor searchable — knowing it is
    enough to verify somebody else's address.
    """
    list_display = ('email', 'user', 'created_at', 'expires_at', 'used_at', 'still_usable')
    list_filter = ('created_at', 'expires_at')
    search_fields = ('email', 'user__email')
    readonly_fields = ('user', 'email', 'created_at', 'expires_at', 'used_at')
    exclude = ('token',)

    @admin.display(description='Still usable', boolean=True)
    def still_usable(self, obj):
        return obj.is_usable

    def has_add_permission(self, request):
        return False


@admin.register(PaymentMethod)
class PaymentMethodAdmin(admin.ModelAdmin):
    """Saved instruments, shown by their display fragments only.

    ``provider_token`` is excluded from every view here. It is not a card number
    — it is useless without our provider credentials — but it is still the handle
    that can move money, and an administrator has no reason to read it.
    """
    list_display = ('organization', 'display_label', 'expiry_display', 'provider',
                    'is_default', 'is_active', 'created_at')
    list_filter = ('provider', 'is_default', 'is_active', 'brand', 'created_at')
    search_fields = ('organization__name', 'last4', 'holder_name')
    readonly_fields = ('id', 'organization', 'added_by', 'provider', 'brand', 'last4',
                       'exp_month', 'exp_year', 'holder_name', 'created_at')
    exclude = ('provider_token', 'provider_customer_id')

    def has_add_permission(self, request):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    """Payment and settlement history.

    ``settlement_available_at`` is when *our* holding period elapses — the rule
    the business set, stamped onto each payment when it was taken. It is not a
    statement about when the payment provider will release the funds, which is
    governed by the provider, the acquirer and the card scheme and is outside
    this application's control.
    """
    list_display = ('created_at', 'organization', 'amount_display', 'status', 'provider',
                    'paid_at', 'settlement_available_at', 'settlement_state')
    list_filter = ('status', 'provider', 'currency', 'created_at', 'paid_at')
    search_fields = ('organization__name', 'provider_reference', 'description')
    date_hierarchy = 'created_at'
    readonly_fields = ('id', 'created_at', 'updated_at', 'settlement_available_at',
                       'settlement_hold_days', 'settlement_state')
    actions = ('mark_settled',)
    fieldsets = (
        (None, {'fields': ('id', 'organization', 'package', 'payment_method', 'created_by')}),
        ('Amount', {'fields': ('amount', 'currency', 'description')}),
        ('Provider', {'fields': ('provider', 'provider_reference', 'status', 'failure_reason')}),
        ('Settlement', {
            'fields': ('paid_at', 'settlement_hold_days', 'settlement_available_at',
                       'settlement_state', 'provider_settlement_at', 'settled_at'),
            'description': ('<b>Settlement available at</b> = paid at + the holding period that '
                            'applied when the payment was taken. It is our business rule about our '
                            'own funds. The payment provider has its own settlement timetable, '
                            'recorded separately in <b>provider settlement at</b>, which this '
                            'system does not control.'),
        }),
        ('Timestamps', {'fields': ('created_at', 'updated_at'), 'classes': ('collapse',)}),
    )

    @admin.display(description='Amount', ordering='amount')
    def amount_display(self, obj):
        return f'{obj.currency} {obj.amount:,.2f}'

    @admin.display(description='Settlement')
    def settlement_state(self, obj):
        state = obj.settlement_status
        if state == 'holding':
            return f'holding — {obj.settlement_days_remaining} day(s) to go'
        return state

    @admin.action(description='Mark as settled (funds extracted)')
    def mark_settled(self, request, queryset):
        eligible = [p for p in queryset if p.is_settlement_eligible]
        for payment in eligible:
            payment.settled_at = timezone.now()
            payment.save(update_fields=['settled_at', 'updated_at'])
        skipped = queryset.count() - len(eligible)
        self.message_user(request, f'Marked {len(eligible)} payment(s) as settled.')
        if skipped:
            self.message_user(
                request,
                f'{skipped} payment(s) skipped — still inside the holding period, or not succeeded.',
                level=messages.WARNING)


# ── Free assessment leads ────────────────────────────────────────────────────

@admin.register(AssessmentSubmission)
class AssessmentSubmissionAdmin(admin.ModelAdmin):
    """The pipeline from the public assessment.

    Read-mostly on purpose. What the visitor answered is a record of what they
    said at a point in time and editing it would falsify the score beside it, so
    only the two fields the team actually works — status and requested action —
    stay editable.
    """
    list_display = ('created_at', 'full_name', 'company', 'source', 'score', 'fit',
                    'urgency', 'budget', 'timeline', 'requested_action', 'status',
                    'delivery')
    list_filter = ('channel', 'utm_campaign', 'fit', 'tier', 'status', 'requested_action',
                   'industry', 'country', 'created_at')
    list_editable = ('status',)
    search_fields = ('first_name', 'last_name', 'email', 'company', 'note',
                     'utm_campaign', 'utm_source', 'referrer')
    date_hierarchy = 'created_at'
    readonly_fields = ('id', 'created_at', 'first_name', 'last_name', 'email', 'company',
                       'industry', 'role', 'country', 'score', 'tier', 'fit', 'urgency',
                       'budget', 'timeline', 'platforms', 'note', 'report_sent_at',
                       'sales_notified_at', 'requested_action_at', 'answer_sheet',
                       'channel', 'utm_source', 'utm_medium', 'utm_campaign',
                       'utm_content', 'utm_term', 'click_id', 'referrer', 'landing_path')
    fieldsets = (
        ('Lead', {
            'fields': ('id', 'created_at', 'first_name', 'last_name', 'email', 'company',
                       'industry', 'role', 'country')
        }),
        ('Result', {
            'fields': ('score', 'tier', 'fit', 'urgency', 'budget', 'timeline', 'platforms',
                       'note')
        }),
        ('Their answers', {'fields': ('answer_sheet',)}),
        ('Where they came from', {
            'fields': ('channel', 'utm_source', 'utm_medium', 'utm_campaign',
                       'utm_content', 'utm_term', 'click_id', 'referrer', 'landing_path'),
            'description': ('Read off the first page of their visit and carried through '
                            'to submission. A <b>channel</b> of "direct" means they arrived '
                            'with no campaign tag and no referring site — typed the '
                            'address, followed a bookmark, or came from a link an app '
                            'stripped the referrer from.'),
        }),
        ('Follow-up', {
            'fields': ('status', 'requested_action', 'requested_action_at',
                       'report_sent_at', 'sales_notified_at'),
            'description': ('A blank <b>report sent</b> means the results email never reached '
                            'them — worth a manual follow-up before assuming they were served.')
        }),
    )

    def has_add_permission(self, request):
        return False

    @admin.display(description='Source', ordering='channel')
    def source(self, obj):
        """Origin as one cell, so the list answers "where are leads coming from"
        without opening a row."""
        if obj.channel == 'meta':
            colour = '#1877F2'
        elif obj.channel in ('', 'direct'):
            colour = '#9B9B9B'
        else:
            colour = '#17754E'
        return format_html('<span style="color:{};">{}</span>', colour, obj.source_label)

    @admin.display(description='Report')
    def delivery(self, obj):
        if obj.report_sent_at:
            return format_html('<span style="color:#17754E;">sent</span>')
        return format_html('<span style="color:#9B2C2C;">not sent</span>')

    @admin.display(description='Answers as given')
    def answer_sheet(self, obj):
        """Every question with the wording the visitor actually saw.

        The stored answers are option indexes, which are unreadable on their own
        and become misleading the moment the wording changes.
        """
        from . import assessment

        rows = format_html_join(
            '',
            '<tr><td style="padding:6px 14px 6px 0;vertical-align:top;color:#666;width:44%">'
            '{}</td><td style="padding:6px 0;vertical-align:top;">{}</td></tr>',
            ((q['text'], assessment.answer_text(obj.answers, q['key']) or '—')
             for q in assessment.QUESTIONS),
        )
        return format_html('<table style="border-collapse:collapse;">{}</table>', rows)
