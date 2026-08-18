from decimal import Decimal
from xml.etree.ElementTree import ParseError

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from email_validator import EmailNotValidError, validate_email as validate_registration_email
from requests.exceptions import RequestException

from .models import Ticket, Transaction
from .sso_compat import ensure_django_six_compat

ensure_django_six_compat()

from cas import CASError
from django_sso_ui.utils import (
    authenticate as sso_authenticate,
    get_cas_client,
    get_service_url,
)

User = get_user_model()

PACKAGE_DETAILS = {
    'ALUMNI_PACK': {
        'label': 'Paket Alumni',
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
    'PENDING': {
        'class_name': 'status-waiting',
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
}

VALID_TSHIRT_SIZES = {'XS', 'S', 'M', 'L', 'XL', 'XXL+'}


def _format_rupiah(amount):
    return f"Rp{int(amount):,}".replace(",", ".")


def _is_student_sso_user(user):
    return getattr(user, 'user_type', '') == 'STUDENT'


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


def _get_first_non_empty_value(data, *keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, str):
            value = value.strip()
        if value:
            return value
    return ''


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
        status = STATUS_DETAILS.get(transaction_obj.status, STATUS_DETAILS['PENDING'])

        history_items.append({
            'package_name': PACKAGE_DETAILS[first_ticket.package_type]['label'],
            'status_class': status['class_name'],
            'status_label': status['label'],
            'attendee_name': first_ticket.attendee_name,
            'ticket_quantity': len(tickets),
            'tshirt_sizes': ', '.join(tshirt_sizes),
            'has_tshirt_sizes': bool(tshirt_sizes),
            'created_at': timezone.localtime(transaction_obj.created_at).strftime('%d/%m/%Y %H:%M:%S'),
            'total_amount': _format_rupiah(transaction_obj.total_amount),
        })

    return history_items


def _create_checkout_transaction(request, package_type):
    full_name = (request.POST.get('full_name') or '').strip()
    try:
        quantity = int(request.POST.get('ticket_quantity') or 1)
    except (TypeError, ValueError):
        raise ValueError('Jumlah tiket tidak valid.')
    quantity = max(1, min(5, quantity))

    if not full_name:
        raise ValueError('Nama Lengkap wajib diisi.')

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
            status='PENDING',
            total_amount=Decimal('0'),
        )

        total_amount = Decimal('0')
        for index in range(quantity):
            if package_type == 'ALUMNI_PACK':
                price = Decimal('275000')
                tshirt_size = tshirt_sizes[index]
            elif package_type == 'STUDENT_PACK':
                price = Decimal('175000') if index == 0 else Decimal('275000')
                tshirt_size = tshirt_sizes[index]
            else:
                price = Decimal('50000')
                tshirt_size = 'NONE'

            ticket = Ticket(
                transaction=transaction_obj,
                attendee_name=full_name,
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
    return render(request, 'registration/index.html')

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
    # Kalau sudah login, langsung lempar ke halaman depan
    if request.user.is_authenticated:
        return redirect('index')

    next_url = _get_safe_next_url(request)

    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip()
        password = request.POST.get('password')

        if not email or not password:
            messages.error(request, "Email dan password wajib diisi.")
            return render(request, 'registration/login.html', {'next_url': next_url})

        user_exists = User.objects.filter(email__iexact=email).order_by('id').first()
        if user_exists is not None:
            if not user_exists.has_usable_password():
                messages.error(
                    request,
                    "Email ini sudah terhubung ke akun SSO UI. Silakan login dengan Continue with SSO.",
                )
                return render(request, 'registration/login.html', {'next_url': next_url})

            auth_user = authenticate(request, username=user_exists.username, password=password)
            if auth_user is not None:
                login(request, auth_user)
                return redirect(next_url or 'index')

            messages.error(request, "Email sudah terdaftar, tetapi password salah.")
            return render(request, 'registration/login.html', {'next_url': next_url})

        # Skenario 2: Email belum ada -> Proses Register sekaligus Login
        email_error, normalized_email = _get_email_registration_error(email)
        if email_error:
            messages.error(request, email_error)
            return render(request, 'registration/login.html', {'next_url': next_url})

        password_errors = _get_password_requirement_errors(password)
        if password_errors:
            messages.error(
                request,
                "Password untuk akun baru harus mengandung "
                + ", ".join(password_errors)
                + ".",
            )
            return render(request, 'registration/login.html', {'next_url': next_url})

        new_user = User.objects.create_user(
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
    client = get_cas_client(service_url)
    login_url = client.get_login_url()
    ticket = request.GET.get('ticket')

    if not ticket:
        return redirect(login_url)

    try:
        sso_profile = sso_authenticate(ticket, client)
    except ParseError:
        messages.error(
            request,
            'Respons verifikasi dari SSO UI tidak valid. Biasanya ini terjadi karena callback localhost belum didukung oleh SSO UI.',
        )
        return redirect('login')
    except (RequestException, CASError):
        messages.error(
            request,
            'Koneksi ke server SSO UI gagal atau respons CAS2 tidak valid. Jika ini masih di localhost, kemungkinan callback belum didukung.',
        )
        return redirect('login')

    return _complete_sso_login(request, sso_profile)

@login_required
def checkout_alumni(request):
    if request.method == 'POST':
        try:
            _create_checkout_transaction(request, 'ALUMNI_PACK')
            return redirect('history')
        except ValueError as error:
            messages.error(request, str(error))
    return render(request, 'registration/checkout-alumni.html')

@login_required
def checkout_mahasiswa(request):
    if not _is_student_sso_user(request.user):
        messages.error(
            request,
            "Paket Mahasiswa Aktif hanya dapat dibeli oleh akun yang login melalui SSO UI.",
        )
        return redirect('index')
    if request.method == 'POST':
        try:
            _create_checkout_transaction(request, 'STUDENT_PACK')
            return redirect('history')
        except ValueError as error:
            messages.error(request, str(error))
    return render(request, 'registration/checkout-mahasiswa.html')

@login_required
def checkout_non_paket(request):
    if request.method == 'POST':
        try:
            _create_checkout_transaction(request, 'TICKET_ONLY')
            return redirect('history')
        except ValueError as error:
            messages.error(request, str(error))
    return render(request, 'registration/checkout-non-paket.html')

@login_required
def history(request):
    return render(
        request,
        'registration/history.html',
        {'history_items': _build_history_items(request.user)},
    )

def custom_logout(request):
    logout(request) # Menghapus sesi user
    return redirect('index')
