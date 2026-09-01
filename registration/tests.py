from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from xml.etree.ElementTree import ParseError
from requests.exceptions import ConnectTimeout, RequestException
from unittest.mock import patch
from unittest.mock import Mock
from decimal import Decimal
from datetime import timedelta
import json
import os
import shutil
import tempfile

from django.test import TestCase, override_settings
from django.utils import timezone

from . import views
from .models import CustomUser, Ticket, Transaction, TransactionSpreadsheetBackup
from .payment_gateway import initiate_payment


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
    @patch('registration.views.initiate_payment', return_value='https://payment.example/redirect')
    def test_checkout_alumni_saves_whatsapp_and_cohort_year_to_transaction(self, mocked_initiate_payment):
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
                'degree_level': 'S1',
                'study_program': 'Ilmu Komputer',
                'ticket_quantity': '2',
                'shirt_size_1': 'M',
                'shirt_size_2': 'L',
            },
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/history/')
        transaction = Transaction.objects.get(user=user)
        self.assertEqual(transaction.status, 'PENDING_PAYMENT')
        self.assertEqual(transaction.whatsapp_number, '081234567890')
        self.assertEqual(transaction.cohort_year, 2022)
        self.assertEqual(transaction.degree_level, 'S1')
        self.assertEqual(transaction.study_program, 'ILMU_KOMPUTER')
        self.assertEqual(transaction.tickets.count(), 2)
        mocked_initiate_payment.assert_not_called()

    def test_checkout_mahasiswa_uses_cohort_year_from_sso_npm(self):
        user = CustomUser.objects.create_user(
            username='2400000002',
            email='mahasiswa@ui.ac.id',
            password='Strong;123',
            user_type='STUDENT',
            npm='2400000002',
        )
        self.client.force_login(user)

        response = self.client.post(
            '/checkout/mahasiswa/',
            {
                'first_name': 'Mahasiswa',
                'last_name': 'UI',
                'whatsapp_number': '081234567890',
                'cohort_year': '2023',
                'degree_level': 'S1',
                'study_program': 'ILMU_KOMPUTER',
                'ticket_quantity': '1',
                'shirt_size_1': 'M',
            },
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 302)
        transaction = Transaction.objects.get(user=user)
        self.assertEqual(transaction.cohort_year, 2024)

    def test_history_expires_pending_payment_after_six_minutes(self):
        user = CustomUser.objects.create_user(
            username='expired@gmail.com',
            email='expired@gmail.com',
            password='Strong;123',
            user_type='ALUMNI',
        )
        transaction = Transaction.objects.create(
            user=user,
            status='PENDING_PAYMENT',
            total_amount=Decimal('275000'),
        )
        Ticket.objects.create(
            transaction=transaction,
            first_name='Peserta',
            last_name='Expired',
            package_type='ALUMNI_PACK',
            tshirt_size='M',
            price=Decimal('275000'),
        )
        Transaction.objects.filter(pk=transaction.pk).update(
            created_at=timezone.now() - timedelta(minutes=7),
        )

        self.client.force_login(user)
        response = self.client.get('/history/', HTTP_HOST='127.0.0.1')

        transaction.refresh_from_db()
        self.assertEqual(transaction.status, 'EXPIRED')
        self.assertContains(response, 'Kedaluwarsa')

    @patch('registration.views.initiate_payment', return_value='https://payment.example/retry')
    def test_retry_payment_rotates_idempotency_key_when_redirect_url_missing(self, mocked_initiate_payment):
        user = CustomUser.objects.create_user(
            username='retry@gmail.com',
            email='retry@gmail.com',
            password='Strong;123',
            user_type='ALUMNI',
        )
        transaction = Transaction.objects.create(
            user=user,
            status='PENDING_PAYMENT',
            whatsapp_number='081234567890',
            cohort_year=2022,
            total_amount=Decimal('275000'),
        )
        original_key = transaction.idempotency_key
        Ticket.objects.create(
            transaction=transaction,
            attendee_name='Retry User',
            package_type='ALUMNI_PACK',
            tshirt_size='M',
            price=Decimal('275000'),
        )

        self.client.force_login(user)
        response = self.client.post(
            f'/history/retry-payment/{transaction.id}/',
            HTTP_HOST='127.0.0.1',
        )

        transaction.refresh_from_db()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, 'https://payment.example/retry')
        self.assertNotEqual(transaction.idempotency_key, original_key)
        self.assertEqual(transaction.status, 'PENDING_CONFIRMATION')

    @patch('registration.views.initiate_payment')
    def test_confirmation_transaction_reopens_saved_payment_url(self, mocked_initiate_payment):
        user = CustomUser.objects.create_user(
            username='reopen@gmail.com',
            email='reopen@gmail.com',
            password='Strong;123',
            user_type='ALUMNI',
        )
        transaction = Transaction.objects.create(
            user=user,
            status='PENDING_CONFIRMATION',
            payment_redirect_url='https://payment.example/choose-method',
            total_amount=Decimal('275000'),
        )
        Ticket.objects.create(
            transaction=transaction,
            attendee_name='Reopen User',
            package_type='ALUMNI_PACK',
            tshirt_size='M',
            price=Decimal('275000'),
        )

        self.client.force_login(user)
        response = self.client.post(
            f'/history/retry-payment/{transaction.id}/',
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, 'https://payment.example/choose-method')
        mocked_initiate_payment.assert_not_called()

    def test_cancel_order_uses_cancelled_status(self):
        user = CustomUser.objects.create_user(
            username='cancel@gmail.com',
            email='cancel@gmail.com',
            password='Strong;123',
            user_type='ALUMNI',
        )
        transaction = Transaction.objects.create(
            user=user,
            status='PENDING_PAYMENT',
            total_amount=Decimal('275000'),
        )
        Ticket.objects.create(
            transaction=transaction,
            attendee_name='Cancel User',
            package_type='ALUMNI_PACK',
            tshirt_size='M',
            price=Decimal('275000'),
        )

        self.client.force_login(user)
        response = self.client.post(
            '/history/manage/',
            {'transaction_id': transaction.id, 'action': 'cancel'},
            HTTP_HOST='127.0.0.1',
        )

        transaction.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(transaction.status, 'CANCELLED')

    def test_manage_ticket_updates_sizes_from_dropdown_fields(self):
        user = CustomUser.objects.create_user(
            username='sizeupdate@gmail.com',
            email='sizeupdate@gmail.com',
            password='Strong;123',
            user_type='ALUMNI',
        )
        transaction = Transaction.objects.create(
            user=user,
            status='PENDING_PAYMENT',
            total_amount=Decimal('550000'),
        )
        first_ticket = Ticket.objects.create(
            transaction=transaction,
            attendee_name='Update User',
            package_type='ALUMNI_PACK',
            tshirt_size='M',
            price=Decimal('275000'),
        )
        second_ticket = Ticket.objects.create(
            transaction=transaction,
            attendee_name='Update User',
            package_type='ALUMNI_PACK',
            tshirt_size='L',
            price=Decimal('275000'),
        )

        self.client.force_login(user)
        response = self.client.post(
            '/history/manage/',
            {
                'transaction_id': transaction.id,
                'action': 'update',
                'new_sizes': ['XS', '3XL'],
            },
            HTTP_HOST='127.0.0.1',
        )

        first_ticket.refresh_from_db()
        second_ticket.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(first_ticket.tshirt_size, 'XS')
        self.assertEqual(second_ticket.tshirt_size, '3XL')


@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'testserver', 'localhost'])
class PaymentStatusFlowTests(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            username='status@gmail.com',
            email='status@gmail.com',
            password='Strong;123',
            user_type='ALUMNI',
        )

    def _create_transaction(self):
        transaction = Transaction.objects.create(
            user=self.user,
            status='PENDING_CONFIRMATION',
            total_amount=Decimal('275000'),
        )
        Ticket.objects.create(
            transaction=transaction,
            attendee_name='Status User',
            package_type='ALUMNI_PACK',
            tshirt_size='M',
            price=Decimal('275000'),
        )
        return transaction

    def test_gateway_callbacks_map_to_confirmation_success_and_failure(self):
        expected_statuses = {
            'processing': 'PENDING_CONFIRMATION',
            'success': 'PAID',
            'capture': 'PAID',
            'authorized': 'PAID',
            'failed': 'FAILED',
        }

        for gateway_status, expected_local_status in expected_statuses.items():
            with self.subTest(gateway_status=gateway_status):
                transaction = self._create_transaction()
                response = self.client.post(
                    '/callback/payment/',
                    data=json.dumps({
                        'idempotency_key': transaction.idempotency_key,
                        'transaction_id': f'gateway-{transaction.id}',
                        'status': gateway_status,
                    }),
                    content_type='application/json',
                    HTTP_HOST='127.0.0.1',
                )

                transaction.refresh_from_db()
                self.assertEqual(response.status_code, 200)
                self.assertEqual(transaction.status, expected_local_status)

    def test_gateway_callback_accepts_form_encoded_payload(self):
        transaction = self._create_transaction()
        response = self.client.post(
            '/callback/payment/',
            data={
                'idempotency_key': transaction.idempotency_key,
                'transaction_id': f'gateway-{transaction.id}',
                'status': 'success',
            },
            HTTP_HOST='127.0.0.1',
        )

        transaction.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(transaction.status, 'PAID')

    @patch('registration.views.refresh_transaction_status')
    def test_history_refreshes_pending_gateway_transactions(self, mocked_refresh_transaction_status):
        transaction = self._create_transaction()
        transaction.gateway_transaction_id = 'gateway-1'
        transaction.save(update_fields=['gateway_transaction_id'])

        self.client.force_login(self.user)
        response = self.client.get('/history/', HTTP_HOST='127.0.0.1')

        self.assertEqual(response.status_code, 200)
        mocked_refresh_transaction_status.assert_called_once()
        self.assertEqual(mocked_refresh_transaction_status.call_args.args[0].id, transaction.id)

    @patch('registration.views.refresh_transaction_status')
    def test_payment_return_redirects_back_to_history_when_paid(self, mocked_refresh_transaction_status):
        transaction = self._create_transaction()
        transaction.gateway_transaction_id = 'gateway-2'
        transaction.save(update_fields=['gateway_transaction_id'])

        def mark_paid(transaction_obj):
            transaction_obj.status = 'PAID'
            transaction_obj.save(update_fields=['status'])

        mocked_refresh_transaction_status.side_effect = mark_paid

        self.client.force_login(self.user)
        response = self.client.get(
            f'/payment/?trx={transaction.idempotency_key}&gateway_return=success',
            HTTP_HOST='127.0.0.1',
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, '/history/')

    @patch('registration.views.initiate_payment', return_value='https://dev-payment.ui.ac.id/pay/example')
    def test_payment_page_redirects_directly_to_gateway_when_link_missing(self, mocked_initiate_payment):
        transaction = Transaction.objects.create(
            user=self.user,
            status='PENDING_PAYMENT',
            total_amount=Decimal('275000'),
        )
        Ticket.objects.create(
            transaction=transaction,
            attendee_name='Gateway Redirect User',
            package_type='ALUMNI_PACK',
            tshirt_size='M',
            price=Decimal('275000'),
        )

        self.client.force_login(self.user)
        response = self.client.get(
            f'/payment/?trx={transaction.idempotency_key}',
            HTTP_HOST='127.0.0.1',
        )

        transaction.refresh_from_db()
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, 'https://dev-payment.ui.ac.id/pay/example')
        self.assertEqual(transaction.status, 'PENDING_CONFIRMATION')
        mocked_initiate_payment.assert_called_once()


@override_settings(ALLOWED_HOSTS=['127.0.0.1', 'testserver', 'localhost'])
class PaymentGatewayFallbackTests(TestCase):
    def setUp(self):
        self.factory = RequestFactory()
        self.user = CustomUser.objects.create_user(
            username='gateway@gmail.com',
            email='gateway@gmail.com',
            password='Strong;123',
            user_type='ALUMNI',
        )
        self.transaction = Transaction.objects.create(
            user=self.user,
            status='PENDING_PAYMENT',
            whatsapp_number='081234567890',
            cohort_year=2022,
            total_amount=Decimal('275000'),
        )
        Ticket.objects.create(
            transaction=self.transaction,
            attendee_name='Gateway User',
            package_type='ALUMNI_PACK',
            tshirt_size='M',
            price=Decimal('275000'),
        )
        self.request = self.factory.get('/', HTTP_HOST='127.0.0.1')

    @patch.dict(
        os.environ,
        {
            'PAYMENT_GATEWAY_API_KEY': 'api-key',
            'PAYMENT_GATEWAY_SIGNING_SECRET': 'secret',
            'PAYMENT_GATEWAY_BASE_URL': 'https://dev-payment.ui.ac.id',
            'PAYMENT_GATEWAY_FALLBACK_BASE_URL': 'https://payment.ui.ac.id',
        },
        clear=False,
    )
    @patch('registration.payment_gateway.requests.post')
    def test_initiate_payment_uses_fallback_base_url_when_primary_times_out(self, mocked_post):
        timeout_error = ConnectTimeout('primary timeout')
        success_response = Mock()
        success_response.status_code = 201
        success_response.json.return_value = {
            'success': True,
            'message': 'payment initiated',
            'data': {
                'transaction_id': 'gateway-uuid',
                'status': 'initiated',
                'redirect_url': 'https://payment.ui.ac.id/pay/example',
            },
        }
        mocked_post.side_effect = [timeout_error, success_response]

        redirect_url = initiate_payment(self.transaction, self.request, 'Paket Alumni')
        self.transaction.refresh_from_db()

        self.assertEqual(redirect_url, 'https://payment.ui.ac.id/pay/example')
        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(
            mocked_post.call_args_list[1].args[0],
            'https://payment.ui.ac.id/api/v1/gateway/payments',
        )
        self.assertEqual(self.transaction.payment_redirect_url, 'https://payment.ui.ac.id/pay/example')

    @patch.dict(
        os.environ,
        {
            'PAYMENT_GATEWAY_API_KEY': 'api-key',
            'PAYMENT_GATEWAY_SIGNING_SECRET': 'secret',
            'PAYMENT_GATEWAY_BASE_URL': 'https://dev-payment.ui.ac.id',
            'PAYMENT_GATEWAY_FALLBACK_BASE_URL': '',
        },
        clear=False,
    )
    @patch('registration.payment_gateway.requests.post')
    def test_initiate_payment_extracts_finpay_url_from_nonstandard_field(self, mocked_post):
        success_response = Mock()
        success_response.status_code = 201
        success_response.json.return_value = {
            'success': True,
            'message': 'payment initiated',
            'data': {
                'transaction_id': 'gateway-uuid',
                'status': 'initiated',
                'payment_link': 'https://devo.finpay.id/pg/payment/card/id/v2/access/example-token',
            },
        }
        mocked_post.return_value = success_response

        redirect_url = initiate_payment(self.transaction, self.request, 'Paket Alumni')
        self.transaction.refresh_from_db()

        self.assertEqual(
            redirect_url,
            'https://devo.finpay.id/pg/payment/card/id/v2/access/example-token',
        )
        self.assertEqual(
            self.transaction.payment_redirect_url,
            'https://devo.finpay.id/pg/payment/card/id/v2/access/example-token',
        )

    @patch.dict(
        os.environ,
        {
            'PAYMENT_GATEWAY_API_KEY': 'api-key',
            'PAYMENT_GATEWAY_SIGNING_SECRET': 'secret',
            'PAYMENT_GATEWAY_BASE_URL': 'https://dev-payment.ui.ac.id',
            'PAYMENT_GATEWAY_FALLBACK_BASE_URL': '',
        },
        clear=False,
    )
    @patch('registration.payment_gateway.requests.post')
    def test_initiate_payment_uses_final_response_url_when_gateway_returns_html(self, mocked_post):
        success_response = Mock()
        success_response.status_code = 200
        success_response.url = 'https://devo.finpay.id/pg/payment/card/id/v2/access/html-token'
        success_response.headers = {}
        success_response.text = '<html><body>redirected</body></html>'
        success_response.json.side_effect = ValueError('not json')
        mocked_post.return_value = success_response

        redirect_url = initiate_payment(self.transaction, self.request, 'Paket Alumni')
        self.transaction.refresh_from_db()

        self.assertEqual(
            redirect_url,
            'https://devo.finpay.id/pg/payment/card/id/v2/access/html-token',
        )
        self.assertEqual(
            self.transaction.payment_redirect_url,
            'https://devo.finpay.id/pg/payment/card/id/v2/access/html-token',
        )

    @patch.dict(
        os.environ,
        {
            'PAYMENT_GATEWAY_API_KEY': 'api-key',
            'PAYMENT_GATEWAY_SIGNING_SECRET': 'secret',
            'PAYMENT_GATEWAY_BASE_URL': 'https://dev-payment.ui.ac.id',
            'PAYMENT_GATEWAY_FALLBACK_BASE_URL': '',
        },
        clear=False,
    )
    @patch('registration.payment_gateway.requests.post')
    def test_initiate_payment_extracts_url_embedded_in_json_text(self, mocked_post):
        success_response = Mock()
        success_response.status_code = 201
        success_response.json.return_value = {
            'success': True,
            'message': 'Open payment page https://devo.finpay.id/pg/payment/card/id/v2/access/embedded-token',
            'data': {
                'transaction_id': 'gateway-uuid',
                'status': 'initiated',
            },
        }
        mocked_post.return_value = success_response

        redirect_url = initiate_payment(self.transaction, self.request, 'Paket Alumni')
        self.transaction.refresh_from_db()

        self.assertEqual(
            redirect_url,
            'https://devo.finpay.id/pg/payment/card/id/v2/access/embedded-token',
        )
        self.assertEqual(
            self.transaction.payment_redirect_url,
            'https://devo.finpay.id/pg/payment/card/id/v2/access/embedded-token',
        )

    @patch.dict(
        os.environ,
        {
            'PAYMENT_GATEWAY_API_KEY': 'api-key',
            'PAYMENT_GATEWAY_SIGNING_SECRET': 'secret',
            'PAYMENT_GATEWAY_BASE_URL': 'https://dev-payment.ui.ac.id',
            'PAYMENT_GATEWAY_FALLBACK_BASE_URL': '',
        },
        clear=False,
    )
    @patch('registration.payment_gateway.requests.post')
    def test_initiate_payment_extracts_relative_location_redirect(self, mocked_post):
        success_response = Mock()
        success_response.status_code = 200
        success_response.url = 'https://dev-payment.ui.ac.id/api/v1/gateway/payments'
        success_response.headers = {'Location': '/pg/payment/card/id/v2/access/relative-token'}
        success_response.text = ''
        success_response.json.side_effect = ValueError('not json')
        mocked_post.return_value = success_response

        redirect_url = initiate_payment(self.transaction, self.request, 'Paket Alumni')
        self.transaction.refresh_from_db()

        self.assertEqual(
            redirect_url,
            'https://dev-payment.ui.ac.id/pg/payment/card/id/v2/access/relative-token',
        )
        self.assertEqual(
            self.transaction.payment_redirect_url,
            'https://dev-payment.ui.ac.id/pg/payment/card/id/v2/access/relative-token',
        )

    @patch.dict(
        os.environ,
        {
            'PAYMENT_GATEWAY_API_KEY': 'api-key',
            'PAYMENT_GATEWAY_SIGNING_SECRET': 'secret',
            'PAYMENT_GATEWAY_BASE_URL': 'https://dev-payment.ui.ac.id',
            'PAYMENT_GATEWAY_FALLBACK_BASE_URL': '',
        },
        clear=False,
    )
    @patch('registration.payment_gateway.requests.post')
    def test_initiate_payment_does_not_use_implicit_prod_fallback(self, mocked_post):
        mocked_post.side_effect = ConnectTimeout('primary timeout')

        with self.assertRaisesMessage(ValueError, 'https://dev-payment.ui.ac.id'):
            initiate_payment(self.transaction, self.request, 'Paket Alumni')

        self.assertEqual(mocked_post.call_count, 1)
        self.assertEqual(
            mocked_post.call_args_list[0].args[0],
            'https://dev-payment.ui.ac.id/api/v1/gateway/payments',
        )


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
            status='PENDING_PAYMENT',
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
