from django.contrib import admin
from .models import Organisation, User

@admin.register(Organisation)
class OrganisationAdmin(admin.ModelAdmin):
    list_display = ('name', 'industry', 'country', 'status', 'created_at')
    list_filter = ('status', 'industry', 'country', 'created_at')
    search_fields = ('name', 'email', 'country')
    readonly_fields = ('id', 'created_at', 'updated_at')
    fieldsets = (
        ('Organization Info', {
            'fields': ('id', 'name', 'address', 'country', 'email', 'phone')
        }),
        ('Details', {
            'fields': ('industry', 'status', 'facebook_url', 'twitter_url', 'instagram_url')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

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
