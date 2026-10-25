"""Bounded durable outbox; SMTP runs outside all payment and school locks."""
from datetime import timedelta
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.core.mail import send_mail
from django.db import transaction, connection
from django.utils import timezone
from service.models import EmailOutbox


class Command(BaseCommand):
    help = 'Send queued business notifications; failed messages retry with backoff (10 attempts max).'
    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20)
    def handle(self, *args, **options):
        limit = options['limit']
        if not 1 <= limit <= 1000:
            raise CommandError('limit must be 1..1000')
        sent = failed = 0
        for _ in range(limit):
            with transaction.atomic():
                queryset = EmailOutbox.objects.filter(sent_at__isnull=True, attempts__lt=10, available_at__lte=timezone.now()).order_by('created_at')
                queryset = queryset.select_for_update(skip_locked=True) if connection.features.has_select_for_update_skip_locked else queryset.select_for_update()
                item = queryset.first()
                if item is None:
                    break
                item.attempts += 1
                attempt = item.attempts
                item.available_at = timezone.now()+timedelta(seconds=max(600, min(86400, 60*2**attempt)))
                item.save(update_fields=['attempts', 'available_at'])
            try:
                delivered = send_mail(item.subject, item.body, settings.DEFAULT_FROM_EMAIL, [item.recipient], fail_silently=False)
                if delivered != 1:
                    raise OSError('Mail backend did not accept message')
            except Exception:
                failed += 1  # Do not log recipients, bodies or SMTP exception secrets.
            else:
                EmailOutbox.objects.filter(pk=item.pk, attempts=attempt, sent_at__isnull=True).update(sent_at=timezone.now())
                sent += 1
        self.stdout.write(f'Accepted by mail backend: {sent}; deferred: {failed}. Check the outbox for exhausted attempts.')
