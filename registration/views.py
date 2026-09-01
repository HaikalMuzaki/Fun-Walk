import json
import logging
from decimal import Decimal
from urllib.parse import parse_qs
from xml.etree.ElementTree import ParseError

from cas import CASError
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from email_validator import EmailNotValidError, validate_email as validate_registration_email
from requests.exceptions import RequestException

from .models import Ticket, Transaction
from .payment_gateway import (
    apply_callback_payload,
    initiate_payment,
    is_terminal_local_status,
    refresh_transaction_status,
    verify_callback_status_if_needed,
)
from .sso_compat import ensure_django_six_compat

ensure_django_six_compat()

from django_sso_ui.utils import authenticate as sso_authenticate
from django_sso_ui.utils import get_cas_client, get_service_url

User = get_user_model()
logger = logging.getLogger(__name__)

PACKAGE_DETAILS = {
    'ALUMNI_PACK': {
        'label': 'Paket Umum',
        'template': 'registration/checkout-alumni.html',
    },
    'STUDENT_PACK': {
        'label': 'Paket Mahasiswa Aktif',
        'template': 'registration/checkout-mahasiswa.html',
    },
    'TICKET_ONLY': {
        'label': 'Non-Paket',
        'template': 'registration/checkout-non-paket.html',
    },
}

STATUS_DETAILS = {
    'PENDING_PAYMENT': {
        'class_name': 'status-waiting',
        'label': 'Menunggu Pembayaran',
    },
    'PENDING_CONFIRMATION': {
        'class_name': 'status-confirming',
        'label': 'Menunggu Konfirmasi',
    },
    'PAID': {
        'class_name': 'status-success',
        'label': 'Sukses',
    },
    'FAILED': {
        'class_name': 'status-failed',
        'label': 'Gagal',
    },
    'CANCELLED': {
        'class_name': 'status-cancelled',
        'label': 'Pesanan Dibatalkan',
    },
}

VALID_DEGREE_LEVELS = {'S1', 'S2', 'S3'}
STUDY_PROGRAM_CHOICES = {
    'ILMU_KOMPUTER': 'Ilmu Komputer',
    'SISTEM_INFORMASI': 'Sistem Informasi',
    'KECERDASAN_ARTIFISIAL': 'Kecerdasan Artifisial',
    'TEKNOLOGI_INFORMASI': 'Teknologi Informasi',
}
VALID_TSHIRT_SIZES = {'XS', 'S', 'M', 'L', 'XL', '3XL'}


def _format_rupiah(amount):
    return f"Rp{int(amount):,}".replace(",", ".")


def _is_student_sso_user(user):
    return getattr(user, 'user_type', '') == 'STUDENT'


def _get_sso_student_cohort_year(user):
    """Derive the admission year from the NPM supplied by SSO UI."""
    npm = ''.join(character for character in (getattr(user, 'npm', '') or '') if character.isdigit())
    if len(npm) < 2:
        return None

    cohort_year = 2000 + int(npm[:2])
    if 2023 <= cohort_year <= 2026:
        return cohort_year
    return None


def _get_password_requirement_errors(password):
    errors = []

    if len(password) < 8:
        errors.append('minimal 8 karakter')
    if not any(character.islower() for character in password):
        errors.append('huruf kecil')
    if not any(character.isupper() for character in password):
        errors.append('huruf kapital')
    if not any(character.isdigit() for character in password):
        errors.append('angka')
    if not any(not character.isalnum() for character in password):
        errors.append('simbol seperti !, @, #, (, ), ,, :, atau .')

    return errors


def _normalize_whatsapp_number(value):
    normalized_value = ''.join(character for character in (value or '').strip() if character.isdigit())
    if not normalized_value:
        raise ValueError('Nomor WhatsApp wajib diisi.')
    return normalized_value


def _parse_cohort_year(value):
    normalized_value = (value or '').strip()
    if not normalized_value:
        return None

    try:
        return int(normalized_value)
    except (TypeError, ValueError):
        raise ValueError('Tahun angkatan tidak valid.')


def _parse_degree_level(value):
    normalized_value = (value or '').strip().upper()
    if normalized_value not in VALID_DEGREE_LEVELS:
        raise ValueError('Jenjang wajib dipilih dari S1, S2, atau S3.')
    return normalized_value


def _parse_study_program(value):
    normalized_value = (value or '').strip().upper().replace(' ', '_')
    label_to_code = {
        label.upper().replace(' ', '_'): code
        for code, label in STUDY_PROGRAM_CHOICES.items()
    }
    normalized_value = label_to_code.get(normalized_value, normalized_value)
    if normalized_value not in STUDY_PROGRAM_CHOICES:
        raise ValueError('Program studi wajib dipilih dari opsi yang tersedia.')
    return normalized_value


def _get_first_non_empty_value(data, *keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value:
            return value
    return ''


def _get_transaction_by_reference(reference):
    normalized_reference = (reference or '').strip()
    if not normalized_reference:
        raise Transaction.DoesNotExist

    transaction_obj = Transaction.objects.filter(idempotency_key=normalized_reference).first()
    if transaction_obj is not None:
        return transaction_obj

    transaction_obj = Transaction.objects.filter(transaction_id=normalized_reference).first()
    if transaction_obj is not None:
        return transaction_obj

    raise Transaction.DoesNotExist


def _get_email_registration_error(email):
    normalized_email = email.strip().lower()
    if not normalized_email.endswith('@gmail.com'):
        return 'Email registrasi harus menggunakan akun Gmail (@gmail.com).', ''

    try:
        validated_email = validate_registration_email(
            normalized_email,
            check_deliverability=True,
            test_environment=False,
        )
    except EmailNotValidError:
        return 'Email Gmail tidak valid atau tidak dapat menerima email.', ''

    if not validated_email.normalized.endswith('@gmail.com'):
        return 'Email registrasi harus menggunakan akun Gmail (@gmail.com).', ''

    return '', validated_email.normalized


def _build_sso_identity(sso_profile):
    attributes = sso_profile.get('attributes') or {}
    username = (sso_profile.get('username') or '').strip().lower()
    if not username:
        raise ValueError('Profil SSO UI tidak valid.')

    email = _get_first_non_empty_value(attributes, 'email', 'mail', 'ui_email')
    email = (email or (username if '@' in username else f'{username}@ui.ac.id')).strip().lower()

    npm = _get_first_non_empty_value(
        attributes,
        'npm',
        'student_id',
        'studentid',
        'kode_identitas',
        'nomor_induk',
    )
    if not npm and username.isdigit():
        npm = username

    full_name = _get_first_non_empty_value(attributes, 'nama', 'displayName', 'cn', 'name')

    return {
        'username': username,
        'email': email,
        'npm': npm,
        'full_name': full_name,
    }


def _sync_sso_student_user(sso_profile):
    identity = _build_sso_identity(sso_profile)
    username = identity['username']
    email = identity['email']

    user = User.objects.filter(username__iexact=username).first()
    if user is None:
        user = User.objects.filter(email__iexact=email).first()

    created = user is None
    if created:
        user = User.objects.create_user(
            username=username,
            email=email,
            user_type='STUDENT',
            npm=identity['npm'] or None,
        )
        user.set_unusable_password()
        update_fields = ['password']
    else:
        update_fields = []

    if user.email.lower() != email:
        user.email = email
        update_fields.append('email')
    if user.user_type != 'STUDENT':
        user.user_type = 'STUDENT'
        update_fields.append('user_type')
    if identity['npm'] and user.npm != identity['npm']:
        user.npm = identity['npm']
        update_fields.append('npm')

    if identity['full_name']:
        name_parts = identity['full_name'].split(None, 1)
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ''
        if user.first_name != first_name:
            user.first_name = first_name
            update_fields.append('first_name')
        if user.last_name != last_name:
            user.last_name = last_name
            update_fields.append('last_name')

    if update_fields:
        user.save(update_fields=list(dict.fromkeys(update_fields)))

    return user


def _complete_sso_login(request, sso_profile):
    if not sso_profile:
        messages.error(request, 'Login SSO UI gagal. Silakan coba lagi.')
        return redirect('login')

    try:
        user = _sync_sso_student_user(sso_profile)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect('login')

    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    next_url = request.session.pop('sso_next_url', '')
    return redirect(next_url or 'index')


def _build_history_items(user):
    history_items = []
    transactions = (
        Transaction.objects.filter(user=user)
        .prefetch_related('tickets')
        .order_by('-created_at')
    )

    for transaction_obj in transactions:
        tickets = list(transaction_obj.tickets.all())
        if not tickets:
            continue

        first_ticket = tickets[0]
        tshirt_sizes = [
            ticket.tshirt_size
            for ticket in tickets
            if ticket.tshirt_size and ticket.tshirt_size != 'NONE'
        ]
        status = STATUS_DETAILS.get(transaction_obj.status, STATUS_DETAILS['PENDING_PAYMENT'])

        history_items.append({
            'transaction_id': transaction_obj.id,
            'raw_status': transaction_obj.status,
            'package_name': PACKAGE_DETAILS[first_ticket.package_type]['label'],
            'status_class': status['class_name'],
            'status_label': status['label'],
            'attendee_name': first_ticket.attendee_name,
            'ticket_quantity': len(tickets),
            'tshirt_sizes_list': tshirt_sizes,
            'tshirt_sizes': ', '.join(tshirt_sizes),
            'has_tshirt_sizes': bool(tshirt_sizes),
            'created_at': timezone.localtime(transaction_obj.created_at).strftime('%d/%m/%Y %H:%M:%S'),
            'total_amount': _format_rupiah(transaction_obj.total_amount),
        })

    return history_items


def _sync_pending_transactions_for_user(user):
    pending_transactions = (
        Transaction.objects.filter(
            user=user,
            status__in=['PENDING_PAYMENT', 'PENDING_CONFIRMATION'],
        )
        .exclude(gateway_transaction_id='')
        .order_by('-created_at')
    )

    for transaction_obj in pending_transactions:
        try:
            refresh_transaction_status(transaction_obj)
        except ValueError as error:
            logger.warning(
                'Sinkronisasi status gateway gagal untuk %s: %s',
                transaction_obj.transaction_id,
                error,
            )


def _create_checkout_transaction(request, package_type, *, cohort_year_override=None):
    first_name = (request.POST.get('first_name') or '').strip()
    last_name = (request.POST.get('last_name') or '').strip()
    whatsapp_number = _normalize_whatsapp_number(request.POST.get('whatsapp_number'))
    cohort_year = (
        cohort_year_override
        if cohort_year_override is not None
        else _parse_cohort_year(request.POST.get('cohort_year'))
    )
    degree_level = _parse_degree_level(request.POST.get('degree_level'))
    study_program = _parse_study_program(request.POST.get('study_program'))
    try:
        quantity = int(request.POST.get('ticket_quantity') or 1)
    except (TypeError, ValueError):
        raise ValueError('Jumlah tiket tidak valid.')
    quantity = max(1, min(5, quantity))

    if not first_name:
        raise ValueError('First name wajib diisi.')
    if not last_name:
        raise ValueError('Last name wajib diisi.')
    if cohort_year is None:
        raise ValueError('Tahun angkatan wajib diisi.')
    if package_type == 'TICKET_ONLY' and not 1985 <= cohort_year <= 2026:
        raise ValueError('Tahun angkatan Non-Paket harus antara 1985 dan 2026.')
    if package_type == 'STUDENT_PACK' and not 2023 <= cohort_year <= 2026:
        raise ValueError('Paket Mahasiswa Aktif hanya tersedia untuk angkatan 2023 sampai 2026.')

    tshirt_sizes = []
    if package_type != 'TICKET_ONLY':
        for index in range(1, quantity + 1):
            size = (request.POST.get(f'shirt_size_{index}') or 'M').strip().upper()
            if size not in VALID_TSHIRT_SIZES:
                raise ValueError(f'Ukuran Kaos {index} tidak valid.')
            tshirt_sizes.append(size)

    with db_transaction.atomic():
        transaction_obj = Transaction.objects.create(
            user=request.user,
            status='PENDING_PAYMENT',
            whatsapp_number=whatsapp_number,
            cohort_year=cohort_year,
            degree_level=degree_level,
            study_program=study_program,
            total_amount=Decimal('0'),
        )

        total_amount = Decimal('0')
        for index in range(quantity):
            if package_type == 'ALUMNI_PACK':
                price = Decimal('250000')
                tshirt_size = tshirt_sizes[index]
            elif package_type == 'STUDENT_PACK':
                price = Decimal('150000') if index == 0 else Decimal('250000')
                tshirt_size = tshirt_sizes[index]
            else:
                price = Decimal('50000')
                tshirt_size = 'NONE'

            ticket = Ticket(
                transaction=transaction_obj,
                first_name=first_name,
                last_name=last_name,
                package_type=package_type,
                tshirt_size=tshirt_size,
                price=price,
            )
            ticket.save()
            total_amount += price

        transaction_obj.total_amount = total_amount
        transaction_obj.save(update_fields=['total_amount'])

    return transaction_obj


def index(request):
    has_bought_student_pack = False

    if request.user.is_authenticated and _is_student_sso_user(request.user):
        has_bought_student_pack = Ticket.objects.filter(
            transaction__user=request.user,
            transaction__status='PAID',
            package_type='STUDENT_PACK',
        ).exists()

    return render(
        request,
        'registration/index.html',
        {'has_bought_student_pack': has_bought_student_pack},
    )


def _get_safe_next_url(request):
    next_url = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if next_url and url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return ''


def login_view(request):
    if request.user.is_authenticated:
        return redirect('index')

    next_url = _get_safe_next_url(request)

    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip()
        password = request.POST.get('password')

        if not email or not password:
            messages.error(request, 'Email dan password wajib diisi.')
            return render(request, 'registration/login.html', {'next_url': next_url})

        user_exists = User.objects.filter(email__iexact=email).order_by('id').first()
        if user_exists is not None:
            if not user_exists.has_usable_password():
                messages.error(
                    request,
                    'Email ini sudah terhubung ke akun SSO UI. Silakan login dengan Continue with SSO.',
                )
                return render(request, 'registration/login.html', {'next_url': next_url})

            auth_user = authenticate(request, username=user_exists.username, password=password)
            if auth_user is not None:
                login(request, auth_user)
                return redirect(next_url or 'index')

            messages.error(request, 'Email sudah terdaftar, tetapi password salah.')
            return render(request, 'registration/login.html', {'next_url': next_url})

        email_error, normalized_email = _get_email_registration_error(email)
        if email_error:
            messages.error(request, email_error)
            return render(request, 'registration/login.html', {'next_url': next_url})

        password_errors = _get_password_requirement_errors(password)
        if password_errors:
            messages.error(
                request,
                'Password untuk akun baru harus mengandung ' + ', '.join(password_errors) + '.',
            )
            return render(request, 'registration/login.html', {'next_url': next_url})

        User.objects.create_user(
            username=normalized_email,
            email=normalized_email,
            password=password,
            user_type='ALUMNI',
        )
        auth_user = authenticate(request, username=normalized_email, password=password)
        if auth_user is not None:
            login(request, auth_user)
            return redirect(next_url or 'index')

    return render(request, 'registration/login.html', {'next_url': next_url})


def sso_login(request):
    if request.user.is_authenticated:
        return redirect(_get_safe_next_url(request) or 'index')

    next_url = _get_safe_next_url(request)
    if next_url:
        request.session['sso_next_url'] = next_url
    else:
        request.session.pop('sso_next_url', None)

    return redirect(reverse('sso_login_callback'))


def sso_login_callback(request):
    service_url = get_service_url(request)
    client = get_cas_client(service_url, request=request)
    renew_client = get_cas_client(service_url, request=request)
    renew_client.renew = True
    login_url = renew_client.get_login_url()
    ticket = request.GET.get('ticket')

    if not ticket:
        logger.info('SSO callback tanpa ticket, redirect ke CAS login lagi. service_url=%s', service_url)
        return redirect(login_url)

    try:
        sso_profile = sso_authenticate(ticket, client)
    except ParseError:
        logger.warning(
            'SSO callback gagal parse response CAS2. service_url=%s ticket=%s',
            service_url,
            ticket,
        )
        messages.error(
            request,
            'Respons verifikasi dari SSO UI tidak valid. Pastikan callback SSO sudah lewat domain yang didaftarkan dan reverse proxy HTTPS sudah aktif.',
        )
        return redirect('login')
    except (RequestException, CASError):
        logger.warning(
            'SSO callback gagal verifikasi ke CAS2. service_url=%s ticket=%s',
            service_url,
            ticket,
        )
        messages.error(
            request,
            'Koneksi ke server SSO UI gagal atau respons CAS2 tidak valid.',
        )
        return redirect('login')

    logger.info(
        'SSO callback berhasil diverifikasi. service_url=%s username=%s',
        service_url,
        (sso_profile or {}).get('username'),
    )
    return _complete_sso_login(request, sso_profile)


@login_required
def checkout_alumni(request):
    if request.method == 'POST':
        try:
            _create_checkout_transaction(request, 'ALUMNI_PACK')
            messages.success(request, 'Pesanan tersimpan. Silakan lanjutkan pembayaran melalui halaman History.')
            return redirect('history')
        except ValueError as error:
            messages.error(request, str(error))
    return render(request, 'registration/checkout-alumni.html')


@login_required
def checkout_mahasiswa(request):
    if not _is_student_sso_user(request.user):
        messages.error(
            request,
            'Paket Mahasiswa Aktif hanya dapat dibeli oleh akun yang login melalui SSO UI.',
        )
        return redirect('index')

    cohort_year = _get_sso_student_cohort_year(request.user)
    if cohort_year is None:
        messages.error(
            request,
            'Angkatan tidak dapat diambil dari data SSO. Pastikan NPM SSO Anda valid.',
        )
        return redirect('index')

    existing_student_tickets = Ticket.objects.filter(
        transaction__user=request.user,
        package_type='STUDENT_PACK',
    ).select_related('transaction')

    for ticket in existing_student_tickets:
        if ticket.transaction.status in {'PENDING_PAYMENT', 'PENDING_CONFIRMATION'}:
            messages.warning(request, 'Silakan selesaikan pembayaran tiket mahasiswa Anda sebelumnya di sini.')
            return redirect('history')
        if ticket.transaction.status == 'PAID':
            messages.error(request, 'Anda telah menggunakan special offer ini.')
            return redirect('index')

    if request.method == 'POST':
        try:
            _create_checkout_transaction(
                request,
                'STUDENT_PACK',
                cohort_year_override=cohort_year,
            )
            messages.success(request, 'Pesanan tersimpan. Silakan lanjutkan pembayaran melalui halaman History.')
            return redirect('history')
        except ValueError as error:
            messages.error(request, str(error))

    return render(
        request,
        'registration/checkout-mahasiswa.html',
        {'cohort_year': cohort_year},
    )


@login_required
def checkout_non_paket(request):
    if request.method == 'POST':
        try:
            _create_checkout_transaction(request, 'TICKET_ONLY')
            messages.success(request, 'Pesanan tersimpan. Silakan lanjutkan pembayaran melalui halaman History.')
            return redirect('history')
        except ValueError as error:
            messages.error(request, str(error))
    return render(
        request,
        'registration/checkout-non-paket.html',
        {'cohort_years': range(1985, 2027)},
    )


@login_required
def history(request):
    _sync_pending_transactions_for_user(request.user)
    return render(
        request,
        'registration/history.html',
        {'history_items': _build_history_items(request.user)},
    )


def custom_logout(request):
    logout(request)
    return redirect('index')


@login_required
def payment_page(request):
    transaction_reference = (request.GET.get('trx') or '').strip()
    gateway_return = (request.GET.get('gateway_return') or '').strip().lower()
    if not transaction_reference:
        return redirect('history')

    try:
        transaction_obj = _get_transaction_by_reference(transaction_reference)
    except Transaction.DoesNotExist:
        messages.error(request, 'Transaksi pembayaran tidak ditemukan.')
        return redirect('history')

    if transaction_obj.user_id != request.user.id:
        messages.error(request, 'Anda tidak memiliki akses ke transaksi ini.')
        return redirect('history')

    if transaction_obj.gateway_transaction_id:
        try:
            refresh_transaction_status(transaction_obj)
        except ValueError as error:
            logger.warning(
                'Refresh status payment page gagal untuk %s: %s',
                transaction_obj.transaction_id,
                error,
            )

    if transaction_obj.status == 'PAID':
        messages.success(request, 'Pembayaran berhasil dikonfirmasi.')
        return redirect('history')

    if transaction_obj.status in {'FAILED', 'CANCELLED'}:
        messages.error(request, 'Pembayaran tidak berhasil. Silakan coba lagi.')
        return redirect('history')

    if gateway_return:
        messages.warning(request, 'Status pembayaran masih menunggu konfirmasi gateway.')
        return redirect('history')

    if transaction_obj.payment_redirect_url:
        return redirect(transaction_obj.payment_redirect_url)

    first_ticket = transaction_obj.tickets.order_by('id').first()
    if first_ticket is None:
        messages.error(request, 'Transaksi ini belum memiliki tiket yang valid.')
        return redirect('history')

    try:
        package_label = PACKAGE_DETAILS[first_ticket.package_type]['label']
        finpay_url = initiate_payment(transaction_obj, request, package_label)
        if transaction_obj.status == 'PENDING_PAYMENT':
            transaction_obj.status = 'PENDING_CONFIRMATION'
            transaction_obj.failed_at = None
            transaction_obj.save(update_fields=['status', 'failed_at'])
        return redirect(finpay_url)
    except ValueError as error:
        messages.error(request, f'Gagal membuat link pembayaran: {str(error)}')
        return redirect('history')

    messages.error(request, 'Link pembayaran tidak tersedia. Silakan mulai lagi dari History.')
    return redirect('history')


@login_required
def retry_payment(request, transaction_id):
    if request.method == 'POST':
        try:
            transaction_obj = Transaction.objects.get(
                id=transaction_id,
                user=request.user,
                status__in=['PENDING_PAYMENT', 'PENDING_CONFIRMATION'],
            )

            if transaction_obj.payment_redirect_url:
                if transaction_obj.status == 'PENDING_PAYMENT':
                    transaction_obj.status = 'PENDING_CONFIRMATION'
                    transaction_obj.save(update_fields=['status'])
                return redirect(transaction_obj.payment_redirect_url)

            if transaction_obj.status == 'PENDING_CONFIRMATION':
                messages.error(request, 'Link pembayaran sebelumnya tidak tersedia. Silakan hubungi panitia.')
                return redirect('history')

            first_ticket = transaction_obj.tickets.order_by('id').first()
            if first_ticket is None:
                messages.error(request, 'Transaksi ini belum memiliki tiket yang valid.')
                return redirect('history')

            # Retry untuk transaksi lama yang tidak pernah menyimpan redirect URL
            # harus memakai idempotency key baru agar gateway membuat payment page baru.
            transaction_obj.rotate_idempotency_key()
            transaction_obj.gateway_transaction_id = ''
            transaction_obj.gateway_status = ''
            transaction_obj.gateway_response_payload = None
            transaction_obj.gateway_callback_payload = None
            transaction_obj.save(
                update_fields=[
                    'idempotency_key',
                    'gateway_transaction_id',
                    'gateway_status',
                    'gateway_response_payload',
                    'gateway_callback_payload',
                ]
            )

            package_label = PACKAGE_DETAILS[first_ticket.package_type]['label']
            finpay_url = initiate_payment(transaction_obj, request, package_label)
            if transaction_obj.status == 'PENDING_PAYMENT':
                transaction_obj.status = 'PENDING_CONFIRMATION'
                transaction_obj.failed_at = None
                transaction_obj.save(update_fields=['status', 'failed_at'])
            return redirect(finpay_url)

        except Transaction.DoesNotExist:
            messages.error(request, 'Transaksi tidak ditemukan atau status pembayaran sudah selesai.')
            return redirect('history')
        except ValueError as error:
            messages.error(request, f'Gagal membuat link pembayaran: {str(error)}')
            return redirect('history')

    return redirect('history')


@csrf_exempt
@require_POST
def payment_callback(request):
    try:
        payload = {}
        if request.content_type and 'application/json' in request.content_type:
            payload = json.loads(request.body)
        elif request.POST:
            payload = request.POST.dict()
        elif request.body:
            payload = {
                key: values[-1]
                for key, values in parse_qs(request.body.decode('utf-8', errors='ignore')).items()
            }

        if not payload:
            return JsonResponse({'error': 'Payload callback kosong'}, status=400)

        if len(payload) == 1:
            nested_payload = payload.get('payload') or payload.get('data')
            if isinstance(nested_payload, str):
                try:
                    payload = json.loads(nested_payload)
                except json.JSONDecodeError:
                    pass

        payload_data = payload.get('data') if isinstance(payload.get('data'), dict) else {}
        order_id = (
            payload.get('idempotency_key')
            or payload.get('order_id')
            or payload_data.get('idempotency_key')
            or payload_data.get('order_id')
        )
        status = payload.get('status') or payload_data.get('status')

        if not order_id or not status:
            return JsonResponse({'error': 'Payload tidak lengkap'}, status=400)

        try:
            transaction_obj = _get_transaction_by_reference(order_id)
        except Transaction.DoesNotExist:
            return JsonResponse({'error': 'Transaksi tidak ditemukan'}, status=404)

        next_status = status.strip().lower()
        current_gateway_status = transaction_obj.gateway_status.strip().lower() if transaction_obj.gateway_status else ''
        if current_gateway_status == next_status:
            return JsonResponse({'message': 'Status transaksi sudah sesuai.'}, status=200)
        if is_terminal_local_status(transaction_obj.status):
            return JsonResponse({'message': 'Transaksi sudah berada di status final sebelumnya.'}, status=200)

        apply_callback_payload(transaction_obj, payload)
        verify_callback_status_if_needed(transaction_obj)

        return JsonResponse({'message': 'Callback berhasil diproses'}, status=200)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Format JSON tidak valid'}, status=400)
    except Exception as error:
        return JsonResponse({'error': str(error)}, status=500)


@login_required
def manage_ticket(request):
    if request.method == 'POST':
        transaction_id = request.POST.get('transaction_id')
        action = request.POST.get('action')

        try:
            transaction_obj = Transaction.objects.get(
                id=transaction_id,
                user=request.user,
                status='PENDING_PAYMENT',
            )

            if action == 'cancel':
                transaction_obj.status = 'CANCELLED'
                transaction_obj.save(update_fields=['status'])
                messages.success(request, 'Pesanan berhasil dibatalkan. Kuota promo SSO Anda telah di-reset.')

            elif action == 'update':
                sizes_list = [size.strip().upper() for size in request.POST.getlist('new_sizes') if size.strip()]
                if not sizes_list:
                    new_sizes_raw = request.POST.get('new_sizes', '')
                    sizes_list = [size.strip().upper() for size in new_sizes_raw.split(',') if size.strip()]
                tickets = transaction_obj.tickets.all().order_by('id')

                if not tickets or tickets[0].package_type == 'TICKET_ONLY':
                    messages.error(request, 'Paket ini tidak termasuk kaos, tidak ada ukuran yang bisa diubah.')
                    return redirect('history')

                if len(sizes_list) != tickets.count():
                    messages.error(
                        request,
                        f'Gagal update. Jumlah ukuran kaos yang dimasukkan ({len(sizes_list)}) tidak sesuai dengan jumlah tiket Anda ({tickets.count()}).',
                    )
                    return redirect('history')

                invalid_sizes = [size for size in sizes_list if size not in VALID_TSHIRT_SIZES]
                if invalid_sizes:
                    messages.error(
                        request,
                        f"Ukuran tidak valid: {', '.join(invalid_sizes)}. Gunakan hanya: XS, S, M, L, XL, 3XL",
                    )
                    return redirect('history')

                for index, ticket in enumerate(tickets):
                    ticket.tshirt_size = sizes_list[index]
                    ticket.save(update_fields=['tshirt_size'])

                messages.success(request, 'Ukuran kaos berhasil diperbarui!')

        except Transaction.DoesNotExist:
            messages.error(request, 'Aksi ditolak: Transaksi tidak ditemukan atau sudah dilanjutkan ke pembayaran.')

    return redirect('history')
