import logging

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils import timezone

from .models import CustomUser

logger = logging.getLogger(__name__)


def send_purchase_reminder(user):
    """Send one reminder to an account that has not created a transaction."""
    user = CustomUser.objects.get(pk=user.pk)
    if user.purchase_reminder_sent_at or not user.email:
        return False

    customer_name = user.get_full_name() or user.username or 'Peserta'
    context = {
        'customer_name': customer_name,
        'purchase_url': f'{settings.PUBLIC_SITE_URL}/',
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
