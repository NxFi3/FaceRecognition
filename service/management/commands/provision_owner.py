"""Explicit, interactive owner provisioning. No passwords in flags or environment."""
from getpass import getpass

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction
from django.utils.crypto import constant_time_compare

from service.models import AuditEvent, School
from service.owner_models import CompanyOwner


class Command(BaseCommand):
    help = 'Provision the SINGLE company owner with an interactive password prompt; never infer ownership from staff/superuser.'

    def add_arguments(self, parser):
        parser.add_argument('--email', required=True, help='New or existing non-school owner account email')
        parser.add_argument('--replace', action='store_true', help='Explicitly replace owner; current owner password is additionally required')

    def handle(self, *args, **options):
        email = options['email'].strip().casefold()
        try:
            validate_email(email)
        except ValidationError as exc:
            raise CommandError('A valid email is required.') from exc
        if len(email) > 150:
            raise CommandError('Email must fit the user-name field (150 characters).')
        current = CompanyOwner.objects.select_related('user').filter(pk=1).first()
        revision = current.revision if current else None
        if current and not options['replace']:
            raise CommandError('An explicit owner already exists. Use --replace and authenticate the current owner.')
        if current:
            password = getpass('Current owner password (not displayed): ')
            if not current.user.check_password(password):
                raise CommandError('Current owner authentication failed.')
        current_stamp = current.user.get_session_auth_hash() if current else None
        User = get_user_model()
        target = User.objects.filter(username=email).first()
        if target and (not target.is_active or School.objects.filter(user=target).exists()):
            raise CommandError('An inactive or school account cannot become company owner.')
        entered = getpass('Existing account password: ' if target else 'New owner password (not displayed): ')
        if target:
            if not target.check_password(entered):
                raise CommandError('Existing target account authentication failed.')
        else:
            if entered != getpass('Repeat new owner password: '):
                raise CommandError('Passwords did not match.')
            target = User(username=email, email=email, is_active=True, is_staff=True, is_superuser=False)
            try:
                validate_password(entered, target)
            except ValidationError as exc:
                raise CommandError('; '.join(exc.messages)) from exc
            target.set_password(entered)
        try:
            with transaction.atomic():
                locked = CompanyOwner.objects.select_for_update().select_related('user').filter(pk=1).first()
                if (locked.revision if locked else None) != revision:
                    raise CommandError('Owner changed concurrently; retry with current credentials.')
                if locked and not constant_time_compare(locked.user.get_session_auth_hash(), current_stamp):
                    raise CommandError('Current owner credentials changed concurrently; retry.')
                if target.pk:
                    target = User.objects.select_for_update().get(pk=target.pk)
                    if not target.is_active or School.objects.filter(user=target).exists() or not target.check_password(entered):
                        raise CommandError('Target account changed concurrently; retry.')
                    target.is_staff = True  # Admin UI access, not company authority by itself.
                    target.save(update_fields=['is_staff'])
                else:
                    target.save()
                if locked:
                    locked.user = target
                    locked.revision += 1
                    locked.save(update_fields=['user', 'revision', 'changed_at'])
                else:
                    CompanyOwner.objects.create(id=1, user=target)
                AuditEvent.objects.create(actor=target, action='owner.replaced' if current else 'owner.provisioned', object_id=str(target.pk))
        except IntegrityError as exc:
            raise CommandError('Concurrent provisioning/account conflict; no owner change committed.') from exc
        self.stdout.write(self.style.SUCCESS('Explicit company owner provisioned. No password was printed or saved outside Django password hashing.'))
