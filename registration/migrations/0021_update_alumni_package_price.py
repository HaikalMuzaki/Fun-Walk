from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('registration', '0020_normalize_gateway_pending_status'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticket',
            name='package_type',
            field=models.CharField(
                choices=[
                    ('ALUMNI_PACK', 'Paket Premium (Rp 250.000)'),
                    ('STUDENT_PACK', 'Paket Mahasiswa (Rp 125.000)'),
                    ('TICKET_ONLY', 'Paket Basic (Rp 50.000)'),
                ],
                max_length=20,
            ),
        ),
    ]
