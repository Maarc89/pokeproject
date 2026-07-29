from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

PASSWORD = "a-good-test-password-42"


class RegistrationTests(TestCase):
    def test_weak_password_is_rejected(self):
        """The headline security fix.

        Registration used to call User.objects.create_user directly, which skips
        AUTH_PASSWORD_VALIDATORS entirely -- "1" was an acceptable password.
        """
        response = self.client.post(
            reverse("register"),
            {"username": "ash", "password1": "1", "password2": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username="ash").exists())

    def test_common_password_is_rejected(self):
        response = self.client.post(
            reverse("register"),
            {"username": "ash", "password1": "password", "password2": "password"},
        )
        self.assertFalse(User.objects.filter(username="ash").exists())
        self.assertContains(response, "too common")

    def test_mismatched_passwords_are_rejected(self):
        self.client.post(
            reverse("register"),
            {"username": "ash", "password1": PASSWORD, "password2": PASSWORD + "x"},
        )
        self.assertFalse(User.objects.filter(username="ash").exists())

    def test_duplicate_username_is_rejected_without_leaking_internals(self):
        User.objects.create_user("ash", password=PASSWORD)

        response = self.client.post(
            reverse("register"),
            {"username": "ash", "password1": PASSWORD, "password2": PASSWORD},
        )

        self.assertEqual(User.objects.filter(username="ash").count(), 1)
        # The old view interpolated the raw exception, which would have exposed
        # the Postgres constraint name.
        self.assertNotContains(response, "duplicate key")
        self.assertNotContains(response, "pokedex_")

    def test_successful_registration_logs_you_in(self):
        response = self.client.post(
            reverse("register"),
            {"username": "ash", "password1": PASSWORD, "password2": PASSWORD},
        )

        self.assertRedirects(response, reverse("index"))
        self.assertTrue(User.objects.filter(username="ash").exists())
        self.assertTrue(response.wsgi_request.user.is_authenticated)


class LoginTests(TestCase):
    def setUp(self):
        User.objects.create_user("ash", password=PASSWORD)

    def test_valid_credentials_log_you_in(self):
        response = self.client.post(
            reverse("login"), {"username": "ash", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("index"))

    def test_bad_credentials_show_an_error(self):
        response = self.client.post(
            reverse("login"), {"username": "ash", "password": "wrong"}
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_next_is_honoured(self):
        """Regression: the view hardcoded a redirect to the index, so
        @login_required always dumped people on the home page."""
        response = self.client.post(
            reverse("login"),
            {"username": "ash", "password": PASSWORD, "next": reverse("battle")},
        )
        self.assertRedirects(response, reverse("battle"))

    def test_next_to_another_host_is_ignored(self):
        response = self.client.post(
            reverse("login"),
            {"username": "ash", "password": PASSWORD, "next": "https://evil.invalid/steal"},
        )
        self.assertRedirects(response, reverse("index"))

    def test_login_required_round_trip(self):
        redirected = self.client.get(reverse("battle"))
        self.assertIn("next=", redirected.url)

        response = self.client.post(
            redirected.url, {"username": "ash", "password": PASSWORD}
        )
        self.assertRedirects(response, reverse("battle"))


class LogoutTests(TestCase):
    """Regression: LogoutView has rejected GET since Django 5.0, and the header
    linked to it with a plain anchor, so Sign out returned 405."""

    def setUp(self):
        User.objects.create_user("ash", password=PASSWORD)
        self.client.login(username="ash", password=PASSWORD)

    def test_post_logs_you_out(self):
        response = self.client.post(reverse("logout"))
        self.assertRedirects(response, reverse("index"))
        self.assertFalse(response.wsgi_request.user.is_authenticated)

    def test_get_is_rejected(self):
        self.assertEqual(self.client.get(reverse("logout")).status_code, 405)

    def test_header_uses_a_post_form_not_a_link(self):
        response = self.client.get(reverse("index"))
        self.assertContains(response, f'action="{reverse("logout")}"')
        self.assertNotContains(response, f'href="{reverse("logout")}"')
