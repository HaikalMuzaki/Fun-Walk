import hashlib
import hmac
import json
import logging
import os
import time

import requests
from django.urls import reverse
from django.utils import timezone
from requests.exceptions import RequestException

logger = logging.getLogger(__name__)

PAYMENT_GATEWAY_PATH = '/api/v1/gateway/payments'

GATEWAY_TO_LOCAL_STATUS = {
    'success': 'PAID',
    'successful': 'PAID',
    'paid': 'PAID',
    'settled': 'PAID',
    'completed': 'PAID',
    'failed': 'FAILED',
    'failure': 'FAILED',
    'denied': 'FAILED',
    'rejected': 'FAILED',
    'error': 'FAILED',
    'cancelled': 'FAILED',
    'canceled': 'FAILED',
    'voided': 'FAILED',
    'expired': 'FAILED',
    'pending': 'PENDING_CONFIRMATION',
    'initiated': 'PENDING_CONFIRMATION',
    'processing': 'PENDING_CONFIRMATION',
    'duplicate': 'PENDING_CONFIRMATION',
}


def _get_env(*names, default=''):
    for name in names:
        value = os.environ.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return default


def get_payment_gateway_config():
    api_key = _get_env('PAYMENT_GATEWAY_API_KEY', 'FINPAY_API_KEY')
    signing_secret = _get_env('PAYMENT_GATEWAY_SIGNING_SECRET', 'FINPAY_SIGNING_SECRET')
    base_url = _get_env('PAYMENT_GATEWAY_BASE_URL', 'FINPAY_BASE_URL', default='https://dev-payment.ui.ac.id')
    fallback_base_url = _get_env('PAYMENT_GATEWAY_FALLBACK_BASE_URL', default='')
    verify_callback_status = (
        _get_env('PAYMENT_GATEWAY_VERIFY_CALLBACK_STATUS', default='false').lower() == 'true'
    )

    if not api_key or not signing_secret:
        raise ValueError(
            'Konfigurasi payment gateway belum lengkap. Isi PAYMENT_GATEWAY_API_KEY dan PAYMENT_GATEWAY_SIGNING_SECRET di environment server.'
        )

    return {
        'api_key': api_key,
        'signing_secret': signing_secret,
        'base_url': base_url.rstrip('/'),
        'fallback_base_url': fallback_base_url.rstrip('/'),
        'verify_callback_status': verify_callback_status,
    }


def get_payment_gateway_base_urls(config):
    base_urls = []
    for candidate in [config['base_url'], config['fallback_base_url']]:
        if candidate and candidate not in base_urls:
            base_urls.append(candidate)

    return base_urls


def split_name(full_name):
    parts = (full_name or '').split(None, 1)
    first_name = parts[0] if parts else 'Peserta'
    last_name = parts[1] if len(parts) > 1 else ''
    return first_name, last_name


def build_absolute_url(request, route_name):
    return request.build_absolute_uri(reverse(route_name))


def serialize_request_body(body_dict=None):
    if not body_dict:
        return ''
    return json.dumps(body_dict, separators=(',', ':'), ensure_ascii=False)


def generate_signed_headers(api_key, signing_secret, method, path, body_str=''):
    timestamp = str(int(time.time()))
    body_hash = hashlib.sha256(body_str.encode('utf-8')).hexdigest()
    payload = f'{timestamp}.{method.upper()}.{path}.{body_hash}'
    signature = hmac.new(
        signing_secret.encode('utf-8'),
        payload.encode('utf-8'),
        hashlib.sha256,
    ).hexdigest()

    return {
        'X-Api-Key': api_key,
        'X-Timestamp': timestamp,
        'X-Signature': signature,
        'Content-Type': 'application/json',
    }


def extract_redirect_url(response_data):
    data = response_data.get('data') or {}
    return data.get('redirect_url') or data.get('finpay_redirect_url')


def normalize_gateway_status(status):
    return (status or '').strip().lower()


def map_gateway_status_to_local(status):
    return GATEWAY_TO_LOCAL_STATUS.get(normalize_gateway_status(status), 'PENDING_CONFIRMATION')


def is_terminal_local_status(status):
    return status in {'PAID', 'FAILED', 'CANCELLED'}


def build_initiate_payload(transaction_obj, request, package_label):
    raw_phone = transaction_obj.whatsapp_number
    e164_phone = f"+62{raw_phone.lstrip('0')}"
    first_ticket = transaction_obj.tickets.order_by('id').first()
    full_name = first_ticket.attendee_name if first_ticket else transaction_obj.user.get_full_name()
    first_name, last_name = split_name(full_name)
    payment_page_url = f"{build_absolute_url(request, 'payment_page')}?trx={transaction_obj.idempotency_key}"

    return {
        'idempotency_key': transaction_obj.idempotency_key,
        'amount': int(transaction_obj.total_amount),
        'currency': 'IDR',
        'description': f'Registrasi Fun Walk Dies Natalis 40 - {package_label} - {transaction_obj.user.username}',
        'customer': {
            'first_name': first_name,
            'last_name': last_name,
            'email': transaction_obj.user.email,
            'mobile_phone': e164_phone,
        },
        'url': {
            'success_url': build_absolute_url(request, 'history'),
            'fail_url': build_absolute_url(request, 'history'),
            'back_url': payment_page_url,
        },
    }


def store_initiate_response(transaction_obj, response_data, redirect_url):
    data = response_data.get('data') or {}
    transaction_obj.gateway_transaction_id = data.get('transaction_id') or transaction_obj.gateway_transaction_id
    transaction_obj.gateway_status = data.get('status') or transaction_obj.gateway_status
    transaction_obj.payment_redirect_url = redirect_url or transaction_obj.payment_redirect_url
    transaction_obj.gateway_response_payload = response_data

    next_status = map_gateway_status_to_local(transaction_obj.gateway_status)
    if not is_terminal_local_status(transaction_obj.status):
        transaction_obj.status = next_status
        if next_status == 'PAID':
            transaction_obj.paid_at = transaction_obj.paid_at or timezone.now()
        elif next_status == 'FAILED':
            transaction_obj.failed_at = transaction_obj.failed_at or timezone.now()

    transaction_obj.save(
        update_fields=[
            'status',
            'gateway_transaction_id',
            'gateway_status',
            'payment_redirect_url',
            'gateway_response_payload',
            'paid_at',
            'failed_at',
        ]
    )


def _request_with_base_url_failover(method, path, headers, body_bytes=None, timeout=30):
    config = get_payment_gateway_config()
    request_errors = []

    for base_url in get_payment_gateway_base_urls(config):
        try:
            if method.upper() == 'POST':
                return requests.post(
                    f'{base_url}{path}',
                    headers=headers,
                    data=body_bytes,
                    timeout=timeout,
                )
            return requests.get(
                f'{base_url}{path}',
                headers=headers,
                timeout=timeout,
            )
        except RequestException as error:
            request_errors.append(f'{base_url}: {error}')
            logger.warning('Request ke payment gateway gagal via %s: %s', base_url, error)

    raise ValueError('Koneksi ke payment gateway gagal: ' + ' | '.join(request_errors))


def initiate_payment(transaction_obj, request, package_label):
    config = get_payment_gateway_config()
    payload = build_initiate_payload(transaction_obj, request, package_label)
    request_body = serialize_request_body(payload)
    headers = generate_signed_headers(
        config['api_key'],
        config['signing_secret'],
        'POST',
        PAYMENT_GATEWAY_PATH,
        request_body,
    )

    try:
        response = _request_with_base_url_failover(
            'POST',
            PAYMENT_GATEWAY_PATH,
            headers,
            body_bytes=request_body.encode('utf-8'),
            timeout=30,
        )
    except ValueError:
        raise

    try:
        response_data = response.json()
    except ValueError:
        response_data = {}

    if response.status_code not in [200, 201]:
        error_message = response_data.get('error') or response_data.get('message') or response.text
        raise ValueError(f'Gateway Error {response.status_code}: {error_message}')

    redirect_url = extract_redirect_url(response_data)
    if not redirect_url:
        raise ValueError('Gateway berhasil merespons, tetapi redirect_url tidak ditemukan di response.')

    store_initiate_response(transaction_obj, response_data, redirect_url)
    return redirect_url


def fetch_payment_status(transaction_obj):
    if not transaction_obj.gateway_transaction_id:
        raise ValueError('gateway_transaction_id belum tersedia untuk transaksi ini.')

    config = get_payment_gateway_config()
    path = f'{PAYMENT_GATEWAY_PATH}/{transaction_obj.gateway_transaction_id}/status'
    headers = generate_signed_headers(
        config['api_key'],
        config['signing_secret'],
        'GET',
        path,
        '',
    )

    try:
        response = _request_with_base_url_failover(
            'GET',
            path,
            headers,
            timeout=30,
        )
    except ValueError as error:
        raise ValueError(f'Gagal mengambil status payment gateway: {error}') from error

    try:
        response_data = response.json()
    except ValueError:
        response_data = {}

    if response.status_code != 200:
        error_message = response_data.get('error') or response_data.get('message') or response.text
        raise ValueError(f'Gateway status error {response.status_code}: {error_message}')

    return response_data


def apply_callback_payload(transaction_obj, payload):
    gateway_transaction_id = payload.get('transaction_id')
    gateway_status = payload.get('status')

    if gateway_transaction_id:
        transaction_obj.gateway_transaction_id = gateway_transaction_id
    if gateway_status:
        transaction_obj.gateway_status = gateway_status

    transaction_obj.payment_channel = payload.get('payment_channel') or transaction_obj.payment_channel
    transaction_obj.payment_type = payload.get('payment_type') or transaction_obj.payment_type
    transaction_obj.gateway_callback_payload = payload

    next_status = map_gateway_status_to_local(gateway_status)
    if not is_terminal_local_status(transaction_obj.status):
        transaction_obj.status = next_status
        if next_status == 'PAID':
            transaction_obj.paid_at = transaction_obj.paid_at or timezone.now()
        elif next_status == 'FAILED':
            transaction_obj.failed_at = transaction_obj.failed_at or timezone.now()

    transaction_obj.save(
        update_fields=[
            'status',
            'gateway_transaction_id',
            'gateway_status',
            'payment_channel',
            'payment_type',
            'gateway_callback_payload',
            'paid_at',
            'failed_at',
        ]
    )


def verify_callback_status_if_needed(transaction_obj):
    try:
        config = get_payment_gateway_config()
    except ValueError:
        return

    if not config['verify_callback_status'] or not transaction_obj.gateway_transaction_id:
        return

    try:
        response_data = fetch_payment_status(transaction_obj)
    except ValueError as error:
        logger.warning('Verifikasi status gateway gagal untuk %s: %s', transaction_obj.transaction_id, error)
        return

    data = response_data.get('data') or {}
    gateway_status = data.get('status')
    if gateway_status:
        transaction_obj.gateway_status = gateway_status
        if not is_terminal_local_status(transaction_obj.status):
            transaction_obj.status = map_gateway_status_to_local(gateway_status)
            if transaction_obj.status == 'PAID':
                transaction_obj.paid_at = transaction_obj.paid_at or timezone.now()
            elif transaction_obj.status == 'FAILED':
                transaction_obj.failed_at = transaction_obj.failed_at or timezone.now()
        transaction_obj.gateway_response_payload = response_data
        transaction_obj.save(
            update_fields=[
                'status',
                'gateway_status',
                'gateway_response_payload',
                'paid_at',
                'failed_at',
            ]
        )
