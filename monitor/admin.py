from django.contrib import admin
from django.utils import timezone

from .models import (CommodityQuote, Event, Organization, Package, Publication,
                     Sector, SectorStory, SubscriptionRequest, User)

@admin.register(Organization)
class OrganizationAdmin(admin.ModelAdmin):
    list_display = ('name', 'industry', 'country', 'status', 'plan_status', 'package',
                    'trial_ends_at', 'created_at')
    list_filter = ('plan_status', 'status', 'industry', 'country', 'created_at')
    search_fields = ('name', 'email', 'country')
    readonly_fields = ('id', 'created_at', 'subscription_activated_at')
    actions = ('start_free_trial', 'extend_trial_14_days')
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


@admin.register(Package)
class PackageAdmin(admin.ModelAdmin):
    list_display = ('name', 'price_display', 'billing_period', 'is_featured', 'is_active', 'sort_order')
    list_editable = ('is_featured', 'is_active', 'sort_order')
    list_filter = ('is_active', 'is_featured', 'billing_period')
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
        ('Presentation', {'fields': ('accent_color', 'is_dark', 'cta_label', 'contact_only')}),
        ('Visibility', {'fields': ('is_featured', 'is_active')}),
    )


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


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('email', 'first_name', 'last_name', 'role', 'is_active', 'date_joined')
    list_filter = ('role', 'is_active', 'date_joined')
    search_fields = ('email', 'first_name', 'last_name')
    readonly_fields = ('id', 'date_joined', 'last_login')
    fieldsets = (
        ('Account', {
            'fields': ('id', 'email', 'password')
        }),
        ('Personal Info', {
            'fields': ('first_name', 'last_name')
        }),
        ('Permissions', {
            'fields': ('role', 'is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')
        }),
        ('Timestamps', {
            'fields': ('date_joined', 'last_login'),
            'classes': ('collapse',)
        }),
    )


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
