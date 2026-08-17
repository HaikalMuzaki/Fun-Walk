from django.urls import path
from . import views
from django.contrib.auth.views import LogoutView
from django_cas_ng import views as cas_views

urlpatterns = [
    path('', views.index, name='index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.custom_logout, name='logout'),
    #path('login/sso/', cas_views.LoginView.as_view(), name='sso_login'),
    path('login/sso/', views.mock_sso_login, name='sso_login'),
    path('checkout/alumni/', views.checkout_alumni, name='checkout_alumni'),
    path('checkout/mahasiswa/', views.checkout_mahasiswa, name='checkout_mahasiswa'),
    path('checkout/tiket-saja/', views.checkout_non_paket, name='checkout_non_paket'),
    path('history/', views.history, name='history'),
]