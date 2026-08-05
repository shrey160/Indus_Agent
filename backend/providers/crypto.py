import os

from cryptography.fernet import Fernet

MASK = "····"


class CryptoError(Exception):
    pass


def _fernet() -> Fernet:
    key = os.environ.get("SECRET_KEY", "")
    if not key:
        raise CryptoError("SECRET_KEY not set")
    try:
        return Fernet(key.encode())
    except Exception as exc:
        raise CryptoError(f"invalid SECRET_KEY: {exc}") from exc


def encrypt(plain: str) -> bytes:
    f = _fernet()
    token = f.encrypt(plain.encode())
    if isinstance(token, str):
        return token.encode()
    return token


def decrypt(enc: bytes | str) -> str:
    if isinstance(enc, str):
        enc = enc.encode()
    return _fernet().decrypt(enc).decode()


def mask(plain: str) -> str:
    if len(plain) <= 10:
        return plain[:6] + MASK
    return plain[:6] + MASK + plain[-4:]
