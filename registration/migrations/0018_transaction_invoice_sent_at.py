from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('registration', '0017_transaction_expired_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='transaction',
            name='invoice_sent_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Waktu Invoice Dikirim'),
        ),
    ]
