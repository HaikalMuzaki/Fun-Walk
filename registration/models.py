from django.db import models
from django.contrib.auth.models import AbstractUser
import uuid

# --- 1. AKUN USER (Bisa SSO Mahasiswa Aktif atau Email Alumni) ---
class CustomUser(AbstractUser):
    USER_TYPE_CHOICES = [
        ('STUDENT', 'Mahasiswa Aktif'),
        ('ALUMNI', 'Alumni'),
    ]
    user_type = models.CharField(max_length=10, choices=USER_TYPE_CHOICES, default='ALUMNI')
    npm = models.CharField(max_length=20, blank=True, null=True, verbose_name="NPM (Khusus Mahasiswa)")
    address = models.TextField(blank=True, null=True, verbose_name="Alamat Pengiriman/Domisili")
    
    # Catatan SSO: Nanti pas integrasi SSO UI, sistem akan nge-bind akun ke sini via NPM.
    # Alumni login normal pakai Email/Username.

# --- 2. TRANSAKSI (Keranjang Belanja) ---
class Transaction(models.Model):
    STATUS_CHOICES = [
        ('PENDING_PAYMENT', 'Menunggu Pembayaran'),
        ('PENDING_CONFIRMATION', 'Menunggu Konfirmasi'),
        ('PAID', 'Sukses'),
        ('FAILED', 'Gagal'),
        ('CANCELLED', 'Pesanan Dibatalkan'),
    ]
    # Relasi: 1 User bisa punya banyak Transaksi
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='transactions')
    transaction_id = models.CharField(max_length=50, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING_PAYMENT')
    whatsapp_number = models.CharField(max_length=20, blank=True, verbose_name="Nomor WhatsApp")
    cohort_year = models.PositiveSmallIntegerField(blank=True, null=True, verbose_name="Tahun Angkatan")
    idempotency_key = models.CharField(max_length=80, unique=True, editable=False, blank=True)
    gateway_transaction_id = models.CharField(max_length=120, blank=True, default='', verbose_name="ID Transaksi Gateway")
    gateway_status = models.CharField(max_length=50, blank=True, default='', verbose_name="Status Gateway")
    payment_channel = models.CharField(max_length=50, blank=True, default='', verbose_name="Channel Pembayaran")
    payment_type = models.CharField(max_length=50, blank=True, default='', verbose_name="Tipe Pembayaran")
    payment_redirect_url = models.URLField(max_length=500, blank=True, default='', verbose_name="Redirect URL Payment")
    gateway_response_payload = models.JSONField(blank=True, null=True, verbose_name="Payload Response Gateway")
    gateway_callback_payload = models.JSONField(blank=True, null=True, verbose_name="Payload Callback Gateway")
    paid_at = models.DateTimeField(blank=True, null=True, verbose_name="Waktu Lunas")
    failed_at = models.DateTimeField(blank=True, null=True, verbose_name="Waktu Gagal")
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.transaction_id:
            self.transaction_id = f"TRX-{uuid.uuid4().hex[:8].upper()}"
        if not self.idempotency_key:
            self.idempotency_key = f"PAY-{uuid.uuid4().hex[:20].upper()}"
        super().save(*args, **kwargs)

    def rotate_idempotency_key(self):
        self.idempotency_key = f"PAY-{uuid.uuid4().hex[:20].upper()}"

    def __str__(self):
        return f"{self.transaction_id} - {self.user.username} - {self.status}"

# --- 3. TIKET / PAX (Isi dari Transaksi) ---
class Ticket(models.Model):
    PACKAGE_CHOICES = [
        ('ALUMNI_PACK', 'Paket Alumni (Rp 275.000)'),
        ('STUDENT_PACK', 'Paket Mahasiswa (Rp 175.000)'),
        ('TICKET_ONLY', 'Tiket Saja / Non-Package (Rp 50.000)'),
    ]
    SIZE_CHOICES = [
        ('NONE', 'Tidak Ada Kaos'),
        ('XS', 'XS'),
        ('S', 'S'),
        ('M', 'M'),
        ('L', 'L'),
        ('XL', 'XL'),
        ('XXL+', 'XXL+'),
    ]
    
    # Relasi: 1 Transaksi bisa punya banyak Tiket (Pax)
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='tickets')
    attendee_name = models.CharField(max_length=150, verbose_name="Nama Peserta (Pax)")
    package_type = models.CharField(max_length=20, choices=PACKAGE_CHOICES)
    tshirt_size = models.CharField(max_length=10, choices=SIZE_CHOICES, default='NONE')
    price = models.DecimalField(max_digits=10, decimal_places=2, editable=False)

    def save(self, *args, **kwargs):
        # Auto-set harga default berdasarkan pilihan paket, kecuali sudah diset eksplisit.
        if not self.price:
            if self.package_type == 'ALUMNI_PACK':
                self.price = 275000
            elif self.package_type == 'STUDENT_PACK':
                self.price = 175000
            else:
                self.price = 50000
        if self.package_type == 'TICKET_ONLY':
            self.tshirt_size = 'NONE'
        super().save(*args, **kwargs)

# --- 4. EXTRA INFO (CMS untuk Frontend) ---
class EventInfo(models.Model):
    title = models.CharField(max_length=100, verbose_name="Judul Info (Rundown/Denah)")
    content = models.TextField(verbose_name="Konten (HTML/Teks)")
    image_attachment = models.ImageField(upload_to='event_info/', blank=True, null=True)
    is_active = models.BooleanField(default=True)


class TransactionSpreadsheetBackup(models.Model):
    title = models.CharField(max_length=150, verbose_name="Nama Backup")
    file = models.FileField(upload_to='admin_exports/', verbose_name="File Spreadsheet")
    transaction_count = models.PositiveIntegerField(default=0, verbose_name="Jumlah Transaksi")
    response_count = models.PositiveIntegerField(default=0, verbose_name="Jumlah Baris Response")
    created_by = models.ForeignKey(
        CustomUser,
        on_delete=models.SET_NULL,
        blank=True,
        null=True,
        related_name='created_transaction_backups',
        verbose_name="Dibuat Oleh",
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="Dibuat Pada")

    class Meta:
        ordering = ['-created_at']
        verbose_name = "Backup Spreadsheet Response"
        verbose_name_plural = "Backup Spreadsheet Response"

    def __str__(self):
        return self.title
