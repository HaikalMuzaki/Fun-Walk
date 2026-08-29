from django.db import migrations, models


def migrate_pending_status(apps, schema_editor):
    Transaction = apps.get_model('registration', 'Transaction')
    Transaction.objects.filter(status='PENDING').update(status='PENDING_PAYMENT')


def restore_pending_status(apps, schema_editor):
    Transaction = apps.get_model('registration', 'Transaction')
    Transaction.objects.filter(
        status__in=['PENDING_PAYMENT', 'PENDING_CONFIRMATION']
    ).update(status='PENDING')
    Transaction.objects.filter(status='CANCELLED').update(status='FAILED')


class Migration(migrations.Migration):

    dependencies = [
        ('registration', '0005_transaction_failed_at_and_more'),
    ]

    operations = [
        migrations.RunPython(migrate_pending_status, restore_pending_status),
        migrations.AlterField(
            model_name='transaction',
            name='status',
            field=models.CharField(
                choices=[
                    ('PENDING_PAYMENT', 'Menunggu Pembayaran'),
                    ('PENDING_CONFIRMATION', 'Menunggu Konfirmasi'),
                    ('PAID', 'Sukses'),
                    ('FAILED', 'Gagal'),
                    ('CANCELLED', 'Pesanan Dibatalkan'),
                ],
                default='PENDING_PAYMENT',
                max_length=20,
            ),
        ),
    ]
