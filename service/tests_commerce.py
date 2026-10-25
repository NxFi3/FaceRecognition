"""Transactional commerce security: no browser/claim driven entitlement."""
import base64
from datetime import timedelta
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .commerce import (CommerceError, approve_trial, cancel_order, create_order,
                       submit_manual_payment, verify_manual_payment, reject_manual_payment)
from .commerce_models import Plan, Order, VerifiedPayment
from .models import School, EmailOutbox, Device
from .owner_models import CompanyOwner


class Fixtures:
    def make_fixtures(self):
        users = get_user_model()
        self.owner = users.objects.create_user('owner@example.com', password='Owner-test-password!445')
        CompanyOwner.objects.create(id=1, user=self.owner)
        self.user = users.objects.create_user('school@example.com', email='school@example.com', password='School-test-password!445')
        self.school = School.objects.create(user=self.user, name='Synthetic school', email_verified_at=timezone.now())
        self.plan = Plan.objects.create(slug='annual', name='Annual', price_irr=90000000,
            duration_days=365, student_limit=900, device_limit=1, is_active=True, is_public=True)
    def order(self, **kwargs):
        return create_order(self.school, kwargs.get('plan', self.plan).pk, kwargs.get('key', uuid.uuid4()))
    def pay(self, order, reference='BANK-123456'):
        return verify_manual_payment(order.pk, self.owner, reference, order.amount_irr)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CommerceTests(Fixtures, TestCase):
    def setUp(self):
        self.make_fixtures()

    def test_server_price_snapshot_and_plan_edit_do_not_reprice_order(self):
        order = self.order()
        self.plan.price_irr = 2 * self.plan.price_irr
        self.plan.save()
        self.assertEqual(Order.objects.get(pk=order.pk).amount_irr, 90000000)
        order.amount_irr = 1
        with self.assertRaises(ValidationError):
            order.save()
        self.school.refresh_from_db()
        self.assertFalse(self.school.entitled)

    def test_duplicate_checkout_is_idempotent_and_cross_plan_replay_rejected(self):
        key = uuid.uuid4()
        order = self.order(key=key)
        self.assertEqual(self.order(key=key).pk, order.pk)
        self.assertEqual(self.order().pk, order.pk)  # A second tab reuses one pending quote.
        other = Plan.objects.create(slug='other', name='Other', price_irr=1,
            duration_days=365, student_limit=900, device_limit=1, is_active=True, is_public=True)
        with self.assertRaises(CommerceError):
            self.order(plan=other, key=key)
        self.assertEqual(Order.objects.count(), 1)

    def test_hidden_inactive_or_zero_price_paid_plan_cannot_be_purchased(self):
        self.plan.is_public = False
        self.plan.save()
        with self.assertRaises(CommerceError):
            self.order()
        self.plan.is_active, self.plan.is_public = False, True
        self.plan.save()
        with self.assertRaises(CommerceError):
            self.order()
        self.plan.price_irr, self.plan.is_active = 0, True
        with self.assertRaises(ValidationError):
            self.plan.full_clean()

    def test_manual_customer_claim_never_activates_subscription(self):
        order = self.order()
        submit_manual_payment(self.school, order.pk, 'BANK-123456')
        self.school.refresh_from_db()
        order.refresh_from_db()
        self.assertFalse(self.school.entitled)
        self.assertEqual(order.status, Order.Status.PAYMENT_SUBMITTED)
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_school_staff_and_superuser_cannot_verify_payments(self):
        order = self.order()
        staff = get_user_model().objects.create_user('staff@example.com', is_staff=True, is_superuser=True)
        for actor in (self.user, staff):
            with self.subTest(actor=actor.pk), self.assertRaises(PermissionDenied):
                verify_manual_payment(order.pk, actor, 'BANK-123456', order.amount_irr)
        self.assertFalse(VerifiedPayment.objects.exists())
        self.school.refresh_from_db()
        self.assertFalse(self.school.entitled)

    def test_verified_manual_payment_fulfills_exactly_once_and_queues_email(self):
        order = self.order()
        paid = self.pay(order)
        self.school.refresh_from_db()
        expiry = self.school.expires_at
        self.assertTrue(self.school.entitled)
        self.assertEqual(self.school.student_limit, 900)
        self.assertEqual(self.school.device_limit, 1)
        self.pay(order)
        self.school.refresh_from_db()
        self.assertEqual(self.school.expires_at, expiry)
        self.assertEqual(VerifiedPayment.objects.count(), 1)
        self.assertEqual(EmailOutbox.objects.filter(dedupe_key=f'order:{order.pk}:fulfilled').count(), 1)
        self.assertEqual(paid.status, Order.Status.FULFILLED)
        with self.assertRaises(CommerceError):
            verify_manual_payment(order.pk, self.owner, 'BANK-DIFFERENT', order.amount_irr)

    def test_wrong_amount_or_reused_payment_reference_never_grants_entitlement(self):
        order = self.order()
        for amount in (1, 0, -1, True, str(order.amount_irr)):
            with self.subTest(amount=amount), self.assertRaises(CommerceError):
                verify_manual_payment(order.pk, self.owner, 'BANK-123456', amount)
        self.assertFalse(VerifiedPayment.objects.exists())
        self.pay(order)
        another = self.order()
        with self.assertRaises(CommerceError):
            self.pay(another)
        self.assertEqual(VerifiedPayment.objects.count(), 1)
        another.refresh_from_db()
        self.assertNotEqual(another.status, Order.Status.FULFILLED)

    def test_repeated_renewals_append_full_period_without_losing_paid_time(self):
        first = self.order()
        self.pay(first)
        self.school.refresh_from_db()
        expiry = self.school.expires_at
        for index in range(2, 5):
            renewal = self.order()
            self.assertEqual(renewal.purpose, Order.Purpose.RENEWAL)
            self.pay(renewal, f'BANK-RENEW{index}')
            self.school.refresh_from_db()
            self.assertEqual(self.school.expires_at, expiry+timedelta(days=365*(index-1)))
        self.assertEqual(VerifiedPayment.objects.count(), 4)

    def test_expired_subscription_can_be_renewed_and_banned_school_cannot(self):
        self.school.expires_at = timezone.now()-timedelta(days=100)
        self.school.starts_at = self.school.expires_at-timedelta(days=365)
        self.school.save()
        paid = self.pay(self.order())
        self.school.refresh_from_db()
        self.assertTrue(self.school.entitled)
        self.assertLess(abs((self.school.expires_at-timezone.now()).total_seconds()-365*86400), 2)
        order = self.order()
        self.school.status = School.Status.BANNED
        self.school.save()
        with self.assertRaises(CommerceError):
            self.pay(order, 'BANK-BANNED1')
        self.assertEqual(VerifiedPayment.objects.count(), 1)

    def test_capacity_downgrade_during_paid_period_requires_support(self):
        self.pay(self.order())
        self.school.refresh_from_db()
        lower = Plan.objects.create(slug='smaller', name='Smaller', price_irr=100,
            duration_days=365, student_limit=100, device_limit=1, is_active=True, is_public=True)
        with self.assertRaises(CommerceError):
            self.order(plan=lower)

    def test_school_cannot_submit_or_cancel_other_school_order(self):
        order = self.order()
        outsider = get_user_model().objects.create_user('outside@example.com')
        another = School.objects.create(user=outsider, name='Other')
        with self.assertRaises(PermissionDenied):
            submit_manual_payment(another, order.pk, 'BANK-123456')
        with self.assertRaises(PermissionDenied):
            cancel_order(another, order.pk)

    def test_cancelled_or_expired_quote_cannot_be_verified(self):
        cancelled = self.order()
        cancel_order(self.school, cancelled.pk)
        with self.assertRaises(CommerceError):
            self.pay(cancelled)
        current = self.order()
        Order.objects.filter(pk=current.pk).update(expires_at=timezone.now()-timedelta(seconds=1))
        with self.assertRaises(CommerceError):
            self.pay(current)
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_trial_requires_owner_approval_once_and_is_not_revenue(self):
        trial = Plan.objects.create(slug='trial', name='Trial', price_irr=0,
            duration_days=14, student_limit=100, device_limit=1, is_trial=True, is_active=True, is_public=True)
        order = self.order(plan=trial)
        self.school.refresh_from_db()
        self.assertFalse(self.school.entitled)
        with self.assertRaises(PermissionDenied):
            approve_trial(order.pk, self.user)
        approve_trial(order.pk, self.owner)
        self.school.refresh_from_db()
        expiry = self.school.expires_at
        approve_trial(order.pk, self.owner)
        self.school.refresh_from_db()
        self.assertEqual(self.school.expires_at, expiry)
        self.assertTrue(self.school.entitled)
        self.assertFalse(VerifiedPayment.objects.exists())
        with self.assertRaises(CommerceError):
            self.order(plan=trial)

    def test_plan_draft_seed_is_inactive_and_never_overwrites_owner_prices(self):
        call_command('seed_plans', verbosity=0)
        plan = Plan.objects.get(slug='school-annual')
        self.assertFalse(plan.is_active)
        self.assertFalse(plan.is_public)
        plan.price_irr, plan.is_active = 1234560, True
        plan.save()
        call_command('seed_plans', verbosity=0)
        plan.refresh_from_db()
        self.assertEqual(plan.price_irr, 1234560)
        self.assertTrue(plan.is_active)

    def test_fulfilled_purchase_entitlement_is_in_next_signed_license(self):
        from .api import signed_lease
        signer = Ed25519PrivateKey.generate()
        private = signer.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
        self.pay(self.order())
        self.school.refresh_from_db()
        with override_settings(SIGNING_PRIVATE_KEY=base64.b64encode(private).decode()):
            envelope = signed_lease(self.school, uuid.uuid4(), 'n'*43)
        serialized = base64.b64decode(envelope['payload'])
        signer.public_key().verify(base64.b64decode(envelope['signature']), serialized)
        payload = json.loads(serialized)
        self.assertEqual(payload['status'], 'active')
        self.assertEqual(payload['student_limit'], 900)
        self.assertEqual(payload['subscription_expires_at'], self.school.expires_at.timestamp())

    def test_payment_and_entitlement_roll_back_together_if_outbox_write_fails(self):
        order = self.order()
        with patch.object(EmailOutbox.objects, 'get_or_create', side_effect=RuntimeError('storage unavailable')):
            with self.assertRaises(RuntimeError):
                self.pay(order)
        self.school.refresh_from_db()
        order.refresh_from_db()
        self.assertFalse(self.school.entitled)
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)
        self.assertFalse(VerifiedPayment.objects.exists())

    def test_renewal_preserves_a_future_contract_start(self):
        start = timezone.now()+timedelta(days=10)
        self.school.starts_at = start
        self.school.expires_at = start+timedelta(days=365)
        self.school.status = School.Status.ACTIVE
        self.school.save()
        self.pay(self.order())
        self.school.refresh_from_db()
        self.assertEqual(self.school.starts_at, start)
        self.assertFalse(self.school.entitled)

    def test_device_reduction_cannot_leave_more_bound_devices_than_new_plan(self):
        order = self.order()
        for index in range(2):
            Device.objects.create(school=self.school, installation_id=uuid.uuid4(), label=f'Device {index}',
                token_digest=str(index)*64, password_stamp='x'*64,
                credential_expires_at=timezone.now()+timedelta(days=30))
        with self.assertRaises(CommerceError):
            self.pay(order)
        self.assertFalse(VerifiedPayment.objects.exists())
        self.school.refresh_from_db()
        self.assertFalse(self.school.entitled)

    def test_rejection_does_not_activate_or_record_a_payment_and_allows_new_order(self):
        order = self.order()
        submit_manual_payment(self.school, order.pk, 'BANK-CLAIM01')
        rejected = reject_manual_payment(order.pk, self.owner, 'تراکنش در حساب مقصد تأیید نشد.')
        self.assertEqual(rejected.status, Order.Status.REJECTED)
        self.assertTrue(rejected.rejection_reason)
        self.school.refresh_from_db()
        self.assertFalse(self.school.entitled)
        self.assertFalse(VerifiedPayment.objects.exists())
        self.assertEqual(EmailOutbox.objects.filter(dedupe_key=f'order:{order.pk}:rejected').count(), 1)
        self.assertNotEqual(self.order().pk, order.pk)

    def test_rejected_order_cannot_later_be_verified(self):
        order = self.order()
        reject_manual_payment(order.pk, self.owner, 'رسید معتبر نیست.')
        with self.assertRaises(CommerceError):
            self.pay(order)
        self.assertFalse(VerifiedPayment.objects.exists())
        self.school.refresh_from_db()
        self.assertFalse(self.school.entitled)

    def test_fulfilled_order_cannot_be_rejected_and_existing_entitlement_unchanged(self):
        first = self.order()
        self.pay(first)
        self.school.refresh_from_db()
        expiry = self.school.expires_at
        with self.assertRaises(CommerceError):
            reject_manual_payment(first.pk, self.owner, 'رسید نامعتبر است.')
        second = self.order()
        reject_manual_payment(second.pk, self.owner, 'تراکنش دوم تأیید نشد.')
        self.school.refresh_from_db()
        self.assertTrue(self.school.entitled)
        self.assertEqual(self.school.expires_at, expiry)
        self.assertEqual(VerifiedPayment.objects.count(), 1)

    def test_rejection_is_owner_only_and_repeated_rejection_idempotent(self):
        from .models import AuditEvent
        order = self.order()
        with self.assertRaises(PermissionDenied):
            reject_manual_payment(order.pk, self.user, 'رسید معتبر نیست.')
        for _ in range(2):
            reject_manual_payment(order.pk, self.owner, 'رسید معتبر نیست.')
        self.assertEqual(AuditEvent.objects.filter(action='order.payment_rejected', object_id=str(order.pk)).count(), 1)
        self.assertEqual(EmailOutbox.objects.filter(dedupe_key=f'order:{order.pk}:rejected').count(), 1)
        with self.assertRaises(CommerceError):
            reject_manual_payment(order.pk, self.owner, 'دلیل دیگری ثبت شود.')
        order.refresh_from_db()
        order.rejection_reason = 'تغییر مستقیم دلیل'
        with self.assertRaises(ValidationError):
            order.save(update_fields=['rejection_reason'])

    def test_empty_or_oversized_rejection_reason_is_rejected(self):
        order = self.order()
        for reason in ('', '   ', 'x'*1001, None):
            with self.subTest(reason=type(reason).__name__), self.assertRaises(CommerceError):
                reject_manual_payment(order.pk, self.owner, reason)
        order.refresh_from_db()
        self.assertEqual(order.status, Order.Status.AWAITING_PAYMENT)

    def test_trial_is_not_misrepresented_as_a_rejected_payment(self):
        trial = Plan.objects.create(slug='trial-review', name='Trial', price_irr=0,
            duration_days=14, student_limit=100, device_limit=1, is_trial=True, is_active=True, is_public=True)
        order = self.order(plan=trial)
        with self.assertRaises(CommerceError):
            reject_manual_payment(order.pk, self.owner, 'درخواست آزمایش در حال بررسی است.')
        self.assertFalse(VerifiedPayment.objects.exists())


@skipUnless(connection.vendor == 'postgresql', 'Payment races require production PostgreSQL row locks')
@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CommerceConcurrencyTests(Fixtures, TransactionTestCase):
    def setUp(self):
        self.make_fixtures()

    def concurrent(self, function):
        barrier = Barrier(2)
        def attempt(index):
            try:
                barrier.wait(timeout=5)
                return function(index)
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(attempt, range(2)))

    def test_parallel_checkout_does_not_duplicate_pending_order(self):
        orders = self.concurrent(lambda _: str(self.order().pk))
        self.assertEqual(len(set(orders)), 1)
        self.assertEqual(Order.objects.count(), 1)

    def test_parallel_verification_fulfills_and_extends_only_once(self):
        order = self.order()
        self.concurrent(lambda _: self.pay(order).pk)
        self.school.refresh_from_db()
        self.assertEqual(VerifiedPayment.objects.count(), 1)
        self.assertLess(abs((self.school.expires_at-timezone.now()).total_seconds()-365*86400), 2)
        self.assertEqual(EmailOutbox.objects.filter(dedupe_key=f'order:{order.pk}:fulfilled').count(), 1)


    def test_same_bank_settlement_cannot_fulfill_two_schools_concurrently(self):
        first = self.order()
        other_user = get_user_model().objects.create_user('second@example.com', email='second@example.com')
        other_school = School.objects.create(user=other_user, name='Second school')
        second = create_order(other_school, self.plan.pk, uuid.uuid4())
        orders = [first, second]
        def verify(index):
            try:
                verify_manual_payment(orders[index].pk, self.owner, 'BANK-SAME123', orders[index].amount_irr)
                return True
            except CommerceError:
                return False
        self.assertEqual(sorted(self.concurrent(verify)), [False, True])
        self.assertEqual(VerifiedPayment.objects.count(), 1)
        self.assertEqual(School.objects.filter(status=School.Status.ACTIVE).count(), 1)


    def test_claim_racing_verification_has_consistent_lock_order(self):
        order = self.order()
        def action(index):
            try:
                if index:
                    verify_manual_payment(order.pk, self.owner, 'BANK-LOCK123', order.amount_irr)
                else:
                    submit_manual_payment(self.school, order.pk, 'BANK-LOCK123')
                return True
            except CommerceError:
                return False  # Verification may finish before the customer claim.
        self.concurrent(action)
        self.assertEqual(VerifiedPayment.objects.count(), 1)
        self.school.refresh_from_db()
        self.assertTrue(self.school.entitled)

    def test_cancellation_racing_verification_is_one_atomic_outcome(self):
        order = self.order()
        def action(index):
            try:
                if index:
                    verify_manual_payment(order.pk, self.owner, 'BANK-CANCEL1', order.amount_irr)
                else:
                    cancel_order(self.school, order.pk)
                return True
            except CommerceError:
                return False
        results = self.concurrent(action)
        self.assertEqual(sorted(results), [False, True])
        order.refresh_from_db()
        self.school.refresh_from_db()
        if order.status == Order.Status.CANCELLED:
            self.assertFalse(self.school.entitled)
            self.assertFalse(VerifiedPayment.objects.exists())
        else:
            self.assertEqual(order.status, Order.Status.FULFILLED)
            self.assertTrue(self.school.entitled)
            self.assertEqual(VerifiedPayment.objects.count(), 1)


    def test_rejection_racing_verification_is_one_atomic_outcome(self):
        order = self.order()
        def action(index):
            try:
                if index:
                    verify_manual_payment(order.pk, self.owner, 'BANK-REJECT1', order.amount_irr)
                else:
                    reject_manual_payment(order.pk, self.owner, 'تراکنش تأیید نشده است.')
                return True
            except CommerceError:
                return False
        results = self.concurrent(action)
        self.assertEqual(sorted(results), [False, True])
        order.refresh_from_db()
        self.school.refresh_from_db()
        if order.status == Order.Status.REJECTED:
            self.assertFalse(self.school.entitled)
            self.assertFalse(VerifiedPayment.objects.exists())
        else:
            self.assertEqual(order.status, Order.Status.FULFILLED)
            self.assertTrue(self.school.entitled)
            self.assertEqual(VerifiedPayment.objects.count(), 1)
