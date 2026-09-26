import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .models import Transaction

logger = logging.getLogger(__name__)


def format_rupiah(amount):
    return f"Rp{int(amount):,}".replace(',', '.')


def send_payment_invoice(transaction_obj):
    """Send one payment receipt and record it only after SMTP accepts it."""
    transaction_obj = (
        Transaction.objects.select_related('user')
        .prefetch_related('tickets')
        .get(pk=transaction_obj.pk)
    )
    if transaction_obj.status != 'PAID' or transaction_obj.invoice_sent_at:
        return False

    recipient = (transaction_obj.user.email or '').strip()
    if not recipient:
        logger.warning('Invoice skipped for %s because the account has no email.', transaction_obj.transaction_id)
        return False

    tickets = list(transaction_obj.tickets.all())
    first_ticket = tickets[0] if tickets else None
    customer_name = (
        (first_ticket.attendee_name if first_ticket else '')
        or transaction_obj.user.get_full_name()
        or transaction_obj.user.username
    )
    context = {
        'customer_name': customer_name,
        'transaction_id': transaction_obj.transaction_id,
        'ticket_count': len(tickets),
        'packages': ', '.join(ticket.get_package_type_display() for ticket in tickets) or '-',
        'total_amount': format_rupiah(transaction_obj.total_amount),
        # Older PAID records may predate the paid_at field. Keep them eligible
        # for a one-time invoice backfill instead of failing the whole batch.
        'paid_at': timezone.localtime(
            transaction_obj.paid_at or transaction_obj.created_at
        ).strftime('%d %B %Y, %H:%M WIB'),
        'payment_method': transaction_obj.payment_channel or transaction_obj.payment_type or 'Payment Gateway',
    }
    message = EmailMultiAlternatives(
        subject=f'Invoice Pembayaran - {transaction_obj.transaction_id}',
        body=render_to_string('registration/emails/payment_invoice.txt', context),
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[recipient],
    )
    message.attach_alternative(
        render_to_string('registration/emails/payment_invoice.html', context),
        'text/html',
    )

    try:
        sent_count = message.send()
    except Exception:
        logger.exception('Invoice email failed for %s.', transaction_obj.transaction_id)
        return False

    if not sent_count:
        logger.error('SMTP did not accept the invoice for %s.', transaction_obj.transaction_id)
        return False

    Transaction.objects.filter(
        pk=transaction_obj.pk,
        invoice_sent_at__isnull=True,
    ).update(invoice_sent_at=timezone.now())
    return True
