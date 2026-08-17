import csv
from django.http import HttpResponse
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, Transaction, Ticket, EventInfo

# --- FITUR EXPORT EXCEL/CSV (P0 ADMIN) ---
@admin.action(description="Export Data Pendaftar (Excel/CSV)")
def export_transactions_to_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="data_pendaftar.csv"'
    writer = csv.writer(response)
    
    # Header Kolom Excel
    writer.writerow(['ID Transaksi', 'Username Akun', 'Nama Peserta', 'Tipe Paket', 'Harga', 'Status Pembayaran', 'Total Transaksi'])
    
    # Looping data transaksi dan tiket yang ada di dalamnya
    for trx in queryset:
        for ticket in trx.tickets.all():
            writer.writerow([
                trx.transaction_id, 
                trx.user.username, 
                ticket.attendee_name, 
                ticket.get_package_type_display(), 
                ticket.price, 
                trx.status, 
                trx.total_amount
            ])
    return response

# --- REGISTRASI MODEL KE DASHBOARD ADMIN ---

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    # Menampilkan kolom tambahan (NPM, User Type) di tabel admin
    list_display = ('username', 'email', 'user_type', 'npm', 'is_staff')
    list_filter = ('user_type', 'is_staff')
    
    # Memasukkan field tambahan ke form edit user
    fieldsets = UserAdmin.fieldsets + (
        ('Info Tambahan Fun Walk', {'fields': ('user_type', 'npm', 'address')}),
    )

@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('transaction_id', 'user', 'status', 'total_amount', 'created_at')
    list_filter = ('status',)
    actions = [export_transactions_to_csv]
    search_fields = ('transaction_id', 'user__username')

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('attendee_name', 'transaction', 'package_type', 'tshirt_size', 'price')
    list_filter = ('package_type', 'tshirt_size')
    search_fields = ('attendee_name', 'transaction__transaction_id')

@admin.register(EventInfo)
class EventInfoAdmin(admin.ModelAdmin):
    list_display = ('title', 'is_active')
    list_filter = ('is_active',)