from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from xml.etree.ElementTree import ParseError
from requests.exceptions import RequestException
from unittest.mock import patch

from django.test import TestCase, override_settings

from . import views
from .models import CustomUser


@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'testserver', 'localhost'])
class LoginRegistrationTests(TestCase):
    def test_register_rejects_weak_password_and_does_not_create_account(self):
        with patch(
            'registration.views.validate_registration_email',
            return_value=type('ValidatedEmail', (), {'normalized': 'weak@gmail.com'})(),
        ):
            response = self.client.post(
                '/login/',
                {
                    'email': 'weak@gmail.com',
                    'password': 'weakpass',
                },
                HTTP_HOST='127.0.0.1',
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomUser.objects.filter(username='weak@gmail.com').exists())
        self.assertContains(response, 'Password untuk akun baru harus mengandung')

    def test_register_creates_account_when_password_meets_requirements(self):
        with patch(
            'registration.views.validate_registration_email',
            return_value=type('ValidatedEmail', (), {'normalized': 'strong@gmail.com'})(),
        ):
            response = self.client.post(
                '/login/',
                {
                    'email': 'strong@gmail.com',
                    'password': 'Strong;123',
                },
                HTTP_HOST='127.0.0.1',
            )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(CustomUser.objects.filter(username='strong@gmail.com').exists())

    def test_register_rejects_invalid_email_format_and_does_not_create_account(self):
        response = self.client.post(
            '/login/',
            {
                'email': 'invalid-email',
                'password': 'Strong;123',
            },
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomUser.objects.filter(username='invalid-email').exists())
        self.assertContains(response, 'Email registrasi harus menggunakan akun Gmail')

    def test_register_rejects_non_gmail_email_and_does_not_create_account(self):
        with patch(
            'registration.views.validate_registration_email',
            side_effect=Exception('validator should not be called for non-gmail'),
        ):
            response = self.client.post(
                '/login/',
                {
                    'email': 'user@yahoo.com',
                    'password': 'Strong;123',
                },
                HTTP_HOST='127.0.0.1',
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomUser.objects.filter(username='user@yahoo.com').exists())
        self.assertContains(response, 'Email registrasi harus menggunakan akun Gmail')

    def test_register_rejects_invalid_or_undeliverable_gmail(self):
        from email_validator import EmailNotValidError

        with patch(
            'registration.views.validate_registration_email',
            side_effect=EmailNotValidError('bad gmail'),
        ):
            response = self.client.post(
                '/login/',
                {
                    'email': 'user@gmail.com',
                    'password': 'Strong;123',
                },
                HTTP_HOST='127.0.0.1',
            )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(CustomUser.objects.filter(username='user@gmail.com').exists())
        self.assertContains(response, 'Email Gmail tidak valid atau tidak dapat menerima email.')

    def test_login_existing_email_with_wrong_password_shows_registered_message(self):
        CustomUser.objects.create_user(
            username='registered@gmail.com',
            email='registered@gmail.com',
            password='Strong;123',
            user_type='ALUMNI',
        )

        response = self.client.post(
            '/login/',
            {
                'email': 'registered@gmail.com',
                'password': 'Wrong;123',
            },
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email sudah terdaftar, tetapi password salah.')

    def test_login_sso_account_with_email_password_redirects_user_to_sso(self):
        user = CustomUser.objects.create_user(
            username='2400000000',
            email='student@ui.ac.id',
            password='Temp;123',
            user_type='STUDENT',
        )
        user.set_unusable_password()
        user.save(update_fields=['password'])

        response = self.client.post(
            '/login/',
            {
                'email': 'student@ui.ac.id',
                'password': 'Anything;123',
            },
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Email ini sudah terhubung ke akun SSO UI')


@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'testserver', 'localhost'])
class SSOLoginTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()

    def _attach_session(self, request):
        middleware = SessionMiddleware(lambda req: None)
        middleware.process_request(request)
        request.session.save()

    def test_sso_login_stores_safe_next_url_before_redirecting(self):
        response = self.client.get(
            '/login/sso/?next=/checkout/mahasiswa/',
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/login/sso/callback/')
        self.assertEqual(self.client.session['sso_next_url'], '/checkout/mahasiswa/')

    def test_sync_sso_student_user_creates_student_account_without_password(self):
        sso_profile = {
            'username': '2400000000',
            'attributes': {
                'email': 'mahasiswa@ui.ac.id',
                'nama': 'Mahasiswa Fasilkom',
                'npm': '2400000000',
            },
        }

        user = views._sync_sso_student_user(sso_profile)

        self.assertEqual(user.username, '2400000000')
        self.assertEqual(user.email, 'mahasiswa@ui.ac.id')
        self.assertEqual(user.user_type, 'STUDENT')
        self.assertEqual(user.npm, '2400000000')
        self.assertFalse(user.has_usable_password())

    def test_complete_sso_login_uses_existing_user_and_redirects_to_saved_next(self):
        user = CustomUser.objects.create_user(
            username='alumni-lama',
            email='student@ui.ac.id',
            password='Strong;123',
            user_type='ALUMNI',
        )
        request = self.factory.get('/login/sso/callback/?ticket=abc')
        self._attach_session(request)
        request.session['sso_next_url'] = '/history/'

        response = views._complete_sso_login(
            request,
            {
                'username': '2400000001',
                'attributes': {
                    'email': 'student@ui.ac.id',
                    'nama': 'Student UI',
                    'npm': '2400000001',
                },
            },
        )

        user.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/history/')
        self.assertEqual(user.pk, CustomUser.objects.get(email='student@ui.ac.id').pk)
        self.assertEqual(user.user_type, 'STUDENT')
        self.assertEqual(user.npm, '2400000001')

    def test_sso_callback_redirects_to_login_when_cas_response_is_invalid(self):
        with patch('registration.views.sso_authenticate', return_value=None):
            response = self.client.get(
                '/login/sso/callback/?ticket=ST-invalid',
                HTTP_HOST='127.0.0.1',
            )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/login/')

    @patch('registration.views.sso_authenticate', side_effect=ParseError('bad xml'))
    def test_sso_callback_handles_parse_error_gracefully(self, mocked_authenticate):
        response = self.client.get(
            '/login/sso/callback/?ticket=ST-invalid',
            follow=True,
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Respons verifikasi dari SSO UI tidak valid')

    @patch('registration.views.sso_authenticate', side_effect=RequestException('network down'))
    def test_sso_callback_handles_sso_connection_error_gracefully(self, mocked_authenticate):
        response = self.client.get(
            '/login/sso/callback/?ticket=ST-invalid',
            follow=True,
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Koneksi ke server SSO UI gagal atau respons CAS2 tidak valid')
