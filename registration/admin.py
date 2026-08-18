import csv
import io

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.core.files.base import ContentFile
from django.http import FileResponse, Http404, HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.utils.html import format_html

from .models import (
    CustomUser,
    EventInfo,
    Ticket,
    Transaction,
    TransactionSpreadsheetBackup,
)

EXPORT_HEADERS = [
    'No',
    'ID Transaksi',
    'Status Pembayaran',
    'Tanggal',
    'Waktu',
    'Username Akun',
    'Email Akun',
    'Jenis Akun',
    'Tahun Angkatan',
    'Nomor WhatsApp',
    'Nama Peserta',
    'Paket',
    'Tiket Ke',
    'Ukuran Kaos',
    'Harga Tiket',
    'Total Transaksi',
]


def _get_transaction_export_queryset(queryset=None):
    base_queryset = queryset if queryset is not None else Transaction.objects.all()
    return (
        base_queryset.select_related('user')
        .prefetch_related('tickets')
        .order_by('-created_at', '-id')
    )


def _build_transaction_export_rows(queryset=None):
    rows = []
    transaction_count = 0

    for transaction_index, transaction_obj in enumerate(_get_transaction_export_queryset(queryset), start=1):
        transaction_count += 1
        created_at = timezone.localtime(transaction_obj.created_at)
        tickets = list(transaction_obj.tickets.all())
        cohort_year = transaction_obj.cohort_year or '-'
        whatsapp_number = transaction_obj.whatsapp_number or '-'

        if not tickets:
            rows.append([
                transaction_index,
                transaction_obj.transaction_id,
                transaction_obj.get_status_display(),
                created_at.strftime('%d/%m/%Y'),
                created_at.strftime('%H:%M:%S'),
                transaction_obj.user.username,
                transaction_obj.user.email,
                transaction_obj.user.get_user_type_display(),
                cohort_year,
                whatsapp_number,
                '-',
                '-',
                '-',
                '-',
                '0',
                transaction_obj.total_amount,
            ])
            continue

        for ticket_index, ticket in enumerate(tickets, start=1):
            rows.append([
                transaction_index,
                transaction_obj.transaction_id,
                transaction_obj.get_status_display(),
                created_at.strftime('%d/%m/%Y'),
                created_at.strftime('%H:%M:%S'),
                transaction_obj.user.username,
                transaction_obj.user.email,
                transaction_obj.user.get_user_type_display(),
                cohort_year,
                whatsapp_number,
                ticket.attendee_name,
                ticket.get_package_type_display(),
                ticket_index,
                '-' if ticket.tshirt_size == 'NONE' else ticket.tshirt_size,
                ticket.price,
                transaction_obj.total_amount,
            ])

    return rows, transaction_count


def _build_transaction_csv_content(queryset=None):
    rows, transaction_count = _build_transaction_export_rows(queryset)
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer)
    writer.writerow(EXPORT_HEADERS)
    writer.writerows(rows)
    return buffer.getvalue(), transaction_count, len(rows)


def _spreadsheet_response(filename, csv_content):
    response = HttpResponse(
        csv_content.encode('utf-8-sig'),
        content_type='text/csv; charset=utf-8',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@admin.action(description="Download spreadsheet untuk transaksi terpilih")
def export_selected_transactions_to_csv(modeladmin, request, queryset):
    csv_content, transaction_count, row_count = _build_transaction_csv_content(queryset)
    timestamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
    filename = f'transaksi-terpilih-{timestamp}.csv'
    modeladmin.message_user(
        request,
        f'{transaction_count} transaksi dengan {row_count} baris response berhasil disiapkan.',
    )
    return _spreadsheet_response(filename, csv_content)


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    list_display = ('username', 'email', 'user_type', 'npm', 'is_staff')
    list_filter = ('user_type', 'is_staff')
    fieldsets = UserAdmin.fieldsets + (
        ('Info Tambahan Fun Walk', {'fields': ('user_type', 'npm', 'address')}),
    )


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    change_list_template = 'admin/registration/transaction/change_list.html'
    list_display = (
        'transaction_id',
        'account_email',
        'account_type',
        'ticket_count',
        'package_summary',
        'status_badge',
        'total_amount',
        'created_at_local',
    )
    list_filter = ('status', 'user__user_type')
    actions = [export_selected_transactions_to_csv]
    search_fields = ('transaction_id', 'user__username', 'user__email', 'tickets__attendee_name')

    def get_urls(self):
        custom_urls = [
            path(
                'responses/',
                self.admin_site.admin_view(self.responses_view),
                name='registration_transaction_responses',
            ),
            path(
                'export/latest/',
                self.admin_site.admin_view(self.download_latest_spreadsheet_view),
                name='registration_transaction_export_latest',
            ),
            path(
                'backup/create/',
                self.admin_site.admin_view(self.create_backup_view),
                name='registration_transaction_backup_create',
            ),
        ]
        return custom_urls + super().get_urls()

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context.update({
            'download_latest_url': reverse('admin:registration_transaction_export_latest'),
            'create_backup_url': reverse('admin:registration_transaction_backup_create'),
            'responses_url': reverse('admin:registration_transaction_responses'),
        })
        return super().changelist_view(request, extra_context=extra_context)

    @admin.display(description='Email Akun', ordering='user__email')
    def account_email(self, obj):
        return obj.user.email

    @admin.display(description='Jenis Akun', ordering='user__user_type')
    def account_type(self, obj):
        return obj.user.get_user_type_display()

    @admin.display(description='Jumlah Tiket')
    def ticket_count(self, obj):
        return obj.tickets.count()

    @admin.display(description='Paket')
    def package_summary(self, obj):
        labels = []
        for ticket in obj.tickets.all():
            label = ticket.get_package_type_display()
            if label not in labels:
                labels.append(label)
        return ', '.join(labels) or '-'

    @admin.display(description='Status', ordering='status')
    def status_badge(self, obj):
        color_map = {
            'PENDING': '#c68b00',
            'PAID': '#0c8a43',
            'FAILED': '#d93025',
        }
        label = obj.get_status_display()
        color = color_map.get(obj.status, '#444444')
        return format_html(
            '<span style="font-weight: 700; color: {};">{}</span>',
            color,
            label,
        )

    @admin.display(description='Tanggal Transaksi', ordering='created_at')
    def created_at_local(self, obj):
        return timezone.localtime(obj.created_at).strftime('%d/%m/%Y %H:%M:%S')

    def responses_view(self, request):
        rows, transaction_count = _build_transaction_export_rows()
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': 'Live Spreadsheet Response',
            'rows': rows,
            'headers': EXPORT_HEADERS,
            'transaction_count': transaction_count,
            'row_count': len(rows),
            'download_latest_url': reverse('admin:registration_transaction_export_latest'),
            'create_backup_url': reverse('admin:registration_transaction_backup_create'),
        }
        return TemplateResponse(request, 'admin/registration/transaction/responses.html', context)

    def download_latest_spreadsheet_view(self, request):
        csv_content, transaction_count, row_count = _build_transaction_csv_content()
        timestamp = timezone.localtime().strftime('%Y%m%d-%H%M%S')
        filename = f'response-transaksi-terbaru-{timestamp}.csv'
        self.message_user(
            request,
            f'Spreadsheet terbaru berhasil dibuat dari {transaction_count} transaksi dan {row_count} baris response.',
        )
        return _spreadsheet_response(filename, csv_content)

    def create_backup_view(self, request):
        csv_content, transaction_count, row_count = _build_transaction_csv_content()
        timestamp = timezone.localtime()
        backup = TransactionSpreadsheetBackup(
            title=f'Spreadsheet Response {timestamp.strftime("%d-%m-%Y %H:%M:%S")}',
            transaction_count=transaction_count,
            response_count=row_count,
            created_by=request.user if request.user.is_authenticated else None,
        )
        backup.file.save(
            f'spreadsheet-response-{timestamp.strftime("%Y%m%d-%H%M%S")}.csv',
            ContentFile(csv_content.encode('utf-8-sig')),
            save=False,
        )
        backup.save()
        self.message_user(
            request,
            'Backup spreadsheet baru berhasil dibuat. File ini menyimpan snapshot response saat tombol backup ditekan.',
        )
        return HttpResponseRedirect(reverse('admin:registration_transactionspreadsheetbackup_changelist'))


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('attendee_name', 'transaction', 'package_type', 'tshirt_size', 'price')
    list_filter = ('package_type', 'tshirt_size')
    search_fields = ('attendee_name', 'transaction__transaction_id')


@admin.register(EventInfo)
class EventInfoAdmin(admin.ModelAdmin):
    list_display = ('title', 'is_active')
    list_filter = ('is_active',)


@admin.register(TransactionSpreadsheetBackup)
class TransactionSpreadsheetBackupAdmin(admin.ModelAdmin):
    list_display = (
        'title',
        'transaction_count',
        'response_count',
        'created_at_local',
        'created_by',
        'download_link',
    )
    readonly_fields = (
        'title',
        'file',
        'transaction_count',
        'response_count',
        'created_by',
        'created_at',
        'download_link',
    )
    search_fields = ('title', 'created_by__email', 'created_by__username')

    def get_urls(self):
        custom_urls = [
            path(
                '<int:backup_id>/download/',
                self.admin_site.admin_view(self.download_backup_view),
                name='registration_transactionspreadsheetbackup_download',
            ),
        ]
        return custom_urls + super().get_urls()

    def has_add_permission(self, request):
        return False

    @admin.display(description='Dibuat Pada', ordering='created_at')
    def created_at_local(self, obj):
        return timezone.localtime(obj.created_at).strftime('%d/%m/%Y %H:%M:%S')

    @admin.display(description='Download File')
    def download_link(self, obj):
        if not obj.file:
            return '-'
        url = reverse('admin:registration_transactionspreadsheetbackup_download', args=[obj.pk])
        return format_html('<a class="button" href="{}">Download Backup</a>', url)

    def download_backup_view(self, request, backup_id):
        backup = self.get_object(request, backup_id)
        if backup is None or not backup.file:
            raise Http404('File backup tidak ditemukan.')

        file_handle = backup.file.open('rb')
        return FileResponse(
            file_handle,
            as_attachment=True,
            filename=backup.file.name.split('/')[-1],
        )
