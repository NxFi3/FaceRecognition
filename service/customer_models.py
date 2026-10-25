"""Company identity metadata; product-specific authority remains in School."""
from django.conf import settings
from django.db import models


class CustomerProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='nxfi_profile')
    display_name = models.CharField(max_length=160, blank=True)
    preferred_language = models.CharField(max_length=2, choices=[('en', 'English'), ('fa', 'فارسی')], default='en')
    email_verified_at = models.DateTimeField(null=True, blank=True)
