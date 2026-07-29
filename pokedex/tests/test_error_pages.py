from django.test import TestCase, override_settings


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
class ErrorPageTests(TestCase):
    def test_unknown_url_renders_the_styled_404(self):
        """Django looks for 404.html at a template root, not main/404.html, so
        without a root template a bad URL fell back to the plain builtin page."""
        response = self.client.get("/no-such-page/")

        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "Page not found", status_code=404)
        self.assertContains(response, "Back to search", status_code=404)
