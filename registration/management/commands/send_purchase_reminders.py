from django.core.management.base import BaseCommand

from registration.reminders import get_purchase_reminder_recipients, send_purchase_reminder


class Command(BaseCommand):
    help = 'Email eligible users who have no completed payment or manual review in progress.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Show recipients without sending email.')
        parser.add_argument('--resend', action='store_true', help='Include recipients who were emailed before.')
        parser.add_argument('--limit', type=int, help='Maximum number of recipients to process.')

    def handle(self, *args, **options):
        recipients = get_purchase_reminder_recipients(include_sent=options['resend']).exclude(
            user_type='LECTURER'
        )
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
