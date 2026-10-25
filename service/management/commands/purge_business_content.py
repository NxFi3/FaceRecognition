"""Dry-run by default. Keep financial/audit records; minimize old resolved messages."""
from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from service.models import ContactRequest, SupportTicket, TicketReply, AuditEvent, EmailOutbox


class Command(BaseCommand):
    help = 'Anonymize resolved contact/ticket message content by operator policy. No school/student/payment records are deleted.'
    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=365)
        parser.add_argument('--apply', action='store_true')
    def handle(self, *args, **options):
        if not 30 <= options['days'] <= 3650:
            raise CommandError('Use an approved retention period between 30 and 3650 days')
        cutoff = timezone.now()-timedelta(days=options['days'])
        with transaction.atomic():
            contacts = ContactRequest.objects.select_for_update().filter(handled=True, created_at__lt=cutoff).exclude(body='[removed by retention policy]')
            tickets = SupportTicket.objects.select_for_update().filter(status='closed', updated_at__lt=cutoff)
            emails = EmailOutbox.objects.filter(sent_at__lt=cutoff)
            contact_ids = list(contacts.values_list('pk', flat=True))
            ticket_ids = list(tickets.values_list('pk', flat=True))
            counts = (len(contact_ids), len(ticket_ids), emails.count())
            if options['apply']:
                identifiers = ticket_ids
                emails.delete()
                ContactRequest.objects.filter(pk__in=contact_ids).update(name='Removed', email='removed@example.invalid', school_name='', body='[removed by retention policy]')
                TicketReply.objects.filter(ticket_id__in=identifiers).update(body='[removed by retention policy]')
                SupportTicket.objects.filter(pk__in=ticket_ids).update(subject='Removed by retention policy', body='[removed by retention policy]')
                AuditEvent.objects.create(action='privacy.retention.applied', object_id=f'contacts={counts[0]},tickets={counts[1]},emails={counts[2]}')
        self.stdout.write(f'{"Applied" if options["apply"] else "DRY RUN"}: contacts={counts[0]}, closed tickets={counts[1]}, sent emails={counts[2]}. Financial/audit records retained.')
