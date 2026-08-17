from decimal import Decimal

from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction as db_transaction
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from email_validator import EmailNotValidError, validate_email as validate_registration_email

from .models import Ticket, Transaction

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

        try:
            # Skenario 1: Email sudah terdaftar -> Proses Login
            user_exists = User.objects.get(email__iexact=email)
            # Karena Django by default mencari 'username', kita passing email ke parameter username
            auth_user = authenticate(request, username=user_exists.username, password=password) 
            
            if auth_user is not None:
                login(request, auth_user)
                return redirect(next_url or 'index')
            else:
                messages.error(request, "Password salah. Silakan coba lagi.")
                
        except User.DoesNotExist:
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
            # Kita 'akali' requirement Django dengan memasukkan email ke kolom username juga
            new_user = User.objects.create_user(
                username=normalized_email,
                email=normalized_email,
                password=password,
                user_type='ALUMNI',
            )
            auth_user = authenticate(request, username=normalized_email, password=password)
            login(request, auth_user)
            return redirect(next_url or 'index')

    return render(request, 'registration/login.html', {'next_url': next_url})

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

def mock_sso_login(request):
    """
    Fungsi tiruan (Mock) untuk bypass SSO UI selama Localhost belum di-whitelist.
    """
    # 1. Tentukan identitas akun dummy
    dummy_username = "mahasiswa.dummy"
    dummy_email = "mahasiswa.dummy@ui.ac.id"
    
    # 2. Cari atau buat user otomatis di database lokal
    user, created = User.objects.get_or_create(
        username=dummy_username,
        defaults={
            'email': dummy_email,
            'user_type': 'STUDENT',
            'npm': '2400000000',
        }
    )
    
    # Kalau user baru dibuat, set passwordnya agar tidak bisa dipakai login manual biasa
    if created:
        user.set_unusable_password()
        user.save()
    elif user.user_type != 'STUDENT':
        user.user_type = 'STUDENT'
        if not user.npm:
            user.npm = '2400000000'
        user.save(update_fields=['user_type', 'npm'])
        
    # 3. Paksa login tanpa password (bypass)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    
    # 4. Ambil parameter 'next' dari URL (contoh: /checkout/alumni/). 
    # Kalau tidak ada, default lemparkan ke halaman utama ('/')
    next_url = request.GET.get('next', '/')
    return redirect(next_url)
