from unittest.mock import patch

from django.test import TestCase, override_settings

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
