from django.core import mail
from django.test import TestCase
from django.urls import reverse

from apps.tenants.models import Tenant

from .models import User

LOGIN = reverse("accounts:login")
DASHBOARD = reverse("core:dashboard")


class LoginTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.tenant = Tenant.objects.create(name="Lazz Pharma", slug="lazz")
        cls.user = User.objects.create_user("owner@x.test", "S3cret!pw", phone="01711111111", tenant=cls.tenant)

    def _login(self, username, password="S3cret!pw", remember=True):
        data = {"username": username, "password": password}
        if remember:
            data["remember_me"] = "on"
        return self.client.post(LOGIN, data)

    def test_login_with_email(self):
        self.assertRedirects(self._login("owner@x.test"), DASHBOARD)

    def test_login_with_email_is_case_insensitive(self):
        self.assertRedirects(self._login("Owner@X.test"), DASHBOARD)

    def test_login_with_phone(self):
        self.assertRedirects(self._login("01711111111"), DASHBOARD)

    def test_wrong_password_shows_error(self):
        response = self._login("owner@x.test", password="nope")
        self.assertContains(response, "Wrong email/phone or password.")

    def test_shared_phone_cannot_log_in(self):
        User.objects.create_user("other@x.test", "S3cret!pw", phone="01711111111", tenant=self.tenant)
        self.assertContains(self._login("01711111111"), "Wrong email/phone or password.")

    def test_inactive_user_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        self.assertContains(self._login("owner@x.test"), "Wrong email/phone or password.")

    def test_without_remember_me_session_ends_with_browser(self):
        self._login("owner@x.test", remember=False)
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    def test_logged_in_user_skips_login_page(self):
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get(LOGIN), DASHBOARD)

    def test_logout_requires_post_and_ends_session(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse("accounts:logout")).status_code, 405)
        self.assertRedirects(self.client.post(reverse("accounts:logout")), LOGIN)
        self.assertNotIn("_auth_user_id", self.client.session)


class ProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = User.objects.create_user("owner@x.test", "S3cret!pw", phone="01711111111")
        User.objects.create_user("other@x.test", "S3cret!pw", phone="01822222222")

    def setUp(self):
        self.client.force_login(self.user)

    def test_update_profile(self):
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": "Rina", "last_name": "Akter", "email": "owner@x.test", "phone": "01733333333",
        })
        self.assertRedirects(response, reverse("accounts:profile"))
        self.user.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.phone), ("Rina", "01733333333"))

    def test_phone_used_by_someone_else_is_rejected(self):
        response = self.client.post(reverse("accounts:profile"), {
            "first_name": "", "last_name": "", "email": "owner@x.test", "phone": "01822222222",
        })
        self.assertContains(response, "Another account already uses this phone number.")

    def test_change_password(self):
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": "S3cret!pw", "new_password1": "N3w!passw0rd", "new_password2": "N3w!passw0rd",
        })
        self.assertRedirects(response, reverse("accounts:profile"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("N3w!passw0rd"))


class PasswordResetTests(TestCase):
    def test_reset_email_contains_working_link(self):
        User.objects.create_user("owner@x.test", "S3cret!pw")
        response = self.client.post(reverse("accounts:password_reset"), {"email": "owner@x.test"})
        self.assertRedirects(response, reverse("accounts:password_reset_done"))
        self.assertEqual(len(mail.outbox), 1)
        link = next(line for line in mail.outbox[0].body.splitlines() if "/accounts/password/reset/" in line)
        path = link.split("testserver", 1)[1]
        # The first hit swaps the token into the session and redirects to the set-password form.
        response = self.client.get(path, follow=True)
        self.assertContains(response, "Set a new password")
