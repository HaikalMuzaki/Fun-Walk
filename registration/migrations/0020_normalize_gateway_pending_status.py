from django.db import migrations


def normalize_gateway_pending_status(apps, schema_editor):
    Transaction = apps.get_model('registration', 'Transaction')
    # Manual transfers have uploaded proof and must remain awaiting admin review.
    Transaction.objects.filter(status='PENDING_CONFIRMATION').exclude(
        payment_channel='MANUAL_TRANSFER_BNI',
    ).update(status='PENDING_PAYMENT')


class Migration(migrations.Migration):

    dependencies = [
        ('registration', '0019_alter_ticket_package_type_and_more'),
    ]

    operations = [
        migrations.RunPython(normalize_gateway_pending_status, migrations.RunPython.noop),
    ]
