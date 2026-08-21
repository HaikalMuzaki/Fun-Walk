from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.custom_logout, name='logout'),
    path('login/sso/', views.sso_login, name='sso_login'),
    # path('login/sso/callback/', views.sso_login_callback, name='sso_login_callback'),
    path('checkout/alumni/', views.checkout_alumni, name='checkout_alumni'),
    path('checkout/mahasiswa/', views.checkout_mahasiswa, name='checkout_mahasiswa'),
    path('checkout/tiket-saja/', views.checkout_non_paket, name='checkout_non_paket'),
    path('history/', views.history, name='history'),
    path('payment/', views.payment_page, name='payment_page'),
    path('callback/payment/', views.payment_callback, name='payment_callback'),
]
