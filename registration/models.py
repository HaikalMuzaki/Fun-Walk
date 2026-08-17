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
        ('PENDING', 'Menunggu Pembayaran'),
        ('PAID', 'Lunas'),
        ('FAILED', 'Gagal/Batal'),
    ]
    # Relasi: 1 User bisa punya banyak Transaksi
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='transactions')
    transaction_id = models.CharField(max_length=50, unique=True, editable=False)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.transaction_id:
            self.transaction_id = f"TRX-{uuid.uuid4().hex[:8].upper()}"
        super().save(*args, **kwargs)

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
        ('S', 'S'), ('M', 'M'), ('L', 'L'), ('XL', 'XL'), ('XXL', 'XXL'),
    ]
    
    # Relasi: 1 Transaksi bisa punya banyak Tiket (Pax)
    transaction = models.ForeignKey(Transaction, on_delete=models.CASCADE, related_name='tickets')
    attendee_name = models.CharField(max_length=150, verbose_name="Nama Peserta (Pax)")
    package_type = models.CharField(max_length=20, choices=PACKAGE_CHOICES)
    tshirt_size = models.CharField(max_length=10, choices=SIZE_CHOICES, default='NONE')
    price = models.DecimalField(max_digits=10, decimal_places=2, editable=False)

    def save(self, *args, **kwargs):
        # Auto-set harga berdasarkan pilihan paket
        if self.package_type == 'ALUMNI_PACK':
            self.price = 275000
        elif self.package_type == 'STUDENT_PACK':
            self.price = 175000
        else:
            self.price = 50000
            self.tshirt_size = 'NONE' # Reset kaos jika cuma beli tiket
        super().save(*args, **kwargs)

# --- 4. EXTRA INFO (CMS untuk Frontend) ---
class EventInfo(models.Model):
    title = models.CharField(max_length=100, verbose_name="Judul Info (Rundown/Denah)")
    content = models.TextField(verbose_name="Konten (HTML/Teks)")
    image_attachment = models.ImageField(upload_to='event_info/', blank=True, null=True)
    is_active = models.BooleanField(default=True)