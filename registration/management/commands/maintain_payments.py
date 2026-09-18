import time
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from registration.invoices import send_payment_invoice
from registration.models import Transaction
from registration.payment_gateway import refresh_transaction_status
from registration.views import _expire_transaction_if_overdue


class Command(BaseCommand):
    help = (
        'Expires overdue transactions and periodically reconciles gateway status. '
        'Run continuously in the payment scheduler container.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Run one maintenance cycle then exit.')
        parser.add_argument('--interval', type=int, default=60, help='Seconds between expiry checks.')
        parser.add_argument(
            '--reconcile-interval',
            type=int,
            default=3600,
            help='Seconds between full Finnet status reconciliation runs.',
        )
        parser.add_argument('--no-reconcile', action='store_true', help='Skip the Finnet reconciliation pass.')

    def _refresh_gateway_status(self, transaction_obj):
        try:
            refresh_transaction_status(transaction_obj)
            return True
        except ValueError as error:
            self.stderr.write(
                f'Gateway status check failed for {transaction_obj.transaction_id}: {error}'
            )
            return False

    def _expire_overdue_transactions(self, now):
        expired_count = 0
        transactions = Transaction.objects.filter(
            status__in=['PENDING_PAYMENT', 'PENDING_CONFIRMATION'],
        ).exclude(payment_channel='MANUAL_TRANSFER_BNI')

        for transaction_obj in transactions.iterator():
            if transaction_obj.gateway_transaction_id:
                self._refresh_gateway_status(transaction_obj)
                transaction_obj.refresh_from_db()

            if _expire_transaction_if_overdue(transaction_obj, now=now):
                expired_count += 1

        return expired_count

    def _reconcile_gateway_statuses(self):
        lookback_days = max(
            1,
            int(getattr(settings, 'PAYMENT_RECONCILIATION_LOOKBACK_DAYS', 1)),
        )
        since = timezone.now() - timedelta(days=lookback_days)
        transactions = Transaction.objects.filter(
            created_at__gte=since,
            status__in=['PENDING_PAYMENT', 'PENDING_CONFIRMATION', 'EXPIRED'],
        ).exclude(
            payment_channel='MANUAL_TRANSFER_BNI',
        ).exclude(
            gateway_transaction_id='',
        )

        checked_count = 0
        for transaction_obj in transactions.iterator():
            self._refresh_gateway_status(transaction_obj)
            checked_count += 1

        return checked_count

    def _retry_unsent_invoices(self):
        retry_hours = max(1, int(getattr(settings, 'PAYMENT_INVOICE_RETRY_HOURS', 24)))
        since = timezone.now() - timedelta(hours=retry_hours)
        transactions = Transaction.objects.filter(
            status='PAID',
            paid_at__gte=since,
            invoice_sent_at__isnull=True,
        )
        sent_count = 0
        for transaction_obj in transactions.iterator():
            if send_payment_invoice(transaction_obj):
                sent_count += 1
        return sent_count

    def _run_cycle(self, reconcile):
        now = timezone.now()
        expired_count = self._expire_overdue_transactions(now)
        checked_count = self._reconcile_gateway_statuses() if reconcile else 0
        invoice_count = self._retry_unsent_invoices() if reconcile else 0
        self.stdout.write(
            'Payment maintenance complete: '
            f'expired={expired_count}, reconciled={checked_count}, invoices_sent={invoice_count}.'
        )

    def handle(self, *args, **options):
        interval = max(1, options['interval'])
        reconcile_interval = max(interval, options['reconcile_interval'])
        run_once = options['once']
        next_reconciliation = timezone.now()

        while True:
            now = timezone.now()
            reconcile = not options['no_reconcile'] and now >= next_reconciliation
            self._run_cycle(reconcile=reconcile)
            if reconcile:
                next_reconciliation = now + timedelta(seconds=reconcile_interval)

            if run_once:
                return

            time.sleep(interval)
