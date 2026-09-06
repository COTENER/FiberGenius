import hashlib
import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils.timezone import now

from mapas.models import LoginThrottle


logger = logging.getLogger('fibergenius.security')


def client_ip(request):
    return request.META.get('REMOTE_ADDR', 'unknown')


def _hash_identity(kind, value):
    identity = f'{kind}|{str(value).strip().casefold()}'
    return hashlib.sha256(identity.encode('utf-8')).hexdigest()


def throttle_keys(username, ip_address):
    # Limita tanto ataques distribuidos contra una cuenta como intentos contra
    # múltiples cuentas desde una misma dirección.
    return (
        _hash_identity('user', username),
        _hash_identity('ip', ip_address),
    )


def lock_status(username, ip_address):
    current = now()
    record = (
        LoginThrottle.objects.filter(
            key_hash__in=throttle_keys(username, ip_address),
            locked_until__gt=current,
        )
        .order_by('-locked_until')
        .first()
    )
    if record:
        return True, max(1, int((record.locked_until - current).total_seconds()))
    return False, 0


@transaction.atomic
def register_failure(username, ip_address):
    current = now()
    window_seconds = max(
        60, int(settings.FIBERGENIUS_LOGIN_ATTEMPT_WINDOW_SECONDS)
    )
    max_attempts = max(1, int(settings.FIBERGENIUS_LOGIN_MAX_ATTEMPTS))
    lockout_seconds = max(60, int(settings.FIBERGENIUS_LOGIN_LOCKOUT_SECONDS))
    locked = False
    attempts_remaining = max_attempts
    for key_hash in throttle_keys(username, ip_address):
        LoginThrottle.objects.get_or_create(
            key_hash=key_hash,
            defaults={
                'attempts': 0,
                'window_started': current,
            },
        )
        record = LoginThrottle.objects.select_for_update().get(
            key_hash=key_hash
        )
        if current - record.window_started >= timedelta(
            seconds=window_seconds
        ):
            record.attempts = 0
            record.window_started = current
            record.locked_until = None

        record.attempts += 1
        if record.attempts >= max_attempts:
            record.locked_until = current + timedelta(seconds=lockout_seconds)
            locked = True
        attempts_remaining = min(
            attempts_remaining,
            max(0, max_attempts - record.attempts),
        )
        record.save()

    if locked:
        logger.warning(
            'login_locked user=%s ip=%s seconds=%s',
            username,
            ip_address,
            lockout_seconds,
        )
    return locked, attempts_remaining


def clear_failures(username, ip_address):
    LoginThrottle.objects.filter(
        key_hash__in=throttle_keys(username, ip_address)
    ).delete()
