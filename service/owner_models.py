"""Explicit, singleton company authority. Neither staff nor superuser implies ownership."""
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class CompanyOwner(models.Model):
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='company_authority')
    revision = models.PositiveIntegerField(default=1, editable=False)
    changed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(id=1), name='single_company_owner')]
        verbose_name = 'مالک صریح شرکت'

    def clean(self):
        if self.pk != 1:
            raise ValidationError('Only the singleton company authority is permitted.')
        if self.user_id and (not self.user.is_active or hasattr(self.user, 'school')):
            raise ValidationError('Owner must be active and separate from school accounts.')
