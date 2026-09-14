from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from tahsilat.models import KullaniciYetki


@override_settings(SECURE_SSL_REDIRECT=False)
class ViewSecuritySmokeTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='OWNER')
        self.user = User.objects.create_user(username='TESTUSER')

    def grant(self, menu_name):
        return KullaniciYetki.objects.create(
            kullanici=self.user,
            menu_adi=menu_name,
            erisim_izni=True,
            olusturan=self.owner,
        )

    @patch('tahsilat.views.authenticate')
    def test_login_view_uses_shared_redirect_resolution(self, authenticate_mock):
        self.grant('perakende')
        self.user.backend = 'django.contrib.auth.backends.ModelBackend'
        authenticate_mock.return_value = self.user

        response = self.client.post(
            reverse('tahsilat:login'),
            {'username': 'TESTUSER', 'password': 'secret'},
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['login_success'])
        self.assertEqual(response.context['redirect_url'], '/perakende/')

    def test_dashboard_redirects_when_dashboard_permission_missing(self):
        self.grant('perakende')
        self.client.force_login(self.user)

        response = self.client.get(reverse('tahsilat:dashboard'))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], '/perakende/')

    def test_reports_chat_query_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)

        response = client.post(
            '/reports/api/n8n/chat-query/',
            data='{"query":"Merhaba"}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)

    def test_reports_chat_page_requires_yapay_zeka_permission(self):
        self.client.force_login(self.user)

        response = self.client.get('/reports/reports/chat/')

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response['Location'], reverse('tahsilat:dashboard'))

    def test_reports_chat_query_requires_yapay_zeka_permission(self):
        self.client.force_login(self.user)

        response = self.client.post(
            '/reports/api/n8n/chat-query/',
            data='{"query":"Merhaba"}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 403)
