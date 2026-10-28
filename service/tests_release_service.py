"""Release regressions use synthetic accounts/artifacts only."""
from datetime import timedelta
import hashlib
from pathlib import Path
import tempfile
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone
from .commerce import create_order, verify_manual_payment
from .models import (School, CustomerProfile, CompanyOwner, Product, Plan, Release,
                     VerifiedPayment, Order)


@override_settings(STORAGES={'staticfiles': {'BACKEND':'django.contrib.staticfiles.storage.StaticFilesStorage'}})
class ReleaseServiceRegressions(TestCase):
    def account(self, name='synthetic-release@example.invalid'):
        user = get_user_model().objects.create_user(name, name, 'Synthetic-release-751593!')
        CustomerProfile.objects.create(user=user, email_verified_at=timezone.now())
        return user

    def payment_fixture(self):
        owner = self.account('synthetic-release-owner@example.invalid')
        CompanyOwner.objects.create(user=owner)
        school = School.objects.create(user=self.account(), name='Synthetic release school')
        product = Product.objects.get(pk='facerecognition')
        plan = Plan.objects.create(product=product, slug='synthetic-release', name='Synthetic', price_irr=90000000,
                                   is_active=True, is_public=True)
        order = create_order(school, plan.pk, uuid.uuid4())
        return owner, school, order

    def test_published_installer_is_downloadable_before_school_or_purchase(self):
        user = self.account()
        self.client.force_login(user)
        with tempfile.TemporaryDirectory() as directory, override_settings(RELEASE_ROOT=directory):
            payload = b'Synthetic release artifact, not an executable'
            Path(directory,'synthetic-release.zip').write_bytes(payload)
            release = Release.objects.create(product_id='facerecognition', version='synthetic-release',
                artifact_name='synthetic-release.zip', size_bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest(),
                notes='Synthetic', is_published=True)
            response = self.client.get(f'/products/facerecognition/account/downloads/{release.pk}/', secure=True)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), payload)
            self.assertFalse(School.objects.filter(user=user).exists())
            self.assertFalse(Order.objects.filter(school__user=user).exists())
            self.assertEqual(self.client.get('/owner/', secure=True).status_code, 403)

    def test_deactivated_owner_snapshot_cannot_fulfill_payment(self):
        owner, school, order = self.payment_fixture()
        get_user_model().objects.filter(pk=owner.pk).update(is_active=False)
        self.assertTrue(owner.is_active)  # Simulates an already-authenticated in-flight request.
        with self.assertRaises(PermissionDenied):
            verify_manual_payment(order.pk, owner, 'SYNTHETIC-RELEASE-1', order.amount_irr)
        school.refresh_from_db(); order.refresh_from_db()
        self.assertFalse(school.entitled)
        self.assertFalse(VerifiedPayment.objects.filter(order=order).exists())

    def test_owner_replaced_while_waiting_for_financial_rows_is_rechecked(self):
        from .commerce import _locked_order
        owner, school, order = self.payment_fixture()
        successor = self.account('synthetic-release-successor@example.invalid')
        def after_wait(identifier):
            # Deterministic boundary reproduction: the decorator/initial owner
            # check predates acquisition of the financial rows.
            CompanyOwner.objects.filter(pk=1).update(user=successor, revision=2)
            return _locked_order(identifier)
        with patch('service.commerce._locked_order', side_effect=after_wait):
            with self.assertRaises(PermissionDenied):
                verify_manual_payment(order.pk, owner, 'SYNTHETIC-RELEASE-2', order.amount_irr)
        self.assertFalse(VerifiedPayment.objects.filter(order=order).exists())

    def test_rotated_owner_password_snapshot_cannot_fulfill_payment(self):
        owner, school, order = self.payment_fixture()
        fresh = get_user_model().objects.get(pk=owner.pk)
        fresh.set_password('Synthetic-new-owner-password-175319!')
        fresh.save(update_fields=['password'])
        with self.assertRaises(PermissionDenied):
            verify_manual_payment(order.pk, owner, 'SYNTHETIC-RELEASE-ROTATION', order.amount_irr)
        self.assertFalse(VerifiedPayment.objects.filter(order=order).exists())

    def test_unverified_company_cannot_download_installer_or_other_product(self):
        user = self.account()
        user.nxfi_profile.email_verified_at = None
        user.nxfi_profile.save(update_fields=['email_verified_at'])
        self.client.force_login(user)
        release = Release.objects.create(product_id='facerecognition', version='synthetic-release',
            artifact_name='synthetic-release.zip', size_bytes=1, sha256='0'*64, notes='Synthetic', is_published=True)
        self.assertEqual(self.client.get(f'/products/facerecognition/account/downloads/{release.pk}/', secure=True).status_code, 404)
        user.nxfi_profile.email_verified_at = timezone.now()
        user.nxfi_profile.save(update_fields=['email_verified_at'])
        other = Product.objects.create(slug='synthetic-unlaunched', name='Synthetic unlaunched', is_published=True)
        release.product = other
        release.save(update_fields=['product'])
        self.assertEqual(self.client.get(f'/products/facerecognition/account/downloads/{release.pk}/', secure=True).status_code, 404)


from django.db import connection, transaction, close_old_connections
from django.test import TransactionTestCase
from django.db.migrations.executor import MigrationExecutor
import threading
from unittest import skipUnless


class ReleaseMigrationCompatibility(TransactionTestCase):
    """Exercise the real migration executor, not just the RunPython function."""
    def test_forward_rollback_reapply_preserves_legacy_business_state(self):
        latest = ('service','0007_alter_companyowner_options_alter_school_options_and_more')
        old = ('service','0005_product_productsubscription_order_product_name_and_more')
        try:
            executor = MigrationExecutor(connection)
            executor.migrate([old])
            historical = executor.loader.project_state([old]).apps
            User = historical.get_model('auth','User')
            SchoolModel = historical.get_model('service','School')
            DeviceModel = historical.get_model('service','Device')
            historical.get_model('service','Product').objects.get_or_create(slug='facerecognition', defaults={'name':'FaceRecognition', 'is_published':True})
            PlanModel = historical.get_model('service','Plan')
            OrderModel = historical.get_model('service','Order')
            PaymentModel = historical.get_model('service','VerifiedPayment')
            customer = User.objects.create(username='synthetic-migration-customer@example.invalid', email='synthetic-migration-customer@example.invalid', password='synthetic-stored-hash', is_active=True)
            owner = User.objects.create(username='synthetic-migration-owner@example.invalid', password='synthetic-owner-hash', is_active=True)
            User.objects.create(username='synthetic-migration-unverified@example.invalid', password='synthetic-inactive-hash', is_active=False)
            now = timezone.now()
            school = SchoolModel.objects.create(user=customer, name='Synthetic legacy school', status='active', starts_at=now-timedelta(days=1), expires_at=now+timedelta(days=365), student_limit=900, email_verified_at=now)
            device = DeviceModel.objects.create(school=school, installation_id=uuid.uuid4(), label='Synthetic legacy desktop', token_digest='c'*64, password_stamp='synthetic-stamp', credential_expires_at=school.expires_at)
            plan = PlanModel.objects.create(product_id='facerecognition', slug='synthetic-migration', name='Synthetic migration', price_irr=90000000, is_active=True, is_public=True)
            order = OrderModel.objects.create(school=school, product_id='facerecognition', plan=plan, purpose='purchase', status='fulfilled', plan_name=plan.name, school_name=school.name, billing_email=customer.email, amount_irr=plan.price_irr, duration_days=365, student_limit=900, device_limit=1, expires_at=now+timedelta(days=7), fulfilled_at=now, approved_by=owner)
            payment = PaymentModel.objects.create(order=order, reference='SYNTHETIC-MIGRATION', amount_irr=plan.price_irr, verified_by=owner)
            expected = (str(school.pk), school.status, school.expires_at, str(device.installation_id), device.token_digest, str(order.pk), str(payment.pk), customer.password)
            for target in [latest, old, latest]:
                executor = MigrationExecutor(connection)
                executor.migrate([target])
                state = executor.loader.project_state([target]).apps
                kept_school = state.get_model('service','School').objects.get(pk=school.pk)
                kept_device = state.get_model('service','Device').objects.get(pk=device.pk)
                kept_order = state.get_model('service','Order').objects.get(pk=order.pk)
                kept_payment = state.get_model('service','VerifiedPayment').objects.get(pk=payment.pk)
                kept_user = state.get_model('auth','User').objects.get(pk=customer.pk)
                actual = (str(kept_school.pk), kept_school.status, kept_school.expires_at, str(kept_device.installation_id), kept_device.token_digest, str(kept_order.pk), str(kept_payment.pk), kept_user.password)
                self.assertEqual(actual, expected)
                if target == latest:
                    Profile = state.get_model('service','CustomerProfile')
                    profile = Profile.objects.get(user_id=customer.pk)
                    self.assertEqual(profile.email_verified_at, now)
                    self.assertEqual(profile.preferred_language, 'fa')
                    inactive = state.get_model('auth','User').objects.get(username='synthetic-migration-unverified@example.invalid')
                    self.assertFalse(inactive.is_active)
                    self.assertIsNone(Profile.objects.get(user_id=inactive.pk).email_verified_at)
        finally:
            MigrationExecutor(connection).migrate([latest])


@skipUnless(connection.vendor == 'postgresql', 'Requires actual PostgreSQL row locks')
class ReleaseOwnerConcurrency(TransactionTestCase):
    def test_replacement_during_school_lock_wait_denies_old_owner(self):
        owner = get_user_model().objects.create_user('synthetic-pg-owner@example.invalid')
        successor = get_user_model().objects.create_user('synthetic-pg-successor@example.invalid')
        CompanyOwner.objects.create(user=owner)
        user = get_user_model().objects.create_user('synthetic-pg-school@example.invalid')
        school = School.objects.create(user=user, name='Synthetic PG school')
        Product.objects.get_or_create(slug='facerecognition', defaults={'name':'FaceRecognition', 'is_published':True})
        plan = Plan.objects.create(product_id='facerecognition', slug='synthetic-pg-release', name='Synthetic', price_irr=90000000, is_active=True, is_public=True)
        order = create_order(school, plan.pk, uuid.uuid4())
        admitted, done = threading.Event(), threading.Event()
        outcome = []
        from .commerce import _owner
        def observed_actor(actor):
            _owner(actor)
            admitted.set()
        def worker():
            close_old_connections()
            try:
                verify_manual_payment(order.pk, owner, 'SYNTHETIC-PG-RELEASE', order.amount_irr)
            except PermissionDenied:
                outcome.append('denied')
            except Exception as exc:
                outcome.append(type(exc).__name__)
            else:
                outcome.append('activated')
            finally:
                close_old_connections()
                done.set()
        with patch('service.commerce._owner', side_effect=observed_actor):
            with transaction.atomic():
                School.objects.select_for_update().get(pk=school.pk)
                thread = threading.Thread(target=worker)
                thread.start()
                self.assertTrue(admitted.wait(5), 'Owner check never completed')
                CompanyOwner.objects.filter(pk=1).update(user=successor, revision=2)
            self.assertTrue(done.wait(5), 'Verification did not finish after row-lock release')
            thread.join(timeout=1)
        self.assertEqual(outcome, ['denied'])
        self.assertFalse(VerifiedPayment.objects.filter(order=order).exists())
