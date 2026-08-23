from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from xml.etree.ElementTree import ParseError
from requests.exceptions import RequestException
from unittest.mock import patch
from unittest.mock import Mock
from decimal import Decimal
import os
import shutil
import tempfile

from django.test import TestCase, override_settings

from . import views
from .models import CustomUser, Ticket, Transaction, TransactionSpreadsheetBackup


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

    @patch('registration.views.get_cas_client')
    def test_sso_callback_without_ticket_forces_credential_reentry(self, mocked_get_cas_client):
        verify_client = Mock()
        renew_client = Mock()
        renew_client.get_login_url.return_value = 'https://sso.ui.ac.id/cas2/login?service=test&renew=true'
        mocked_get_cas_client.side_effect = [verify_client, renew_client]

        response = self.client.get(
            '/login/sso/callback/',
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, 'https://sso.ui.ac.id/cas2/login?service=test&renew=true')
        self.assertTrue(renew_client.renew)

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


@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'testserver', 'localhost'])
class CheckoutPersistenceTests(TestCase):
    def test_checkout_alumni_saves_whatsapp_and_cohort_year_to_transaction(self):
        user = CustomUser.objects.create_user(
            username='checkout@gmail.com',
            email='checkout@gmail.com',
            password='Strong;123',
            user_type='ALUMNI',
        )
        self.client.force_login(user)

        response = self.client.post(
            '/checkout/alumni/',
            {
                'full_name': 'Bilqis Nisrina',
                'whatsapp_number': '081234567890',
                'cohort_year': '2022',
                'study_program': 'Ilmu Komputer',
                'ticket_quantity': '2',
                'shirt_size_1': 'M',
                'shirt_size_2': 'L',
            },
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 302)
        transaction = Transaction.objects.get(user=user)
        self.assertEqual(transaction.whatsapp_number, '081234567890')
        self.assertEqual(transaction.cohort_year, 2022)
        self.assertEqual(transaction.tickets.count(), 2)


class AdminSpreadsheetTests(TestCase):
    def setUp(self):
        self.temp_media_root = tempfile.mkdtemp()
        self.settings_override = self.settings(
            MEDIA_ROOT=self.temp_media_root,
            ALLOWED_HOSTS=['127.0.0.1', 'testserver', 'localhost'],
        )
        self.settings_override.enable()

        self.admin_user = CustomUser.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='Admin;123',
        )
        self.client.force_login(self.admin_user)

        self.regular_user = CustomUser.objects.create_user(
            username='user@gmail.com',
            email='user@gmail.com',
            password='Strong;123',
            user_type='ALUMNI',
            first_name='Budi',
            last_name='Santoso',
        )

        self.transaction = Transaction.objects.create(
            user=self.regular_user,
            status='PENDING',
            whatsapp_number='081234567890',
            cohort_year=2022,
            total_amount=Decimal('275000'),
        )
        Ticket.objects.create(
            transaction=self.transaction,
            attendee_name='Budi Santoso',
            package_type='ALUMNI_PACK',
            tshirt_size='L',
            price=Decimal('275000'),
        )

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.temp_media_root, ignore_errors=True)

    def test_admin_latest_spreadsheet_export_always_uses_newest_transaction_data(self):
        first_response = self.client.get('/admin/registration/transaction/export/latest/')
        first_csv = first_response.content.decode('utf-8-sig')

        self.assertEqual(first_response.status_code, 200)
        self.assertIn(self.transaction.transaction_id, first_csv)
        first_line_count = len([line for line in first_csv.splitlines() if line.strip()])

        second_transaction = Transaction.objects.create(
            user=self.regular_user,
            status='PAID',
            total_amount=Decimal('50000'),
        )
        Ticket.objects.create(
            transaction=second_transaction,
            attendee_name='Budi Santoso',
            package_type='TICKET_ONLY',
            tshirt_size='NONE',
            price=Decimal('50000'),
        )

        second_response = self.client.get('/admin/registration/transaction/export/latest/')
        second_csv = second_response.content.decode('utf-8-sig')
        second_line_count = len([line for line in second_csv.splitlines() if line.strip()])

        self.assertEqual(second_response.status_code, 200)
        self.assertIn(second_transaction.transaction_id, second_csv)
        self.assertGreater(second_line_count, first_line_count)
        self.assertIn('081234567890', second_csv)
        self.assertIn('2022', second_csv)

    def test_admin_can_create_new_backup_snapshot_file_from_current_responses(self):
        response = self.client.get('/admin/registration/transaction/backup/create/', follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(TransactionSpreadsheetBackup.objects.count(), 1)

        backup = TransactionSpreadsheetBackup.objects.get()
        self.assertEqual(backup.transaction_count, 1)
        self.assertEqual(backup.response_count, 1)
        self.assertTrue(backup.file.name.endswith('.csv'))
        self.assertTrue(os.path.exists(backup.file.path))
