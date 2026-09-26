import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.utils import timezone

from .models import CustomUser

logger = logging.getLogger(__name__)


def get_purchase_reminder_recipients(*, include_sent=False):
    """Return accounts without a completed payment or manual review in progress."""
    recipients = (
        CustomUser.objects.filter(is_active=True)
        .exclude(is_staff=True)
        .exclude(email='')
        .annotate(
            paid_transaction_count=Count(
                'transactions',
                filter=Q(transactions__status='PAID'),
            ),
            manual_review_count=Count(
                'transactions',
                filter=Q(transactions__status='PENDING_CONFIRMATION'),
            ),
        )
        .filter(paid_transaction_count=0, manual_review_count=0)
        .order_by('id')
    )
    if not include_sent:
        recipients = recipients.filter(purchase_reminder_sent_at__isnull=True)
    return recipients


def send_purchase_reminder(user):
    """Send one reminder to an account that has not completed payment."""
    user = CustomUser.objects.get(pk=user.pk)
    if user.purchase_reminder_sent_at or not user.email:
        return False

    transactions = user.transactions.all()
    if transactions.filter(status__in=['PAID', 'PENDING_CONFIRMATION']).exists():
        return False

    customer_name = user.get_full_name() or user.username or 'Peserta'
    has_unsuccessful_transaction = transactions.exists()
    context = {
        'customer_name': customer_name,
        'action_url': (
            f'{settings.PUBLIC_SITE_URL}/history/'
            if has_unsuccessful_transaction
            else f'{settings.PUBLIC_SITE_URL}/'
        ),
        'message': (
            'Anda memiliki pesanan yang belum selesai. Silakan buka riwayat transaksi untuk melanjutkan pembayaran.'
            if has_unsuccessful_transaction
            else 'Anda sudah berhasil membuat akun, tetapi belum memiliki pesanan. Pilih paket untuk melanjutkan pendaftaran.'
        ),
        'action_label': 'Lihat History' if has_unsuccessful_transaction else 'Pilih Paket',
    }
    message = EmailMultiAlternatives(
        subject='Yuk Lengkapi Pendaftaran Dies Natalis ke-40 Fasilkom UI',
        body=render_to_string('registration/emails/purchase_reminder.txt', context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
    )
    message.attach_alternative(
        render_to_string('registration/emails/purchase_reminder.html', context),
        'text/html',
    )

    try:
        sent_count = message.send()
    except Exception:
        logger.exception('Purchase reminder email failed for user %s.', user.pk)
        return False

    if not sent_count:
        logger.error('SMTP did not accept the purchase reminder for user %s.', user.pk)
        return False

    CustomUser.objects.filter(
        pk=user.pk,
        purchase_reminder_sent_at__isnull=True,
    ).update(purchase_reminder_sent_at=timezone.now())
    return True
