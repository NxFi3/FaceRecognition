from django.utils.translation import gettext_lazy as _
from django.contrib import admin
from django import forms
from django.db import transaction
from .models import AuditEvent, Backup, Device, School
from .owner_auth import is_company_owner, locked_company_owner


class OwnerModelAdmin(admin.ModelAdmin):
    """Explicit owner can manage business models without auth-user/group powers."""
    def has_module_permission(self, request):
        return is_company_owner(request.user)
    def has_view_permission(self, request, obj=None):
        return is_company_owner(request.user)
    def has_add_permission(self, request):
        return is_company_owner(request.user)
    def has_change_permission(self, request, obj=None):
        return is_company_owner(request.user)
    def has_delete_permission(self, request, obj=None):
        return is_company_owner(request.user)


class SchoolAdminForm(forms.ModelForm):
    class Meta:
        model = School
        fields = '__all__'

    def clean_user(self):
        from .owner_models import CompanyOwner
        user = self.cleaned_data['user']
        if CompanyOwner.objects.filter(user_id=user.pk).exists():
            raise forms.ValidationError(
                _('The company owner account must remain separate from school accounts. Choose a separate school account.'),
                code='owner_cannot_be_school')
        return user

    def clean(self):
        cleaned = super().clean()
        allowance = cleaned.get('device_limit')
        if allowance is not None:
            # ModelAdmin POSTs have an outer transaction. Lock the same school row
            # as API device admission so a device cannot slip in during a reduction.
            with transaction.atomic():
                School.objects.select_for_update().filter(pk=self.instance.pk).first()
                bound = Device.objects.filter(school_id=self.instance.pk, revoked=False,
                                               token_digest__isnull=False).count()
                if bound > allowance:
                    self.add_error('device_limit', forms.ValidationError(
                        _('Explicitly revoke extra devices first. Reducing the allowance does not silently revoke offline installations.'),
                        code='active_devices_exceed_allowance'))
        return cleaned


@admin.register(School)
class SchoolAdmin(OwnerModelAdmin):
    form = SchoolAdminForm
    list_display = ("name", "status", "starts_at", "expires_at", "student_limit", "device_limit")
    list_filter = ("status",)
    search_fields = ("name", "user__username")
    readonly_fields = ("id", "created_at", "email_verified_at")
    def has_delete_permission(self, request, obj=None):
        # School deletion can cascade to installation credentials. Retire the
        # account/license explicitly; privacy erasure requires a reviewed process.
        return False
    def save_model(self, request, obj, form, change):
        locked_company_owner(request.user)
        super().save_model(request, obj, form, change)
        from .commerce import sync_face_subscription
        sync_face_subscription(obj)
        AuditEvent.objects.create(actor=request.user, school=obj, action="subscription.updated" if change else "school.created", object_id=str(obj.pk))


@admin.register(Device)
class DeviceAdmin(OwnerModelAdmin):
    list_display = ("school", "label", "revoked", "last_seen", "credential_expires_at")
    list_filter = ("revoked",)
    readonly_fields = ("school", "installation_id", "label", "credential_expires_at", "last_seen")
    exclude = ("token_digest", "password_stamp")
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        # Deleting the digest would strand a still-valid offline license: it
        # could no longer authenticate to receive its signed revocation status.
        return False
    def save_model(self, request, obj, form, change):
        with transaction.atomic():
            School.objects.select_for_update().get(pk=obj.school_id)
            Device.objects.select_for_update().filter(pk=obj.pk).first()
            locked_company_owner(request.user)
            # Only revocation is editable here. The form instance may predate a
            # completed login; never restore its old token/stamp/expiry columns.
            obj.save(update_fields=['revoked'])
            AuditEvent.objects.create(actor=request.user, school=obj.school, action="device.updated", object_id=str(obj.pk))


@admin.register(Backup)
class BackupAdmin(OwnerModelAdmin):
    list_display = ("id", "school", "size", "created_at")
    readonly_fields = ("id", "school", "blob_name", "size", "sha256", "created_at", "format")
    def has_add_permission(self, request):
        return False
    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AuditEvent)
class AuditAdmin(OwnerModelAdmin):
    list_display = ("created_at", "actor", "school", "action", "object_id")
    readonly_fields = ("created_at", "actor", "school", "action", "object_id")
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False
    def has_delete_permission(self, request, obj=None):
        return False
