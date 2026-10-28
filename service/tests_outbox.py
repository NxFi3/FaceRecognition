from datetime import timedelta
from io import StringIO
from unittest.mock import patch
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from .models import EmailOutbox, ContactRequest


class BusinessOperationsTests(TestCase):
    def test_delivery_is_bounded_and_not_resent_after_success(self):
        for i in range(2):
            EmailOutbox.objects.create(recipient='synthetic@example.com', subject='Test', body='Synthetic', dedupe_key=str(i))
        call_command('flush_commercial_emails', limit=1, stdout=StringIO())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(EmailOutbox.objects.filter(sent_at__isnull=False).count(), 1)
        call_command('flush_commercial_emails', limit=5, stdout=StringIO())
        self.assertEqual(len(mail.outbox), 2)
        call_command('flush_commercial_emails', limit=5, stdout=StringIO())
        self.assertEqual(len(mail.outbox), 2)
    def test_network_failure_retries_later_without_changing_any_entitlement(self):
        item = EmailOutbox.objects.create(recipient='synthetic@example.com', subject='Test', body='Synthetic', dedupe_key='retry')
        with patch('service.management.commands.flush_commercial_emails.send_mail', side_effect=OSError('SMTP unavailable')):
            call_command('flush_commercial_emails', stdout=StringIO())
        item.refresh_from_db()
        self.assertEqual(item.attempts, 1)
        self.assertIsNone(item.sent_at)
        self.assertGreater(item.available_at, timezone.now())
        call_command('flush_commercial_emails', stdout=StringIO())
        item.refresh_from_db()
        self.assertEqual(item.attempts, 1)
    def test_retention_is_explicit_resolved_only_and_dry_run_default(self):
        old = ContactRequest.objects.create(name='Private name', email='synthetic@example.com', kind='contact', body='Synthetic body', consent=True, handled=True)
        open_contact = ContactRequest.objects.create(name='Keep', email='synthetic@example.com', kind='contact', body='Unresolved', consent=True)
        ContactRequest.objects.update(created_at=timezone.now()-timedelta(days=400))
        call_command('purge_business_content', stdout=StringIO())
        old.refresh_from_db()
        self.assertEqual(old.name, 'Private name')
        call_command('purge_business_content', apply=True, stdout=StringIO())
        old.refresh_from_db(); open_contact.refresh_from_db()
        self.assertEqual(old.email, 'removed@example.invalid')
        self.assertEqual(open_contact.name, 'Keep')
