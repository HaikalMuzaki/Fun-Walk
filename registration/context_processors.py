from django.conf import settings


def payment_gateway_maintenance(request):
    return {
        'payment_gateway_maintenance': settings.PAYMENT_GATEWAY_MAINTENANCE,
        'payment_gateway_maintenance_message': settings.PAYMENT_GATEWAY_MAINTENANCE_MESSAGE,
    }
