import csv

from django.core.management.base import BaseCommand
from django.db.models import Count

from registration.models import CustomUser


class Command(BaseCommand):
    help = 'Export registered users without transactions as CSV for purchase reminder review.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--include-sent',
            action='store_true',
            help='Include users who have already received a purchase reminder.',
        )

    def handle(self, *args, **options):
        recipients = (
            CustomUser.objects.filter(is_active=True)
            .exclude(is_staff=True)
            .exclude(email='')
            .annotate(transaction_count=Count('transactions'))
            .filter(transaction_count=0)
            .order_by('email')
        )
        if not options['include_sent']:
            recipients = recipients.filter(purchase_reminder_sent_at__isnull=True)

        writer = csv.writer(self.stdout)
        writer.writerow(['email', 'nama', 'jenis_akun', 'dosen'])
        for user in recipients:
            writer.writerow([
                user.email,
                user.get_full_name() or user.username,
                user.get_user_type_display(),
                'Ya' if user.user_type == 'LECTURER' else 'Tidak',
            ])
