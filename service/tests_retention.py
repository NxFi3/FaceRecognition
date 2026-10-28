"""Production database regression for retention racing with a customer's fresh reply."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import StringIO
import threading
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection, connections
from django.test import Client, TestCase, TransactionTestCase
from django.utils import timezone
from service.models import School, SupportTicket, TicketReply, Product


@skipUnless(connection.vendor == 'postgresql', 'Retention serialization requires production PostgreSQL row locks')
class RetentionReopenConcurrencyTests(TransactionTestCase):
    def setUp(self):
        Product.objects.get_or_create(slug='facerecognition', defaults={'name': 'FaceRecognition', 'summary': 'Synthetic fixture', 'is_published': True})
        cache.clear()
        self.user = get_user_model().objects.create_user('retention-race@example.invalid')
        self.school = School.objects.create(user=self.user, name='Synthetic retention race')
        self.ticket = SupportTicket.objects.create(school=self.school, subject='Old synthetic request',
            body='Old synthetic content', status='closed')
        SupportTicket.objects.filter(pk=self.ticket.pk).update(updated_at=timezone.now()-timedelta(days=500))
        self.old_reply = TicketReply.objects.create(ticket=self.ticket, author=self.user, body='Old synthetic reply')

    def test_fresh_customer_reply_survives_concurrent_retention_and_reopens_ticket(self):
        purge_ready = threading.Event()
        reopen_attempted = threading.Event()
        permit_purge = threading.Event()
        manager = TicketReply.objects
        original_filter = manager.filter
        def before_reply_scrub(*args, **kwargs):
            if 'ticket_id__in' in kwargs:
                purge_ready.set()
                if not permit_purge.wait(5):
                    raise AssertionError('Timed out scheduling retention/reopen interleaving')
            return original_filter(*args, **kwargs)
        def purge():
            try:
                call_command('purge_business_content', days=365, apply=True, stdout=StringIO())
            finally:
                connections.close_all()
        def reopen():
            try:
                client = Client()
                client.force_login(get_user_model().objects.get(pk=self.user.pk))
                def observe_parent_lock(execute, sql, params, many, context):
                    if 'service_supportticket' in sql and 'FOR UPDATE' in sql.upper():
                        reopen_attempted.set()
                    return execute(sql, params, many, context)
                with connection.execute_wrapper(observe_parent_lock):
                    response = client.post(f'/account/tickets/{self.ticket.pk}/',
                        {'body': 'Fresh synthetic reply must survive'}, secure=True)
                return response.status_code
            finally:
                connections.close_all()
        with patch.object(manager, 'filter', side_effect=before_reply_scrub), ThreadPoolExecutor(max_workers=2) as executor:
            purge_job = executor.submit(purge)
            try:
                self.assertTrue(purge_ready.wait(5), 'Retention never reached the scrub boundary')
                reopen_job = executor.submit(reopen)
                self.assertTrue(reopen_attempted.wait(5), 'Customer reply did not serialize on its parent ticket')
            finally:
                permit_purge.set()
            purge_job.result(timeout=5)
            self.assertEqual(reopen_job.result(timeout=5), 302)
        self.ticket.refresh_from_db()
        self.old_reply.refresh_from_db()
        self.assertEqual(self.ticket.status, 'open')
        self.assertEqual(self.old_reply.body, '[removed by retention policy]')
        self.assertTrue(TicketReply.objects.filter(ticket=self.ticket,
            body='Fresh synthetic reply must survive').exists())


class RetentionLifecycleTests(TestCase):
    def test_new_replies_on_previously_scrubbed_ticket_are_purged_in_later_cycle(self):
        user = get_user_model().objects.create_user('retention-lifecycle@example.invalid')
        school = School.objects.create(user=user, name='Synthetic retention lifecycle')
        ticket = SupportTicket.objects.create(school=school, subject='Old synthetic request',
            body='Old synthetic content', status='closed')
        old = timezone.now()-timedelta(days=500)
        SupportTicket.objects.filter(pk=ticket.pk).update(updated_at=old)
        TicketReply.objects.create(ticket=ticket, author=user, body='Old synthetic reply')
        call_command('purge_business_content', days=365, apply=True, stdout=StringIO())
        ticket.refresh_from_db()
        self.assertEqual(ticket.body, '[removed by retention policy]')
        # A customer can legitimately reopen an old ticket and add fresh content.
        client = Client()
        client.force_login(user)
        response = client.post(f'/account/tickets/{ticket.pk}/',
            {'body': 'Fresh content for the next retention cycle'}, secure=True)
        self.assertEqual(response.status_code, 302)
        fresh = TicketReply.objects.get(ticket=ticket, body='Fresh content for the next retention cycle')
        SupportTicket.objects.filter(pk=ticket.pk).update(status='closed', updated_at=old)
        call_command('purge_business_content', days=365, apply=True, stdout=StringIO())
        fresh.refresh_from_db()
        self.assertEqual(fresh.body, '[removed by retention policy]')
