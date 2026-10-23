from django.contrib import admin
from .models import AuditEvent, Backup, Device, School


@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "starts_at", "expires_at", "student_limit", "device_limit")
    list_filter = ("status",)
    search_fields = ("name", "user__username")
    readonly_fields = ("id", "created_at", "email_verified_at")
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(actor=request.user, school=obj, action="subscription.updated" if change else "school.created", object_id=str(obj.pk))


@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("school", "label", "revoked", "last_seen", "credential_expires_at")
    list_filter = ("revoked",)
    readonly_fields = ("school", "installation_id", "label", "credential_expires_at", "last_seen")
    exclude = ("token_digest",)
    def has_add_permission(self, request):
        return False
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        AuditEvent.objects.create(actor=request.user, school=obj.school, action="device.updated", object_id=str(obj.pk))


@admin.register(Backup)
class BackupAdmin(admin.ModelAdmin):
    list_display = ("id", "school", "size", "created_at")
    readonly_fields = ("id", "school", "blob_name", "size", "sha256", "created_at")
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "school", "action", "object_id")
    readonly_fields = ("created_at", "actor", "school", "action", "object_id")
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
