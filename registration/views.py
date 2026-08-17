from django.shortcuts import render, redirect
from django.contrib.auth import authenticate, login, logout, get_user_model
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User

User = get_user_model()

def index(request):
    return render(request, 'registration/index.html')

def login_view(request):
    # Kalau sudah login, langsung lempar ke halaman depan
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        email = request.POST.get('email')
        password = request.POST.get('password')
        # Menangkap URL tujuan awal sebelum dicegat (contoh: /checkout/alumni/)
        next_url = request.POST.get('next', 'index') 

        if not email or not password:
            messages.error(request, "Email dan password wajib diisi.")
            return render(request, 'registration/login.html')

        try:
            # Skenario 1: Email sudah terdaftar -> Proses Login
            user_exists = User.objects.get(email=email)
            # Karena Django by default mencari 'username', kita passing email ke parameter username
            auth_user = authenticate(request, username=email, password=password) 
            
            if auth_user is not None:
                login(request, auth_user)
                return redirect(next_url)
            else:
                messages.error(request, "Password salah. Silakan coba lagi.")
                
        except User.DoesNotExist:
            # Skenario 2: Email belum ada -> Proses Register sekaligus Login
            # Kita 'akali' requirement Django dengan memasukkan email ke kolom username juga
            new_user = User.objects.create_user(username=email, email=email, password=password)
            auth_user = authenticate(request, username=email, password=password)
            login(request, auth_user)
            return redirect(next_url)

    return render(request, 'registration/login.html')

@login_required
def checkout_alumni(request):
    return render(request, 'registration/checkout-alumni.html')

@login_required
def checkout_mahasiswa(request):
    return render(request, 'registration/checkout-mahasiswa.html')

@login_required
def checkout_non_paket(request):
    return render(request, 'registration/checkout-non-paket.html')

@login_required
def history(request):
    return render(request, 'registration/history.html')

def custom_logout(request):
    logout(request) # Menghapus sesi user
    return redirect('index')

def mock_sso_login(request):
    """
    Fungsi tiruan (Mock) untuk bypass SSO UI selama Localhost belum di-whitelist.
    """
    # 1. Tentukan identitas akun dummy
    dummy_username = "mahasiswa.dummy"
    dummy_email = "mahasiswa.dummy@ui.ac.id"
    
    # 2. Cari atau buat user otomatis di database lokal
    user, created = User.objects.get_or_create(
        username=dummy_username,
        defaults={'email': dummy_email}
    )
    
    # Kalau user baru dibuat, set passwordnya agar tidak bisa dipakai login manual biasa
    if created:
        user.set_unusable_password()
        user.save()
        
    # 3. Paksa login tanpa password (bypass)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    
    # 4. Ambil parameter 'next' dari URL (contoh: /checkout/alumni/). 
    # Kalau tidak ada, default lemparkan ke halaman utama ('/')
    next_url = request.GET.get('next', '/')
    return redirect(next_url)