from django.core.management.base import BaseCommand

from registration.invoices import send_payment_invoice
from registration.models import Transaction


class Command(BaseCommand):
    help = 'Email invoices for paid transactions that have no invoice_sent_at timestamp.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show recipients without sending email.')
        parser.add_argument('--limit', type=int, help='Maximum number of invoices to process.')

    def handle(self, *args, **options):
        transactions = (
            Transaction.objects.filter(status='PAID', invoice_sent_at__isnull=True)
            .select_related('user')
            .order_by('paid_at', 'created_at', 'id')
        )
        if options['limit'] is not None:
            transactions = transactions[:max(0, options['limit'])]
        transactions = list(transactions)

        if options['dry_run']:
            for transaction_obj in transactions:
                self.stdout.write(
                    f'{transaction_obj.transaction_id}\t{transaction_obj.user.email}'
                )
            self.stdout.write(
                self.style.WARNING(
                    f'Dry run: {len(transactions)} invoice(s) missing, no email sent.'
                )
            )
            return

        sent_count = 0
        failed_count = 0
        for transaction_obj in transactions:
            if send_payment_invoice(transaction_obj):
                sent_count += 1
            else:
                failed_count += 1
        self.stdout.write(
            self.style.SUCCESS(
                f'Missing invoice delivery complete: sent={sent_count}, failed={failed_count}.'
            )
        )
