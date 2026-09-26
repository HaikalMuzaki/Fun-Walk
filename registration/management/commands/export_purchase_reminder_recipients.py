import csv

from django.core.management.base import BaseCommand

from registration.reminders import get_purchase_reminder_recipients


class Command(BaseCommand):
    help = 'Export users eligible for a purchase reminder as CSV for review.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--include-sent',
            action='store_true',
            help='Include users who have already received a purchase reminder.',
        )

    def handle(self, *args, **options):
        recipients = get_purchase_reminder_recipients(
            include_sent=options['include_sent']
        ).order_by('email')

        writer = csv.writer(self.stdout)
        writer.writerow(['email', 'nama', 'jenis_akun', 'dosen'])
        for user in recipients:
            writer.writerow([
                user.email,
                user.get_full_name() or user.username,
                user.get_user_type_display(),
                'Ya' if user.user_type == 'LECTURER' else 'Tidak',
            ])
