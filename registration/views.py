import logging
import json
import time
import hmac
import hashlib
import requests
from decimal import Decimal
from xml.etree.ElementTree import ParseError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
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
import random

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
logger = logging.getLogger(__name__)

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
    whatsapp_number = _normalize_whatsapp_number(request.POST.get('whatsapp_number'))
    cohort_year = _parse_cohort_year(request.POST.get('cohort_year'))
    try:
        quantity = int(request.POST.get('ticket_quantity') or 1)
    except (TypeError, ValueError):
        raise ValueError('Jumlah tiket tidak valid.')
    quantity = max(1, min(5, quantity))

    if not full_name:
        raise ValueError('Nama Lengkap wajib diisi.')
    if cohort_year is None:
        raise ValueError('Tahun angkatan wajib diisi.')

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
            whatsapp_number=whatsapp_number,
            cohort_year=cohort_year,
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
    has_bought_student_pack = False
    
    # Cek apakah user udah login dan dia mahasiswa
    if request.user.is_authenticated and _is_student_sso_user(request.user):
        # Cek apakah udah punya tiket mahasiswa yang PAID
        has_bought_student_pack = Ticket.objects.filter(
            transaction__user=request.user,
            transaction__status='PAID',
            package_type='STUDENT_PACK'
        ).exists()
        
    return render(request, 'registration/index.html', {
        'has_bought_student_pack': has_bought_student_pack
    })

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
            'Koneksi ke server SSO UI gagal atau callback belum sesuai domain HTTPS yang didaftarkan ke SSO UI.',
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
            # 1. Simpan data ke database
            transaction_obj = _create_checkout_transaction(request, 'ALUMNI_PACK')
            
            # 2. Tembak API Payment Gateway UI untuk dapat link Finpay
            finpay_url = initiate_finpay_payment(transaction_obj, request)
            
            # 3. Lempar user ke halaman Finpay
            return redirect(finpay_url)
        except ValueError as error:
            # Menangkap error validasi form atau error dari API Gateway
            messages.error(request, str(error))
    return render(request, 'registration/checkout-alumni.html')


@login_required
def checkout_mahasiswa(request):
    # Validasi 1: Harus akun SSO
    if not _is_student_sso_user(request.user):
        messages.error(
            request,
            "Paket Mahasiswa Aktif hanya dapat dibeli oleh akun yang login melalui SSO UI."
        )
        return redirect('index')

    # Validasi 2: Query langsung ke tabel Ticket (Pasti terbaca oleh Django)
    existing_student_tickets = Ticket.objects.filter(
        transaction__user=request.user,
        package_type='STUDENT_PACK'
    ).select_related('transaction')

    # Cek status transaksinya
    for ticket in existing_student_tickets:
        if ticket.transaction.status == 'PENDING':
            messages.warning(request, "Silakan selesaikan pembayaran tiket mahasiswa Anda sebelumnya di sini.")
            return redirect('history')
        elif ticket.transaction.status == 'PAID':
            messages.error(request, "Anda telah menggunakan special offer ini.")
            return redirect('index')

    # Proses form jika validasi lolos
    if request.method == 'POST':
        try:
            # 1. Simpan data ke database
            transaction_obj = _create_checkout_transaction(request, 'STUDENT_PACK')
            
            # 2. Tembak API Payment Gateway UI untuk dapat link Finpay
            finpay_url = initiate_finpay_payment(transaction_obj, request)
            
            # 3. Lempar user ke halaman Finpay
            return redirect(finpay_url)
        except ValueError as error:
            messages.error(request, str(error))
            
    return render(request, 'registration/checkout-mahasiswa.html')


@login_required
def checkout_non_paket(request):
    if request.method == 'POST':
        try:
            # 1. Simpan data ke database
            transaction_obj = _create_checkout_transaction(request, 'TICKET_ONLY')
            
            # 2. Tembak API Payment Gateway UI untuk dapat link Finpay
            finpay_url = initiate_finpay_payment(transaction_obj, request)
            
            # 3. Lempar user ke halaman Finpay
            return redirect(finpay_url)
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

def payment_page(request):
    PRICE_MAP = {
        'Paket Alumni': 275000,
        'Paket Mahasiswa Aktif': 175000,
        'Non-Paket': 50000
    }

    # Ambil data dari Session hasil redirect, fallback ke default jika kosong
    ticket_type = request.session.get('payment_ticket_type', 'Paket Alumni')
    quantity = request.session.get('payment_quantity', 1)

    # Kalkulasi lengkap ala E-Commerce
    base_price = PRICE_MAP.get(ticket_type, 0)
    
    subtotal = base_price * quantity
    total_bayar = subtotal

    # Hapus session agar tidak menyangkut kalau user refresh atau buka form baru
    if 'payment_ticket_type' in request.session:
        del request.session['payment_ticket_type']
    if 'payment_quantity' in request.session:
        del request.session['payment_quantity']

    context = {
        'ticket_type': ticket_type,
        'base_price': base_price,
        'quantity': quantity,
        'subtotal': subtotal,
        'total_bayar': total_bayar,
    }
    
    return render(request, 'registration/payment.html', context)

def generate_signed_headers(api_key, signing_secret, method, path, body_dict):
    timestamp = str(int(time.time()))
    
    # Konversi dictionary ke string JSON tanpa spasi ekstra untuk konsistensi hash
    body_str = json.dumps(body_dict, separators=(',', ':')) if body_dict else ""
    
    # Hash SHA256 dari body request
    body_hash = hashlib.sha256(body_str.encode('utf-8')).hexdigest()
    
    # Penggabungan payload sesuai rumus
    payload = f"{timestamp}.{method.upper()}.{path}.{body_hash}"
    
    # Pembuatan HMAC-SHA256 menggunakan signing_secret
    signature = hmac.new(
        signing_secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()
    
    return {
        "X-Api-Key": api_key,
        "X-Timestamp": timestamp,
        "X-Signature": signature,
        "Content-Type": "application/json",
    }

def initiate_finpay_payment(transaction_obj, request):
    # Dapatkan API_KEY dan SIGNING_SECRET dari dosen/DTD
    API_KEY = "dummy_api_key_disini"
    SIGNING_SECRET = "dummy_signing_secret_disini"
    
    BASE_URL = "https://dev-payment.ui.ac.id"
    PATH = "/api/v1/gateway/payments"
    
    # Format nomor telepon ke standar E.164
    raw_phone = transaction_obj.whatsapp_number
    e164_phone = f"+62{raw_phone.lstrip('0')}"
    
    # Susun Body Request
    body = {
        "idempotency_key": f"FUNWALK-{transaction_obj.id}",
        "amount": int(transaction_obj.total_amount),
        "currency": "IDR",
        "description": f"Registrasi Fun Walk Dies Natalis 40 - {transaction_obj.user.username}",
        "customer": {
            "first_name": transaction_obj.user.first_name or "Mahasiswa",
            "last_name": transaction_obj.user.last_name or "UI",
            "email": transaction_obj.user.email,
            "mobile_phone": e164_phone
        },
        "url": {
            "success_url": request.build_absolute_uri('/history/'),
            "fail_url": request.build_absolute_uri('/history/'),
            "back_url": request.build_absolute_uri('/payment/')
        }
    }

    # Generate Headers menggunakan fungsi sebelumnya
    headers = generate_signed_headers(API_KEY, SIGNING_SECRET, "POST", PATH, body)

    # Kirim HTTP POST Request
    response = requests.post(f"{BASE_URL}{PATH}", headers=headers, json=body)
    
    if response.status_code in [200, 201]:
        response_data = response.json()
        # Ekstraksi URL berdasarkan Swagger UI
        redirect_url = response_data.get("data", {}).get("finpay_redirect_url")
        return redirect_url
    else:
        raise ValueError(f"Gateway Error {response.status_code}: {response.text}")

@csrf_exempt
@require_POST
def payment_callback(request):
    try:
        # 1. Baca payload JSON yang dikirim oleh Gateway UI
        payload = json.loads(request.body)
        
        # 2. Ambil ID Transaksi dan Statusnya
        # Dokumen DTD menyebutkan kita bisa pakai idempotency_key atau order_id
        order_id = payload.get('idempotency_key') or payload.get('order_id')
        status = payload.get('status')

        if not order_id or not status:
            return JsonResponse({'error': 'Payload tidak lengkap'}, status=400)

        # 3. Ekstrak ID asli transaksi kita (karena tadi formatnya "FUNWALK-{id}")
        if order_id.startswith('FUNWALK-'):
            tx_id = order_id.split('-')[1]
        else:
            return JsonResponse({'error': 'Format order_id tidak dikenali'}, status=400)

        # 4. Cari transaksi di database
        try:
            transaction_obj = Transaction.objects.get(id=tx_id)
        except Transaction.DoesNotExist:
            return JsonResponse({'error': 'Transaksi tidak ditemukan'}, status=404)

        # 5. Idempotency Check: Kalau udah PAID, gausah diapa-apain lagi[cite: 1]
        if transaction_obj.status == 'PAID':
            return JsonResponse({'message': 'Transaksi sudah lunas sebelumnya'}, status=200)

        # 6. Update status berdasarkan mapping DTD[cite: 1]
        if status == 'success':
            transaction_obj.status = 'PAID'
        elif status in ['failed', 'cancelled', 'voided']:
            transaction_obj.status = 'FAILED'
        
        # Simpan perubahan ke database
        transaction_obj.save(update_fields=['status'])

        # 7. WAJIB balas HTTP 200 supaya sistem Gateway UI berhenti nge-retry[cite: 1]
        return JsonResponse({'message': 'Callback berhasil diproses'}, status=200)

    except json.JSONDecodeError:
        return JsonResponse({'error': 'Format JSON tidak valid'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)
