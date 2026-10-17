import hashlib
import hmac
import secrets


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
    return f'scrypt${salt}${digest}'


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, expected = stored.split('$')
        actual = hashlib.scrypt(password.encode(), salt=salt.encode(), n=16384, r=8, p=1).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
