import sys
import types
from urllib import parse as urllib_parse


def ensure_django_six_compat():
    if 'django.utils.six.moves' in sys.modules:
        return

    six_module = types.ModuleType('django.utils.six')
    moves_module = types.ModuleType('django.utils.six.moves')
    moves_module.urllib_parse = urllib_parse
    six_module.moves = moves_module

    sys.modules.setdefault('django.utils.six', six_module)
    sys.modules.setdefault('django.utils.six.moves', moves_module)
