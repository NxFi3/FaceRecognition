"""Owner-owned school onboarding uses the real web authorization boundary."""
from django.contrib.auth import get_user_model, authenticate
from django.test import Client, TestCase
from django.utils import timezone

from .models import AuditEvent, School
from .owner_models import CompanyOwner


class OwnerSchoolProvisionTests(TestCase):
    def setUp(self):
        self.owner_password = "Only-for-tests-owner-passphrase99!"
        self.owner = get_user_model().objects.create_user(
            username="test-owner@example.invalid", email="test-owner@example.invalid",
            password=self.owner_password, is_active=True,
        )
        CompanyOwner.objects.create(pk=1, user=self.owner)
        self.client.force_login(self.owner)

    def step_up(self):
        response = self.client.post("/owner/reauth/", {
            "password": self.owner_password, "next": "/owner/schools/new/",
        }, secure=True)
        self.assertEqual(response.status_code, 302)

    def test_only_owner_with_fresh_password_can_provision_a_school(self):
        response = self.client.get("/owner/schools/new/", secure=True)
        self.assertEqual(response.status_code, 302)  # Reauthentication required.
        self.step_up()
        response = self.client.post("/owner/schools/new/", {
            "name": "Pilot Test Secondary School",
            "email": "school-provisioned@example.invalid",
            "temporary_password": "Unique-test-school-passphrase99!",
            "student_limit": "900",
            "device_limit": "2",
            "subscription_days": "30",
        }, secure=True)
        self.assertEqual(response.status_code, 302, response.content[:500])
        school = School.objects.get(user__username="school-provisioned@example.invalid")
        self.assertTrue(school.entitled)
        self.assertEqual((school.student_limit, school.device_limit), (900, 2))
        self.assertGreater(school.expires_at, timezone.now())
        self.assertIsNotNone(authenticate(username=school.user.username, password="Unique-test-school-passphrase99!"))
        self.assertTrue(AuditEvent.objects.filter(school=school, action="school.owner_provisioned").exists())
        self.assertNotEqual(school.user_id, self.owner.pk)

    def test_other_accounts_cannot_add_schools(self):
        user = get_user_model().objects.create_user(username="outsider@example.invalid", password="Test-outsider-passphrase99!")
        other = Client()
        other.force_login(user)
        result = other.post("/owner/schools/new/", {
            "name": "Unauthorized", "email": "attack@example.invalid",
            "temporary_password": "Attack-passphrase99!", "student_limit": "900",
            "device_limit": "1", "subscription_days": "30",
        }, secure=True)
        self.assertEqual(result.status_code, 403)
        self.assertFalse(School.objects.filter(name="Unauthorized").exists())

    def test_existing_email_cannot_be_reprovisioned(self):
        self.step_up()
        get_user_model().objects.create_user(username="existing@example.invalid", password="Existing-passphrase99!")
        result = self.client.post("/owner/schools/new/", {
            "name": "Duplicate", "email": "EXISTING@example.invalid",
            "temporary_password": "Another-passphrase99!", "student_limit": "900",
            "device_limit": "1", "subscription_days": "30",
        }, secure=True)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(School.objects.filter(name="Duplicate").exists())
