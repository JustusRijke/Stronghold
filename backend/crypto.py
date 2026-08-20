"""Encryption for credential settings (db.SECRET_SETTINGS).

Encryption, not hashing: the WooCommerce client has to send the real key and
secret on every request, so the value must come back out. A hash could only
verify one, never produce it.

The key lives in its own file, outside the data file and gitignored -- the
whole point, since `inventory.sql` is committed to git. Losing it is a
recoverable accident, not a disaster: the encrypted values become unreadable
and the user re-enters them. Every failure path therefore returns None rather
than raising, so a missing key degrades to "not configured" instead of taking
the app down.

Fernet is AES-CBC plus an HMAC, so a value that was truncated or edited by hand
fails to decrypt rather than silently yielding garbage.
"""

import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

_log = logging.getLogger(__name__)

_fernet: Fernet | None = None


def init(path: Path) -> None:
    """Load the key file, creating it if this is the first run. Called from
    main.create_app, like db.init."""
    global _fernet
    _fernet = _load(path)


def _load(path: Path) -> Fernet | None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        # 0600 before the key is written: the file must never exist, even
        # briefly, with wider permissions
        path.touch(mode=0o600)
        path.write_bytes(Fernet.generate_key())
        _log.info("created secrets key file %s", path)
    try:
        return Fernet(path.read_bytes().strip())
    except (ValueError, OSError) as e:
        # a truncated, replaced or unreadable key. Say so loudly in the log and
        # carry on -- the only casualty is the encrypted settings
        _log.warning(
            "secrets key file %s is unusable (%s); stored credentials cannot be "
            "read and must be re-entered",
            path,
            e,
        )
        return None


def encrypt(value: str) -> str | None:
    """The value as a token, or None when there is no usable key."""
    if _fernet is None:
        return None
    return _fernet.encrypt(value.encode()).decode()


def decrypt(token: str) -> str | None:
    """The original value, or None when the key is missing or does not match
    this token (a rotated key, a hand-edited data file)."""
    if _fernet is None:
        return None
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        return None
