"""Business records only. Student records/templates never belong in these tables."""
from django.utils.translation import gettext_lazy as _
import uuid
from django.conf import settings
from django.core.validators import RegexValidator, MinValueValidator
from django.db import models
from django.utils import timezone


class ContactRequest(models.Model):
    name = models.CharField(max_length=120)
    email = models.EmailField()
    school_name = models.CharField(max_length=160, blank=True)
    kind = models.CharField(max_length=16, choices=[('demo', _('Demonstration request')), ('contact', _('Contact'))])
    body = models.TextField(max_length=4000)
    consent = models.BooleanField(default=False)
    handled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class SupportTicket(models.Model):
    product = models.ForeignKey('service.Product', on_delete=models.PROTECT, default='facerecognition')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    school = models.ForeignKey('service.School', on_delete=models.PROTECT)
    subject = models.CharField(max_length=160)
    category = models.CharField(max_length=20, choices=[('support', _('Support')), ('renewal', _('Renewal')), ('upgrade', _('Upgrade')), ('cancellation', _('Cancellation request')), ('privacy', _('Export or delete business information'))])
    body = models.TextField(max_length=6000)
    status = models.CharField(max_length=12, choices=[('open', _('Open')), ('closed', _('Closed'))], default='open')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ['-created_at']


class TicketReply(models.Model):
    ticket = models.ForeignKey(SupportTicket, on_delete=models.PROTECT, related_name='replies')
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    body = models.TextField(max_length=6000)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['created_at']


class Release(models.Model):
    product = models.ForeignKey('service.Product', on_delete=models.PROTECT, default='facerecognition')
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version = models.CharField(max_length=80)
    platform = models.CharField(max_length=24, default='windows-x64', choices=[('windows-x64', 'Windows x64')])
    artifact_name = models.CharField(max_length=160, validators=[RegexValidator(r'\A[A-Za-z0-9][A-Za-z0-9._-]*\.(zip|exe)\Z', _('Artifact names may contain only Latin letters, digits, ._- and a zip/exe extension.'))])
    sha256 = models.CharField(max_length=64, validators=[RegexValidator(r'\A[0-9a-f]{64}\Z')])
    size_bytes = models.PositiveBigIntegerField(validators=[MinValueValidator(1)])
    notes = models.TextField(max_length=6000)
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ['-created_at']


class EmailOutbox(models.Model):
    recipient = models.EmailField()
    subject = models.CharField(max_length=200)
    body = models.TextField()
    dedupe_key = models.CharField(max_length=200, unique=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
