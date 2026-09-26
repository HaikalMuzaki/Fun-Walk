from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('registration', '0021_update_alumni_package_price'),
    ]

    operations = [
        migrations.AddField(
            model_name='customuser',
            name='purchase_reminder_sent_at',
            field=models.DateTimeField(
                blank=True,
                null=True,
                verbose_name='Waktu Email Pengingat Pembelian Dikirim',
            ),
        ),
    ]
