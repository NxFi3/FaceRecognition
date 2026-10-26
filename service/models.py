from django.utils.translation import gettext_lazy as _
import uuid
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class School(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", _('Active')
        SUSPENDED = "suspended", _('Suspended')
        BANNED = "banned", _('Banned')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, verbose_name=_("Account"))
    name = models.CharField(_('School name'), max_length=160)
    status = models.CharField(_('Subscription status'), max_length=16, choices=Status.choices, default=Status.SUSPENDED)
    starts_at = models.DateTimeField(_('Subscription starts (UTC)'), null=True, blank=True)
    expires_at = models.DateTimeField(_('Subscription expires (UTC)'), null=True, blank=True)
    student_limit = models.PositiveIntegerField(_('Student record capacity'), default=0, validators=[MaxValueValidator(1000000)], help_text=_('Active records count toward capacity. Archiving does not delete data. Zero means no licensed access.'))
    device_limit = models.PositiveSmallIntegerField(_('Authorized devices'), default=1, validators=[MinValueValidator(1), MaxValueValidator(100)])
    backup_quota_bytes = models.PositiveBigIntegerField(_("Backup quota (bytes)"), default=1024 ** 3)
    backup_retention = models.PositiveSmallIntegerField(_('Backups retained'), default=7, validators=[MinValueValidator(1), MaxValueValidator(100)])
    backups_enabled = models.BooleanField(_('Online backups enabled'), default=True)
    email_verified_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_at = models.DateTimeField(_("Created (UTC)"), auto_now_add=True)

    def clean(self):
        if self.starts_at and self.expires_at and self.expires_at <= self.starts_at:
            raise ValidationError(_('Subscription expiry must be after its start.'))
        if self.device_limit < 1 or not 1 <= self.backup_retention <= 100:
            raise ValidationError(_('Device allowance must be at least one, and backup retention must be between 1 and 100.'))

    @property
    def entitled(self):
        now = timezone.now()
        return bool(self.user.is_active and self.status == self.Status.ACTIVE and self.starts_at
                    and self.expires_at and self.starts_at <= now < self.expires_at and self.student_limit > 0)

    class Meta:
        verbose_name = _('School')
        verbose_name_plural = _('Schools')

    def __str__(self):
        return self.name


class Device(models.Model):
    school = models.ForeignKey(School, on_delete=models.CASCADE, verbose_name=_("School"))
    installation_id = models.UUIDField(_("Installation ID"))
    label = models.CharField(_("Device label"), max_length=80)
    token_digest = models.CharField(max_length=64, unique=True, null=True, editable=False)
    password_stamp = models.CharField(max_length=64, editable=False)
    credential_expires_at = models.DateTimeField(_("Credential expires (UTC)"))
    revoked = models.BooleanField(_("Revoked"), default=False)
    last_seen = models.DateTimeField(_("Last seen (UTC)"), default=timezone.now)
    class Meta:
        verbose_name = _("Device")
        verbose_name_plural = _("Devices")
        constraints = [models.UniqueConstraint(fields=["school", "installation_id"], name="school_installation_unique")]


class Backup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey(School, on_delete=models.PROTECT, verbose_name=_("School"))
    blob_name = models.CharField(_("Encrypted blob name"), max_length=64, unique=True, editable=False)
    size = models.PositiveBigIntegerField(_("Size (bytes)"))
    sha256 = models.CharField("SHA-256", max_length=64)
    format = models.CharField(_("Backup format"), max_length=20, default="legacy-sqlite", choices=[("legacy-sqlite", _("Legacy SQLite")), ("client-v2", _("Client-encrypted complete recovery"))])
    created_at = models.DateTimeField(_("Created (UTC)"), auto_now_add=True)
    class Meta:
        verbose_name = _("Backup")
        verbose_name_plural = _("Backups")
        ordering = ["-created_at"]


class AuditEvent(models.Model):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, verbose_name=_("Actor"))
    school = models.ForeignKey(School, null=True, on_delete=models.SET_NULL, verbose_name=_("School"))
    action = models.CharField(_("Action"), max_length=80)
    object_id = models.CharField(_("Record ID"), max_length=80, blank=True)
    created_at = models.DateTimeField(_("Created (UTC)"), auto_now_add=True)
    class Meta:
        verbose_name = _("Audit event")
        verbose_name_plural = _("Audit events")
        ordering = ["-created_at"]

# Separate business/security modules remain registered in the existing service app.
from .commerce_models import Product, ProductSubscription, Plan, Order, VerifiedPayment  # noqa: E402,F401
from .owner_models import CompanyOwner  # noqa: E402,F401
from .portal_models import ContactRequest, SupportTicket, TicketReply, Release, EmailOutbox  # noqa: E402,F401
from .customer_models import CustomerProfile  # noqa: E402,F401
