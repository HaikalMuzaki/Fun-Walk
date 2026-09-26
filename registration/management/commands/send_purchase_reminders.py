from django.core.management.base import BaseCommand
from django.db.models import Count

from registration.models import CustomUser
from registration.reminders import send_purchase_reminder


class Command(BaseCommand):
    help = 'Email active users who have registered but have not created any transaction.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show recipients without sending email.')
        parser.add_argument('--resend', action='store_true', help='Include recipients who were emailed before.')
        parser.add_argument('--limit', type=int, help='Maximum number of recipients to process.')

    def handle(self, *args, **options):
        recipients = (
            CustomUser.objects.filter(is_active=True)
            .exclude(is_staff=True)
            .exclude(email='')
            .annotate(transaction_count=Count('transactions'))
            .filter(transaction_count=0)
            .order_by('id')
        )
        if not options['resend']:
            recipients = recipients.filter(purchase_reminder_sent_at__isnull=True)
        if options['limit'] is not None:
            recipients = recipients[:max(0, options['limit'])]

        recipients = list(recipients)
        if options['dry_run']:
            for user in recipients:
                self.stdout.write(f'{user.pk}\t{user.email}')
            self.stdout.write(self.style.WARNING(f'Dry run: {len(recipients)} recipient(s), no email sent.'))
            return

        sent_count = 0
        failed_count = 0
        for user in recipients:
            if send_purchase_reminder(user):
                sent_count += 1
            else:
                failed_count += 1
        self.stdout.write(
            self.style.SUCCESS(
                f'Purchase reminders complete: sent={sent_count}, failed={failed_count}.'
            )
        )
