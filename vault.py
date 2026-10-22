# encrypts the text lookout saves (parts of removed messages, case notes, nicknames), so the database file on
# its own gives nothing away. the key is DATA_KEY in .env, made on the first start if it's not there
import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

import config

log = logging.getLogger("lookout.vault")
PREFIX = "enc1:"  # marks encrypted text, anything without it was saved before encryption


def _key() -> bytes:
    key = getattr(config, "DATA_KEY", "")
    if key:
        return key.encode()
    new = Fernet.generate_key()
    env = Path(config.DB_PATH).parent / ".env"
    try:
        with open(env, "a", encoding="utf-8") as f:
            f.write(f"\nDATA_KEY={new.decode()}\n")
        log.warning("Made a new DATA_KEY and saved it in %s. Keep a copy of it somewhere safe. Without it, "
                    "the saved case text can't be read.", env)
    except OSError as e:
        log.error("Couldn't save the new DATA_KEY to %s (%s). Text saved now can't be read after a restart.", env, e)
    return new


_fernet = Fernet(_key())


def seal(text: str | None) -> str | None:
    if not text:
        return text
    return PREFIX + _fernet.encrypt(text.encode()).decode()


def unseal(text: str | None) -> str | None:
    if not text or not text.startswith(PREFIX):
        return text
    try:
        return _fernet.decrypt(text[len(PREFIX):].encode()).decode()
    except InvalidToken:  # a different DATA_KEY than the one it was saved with
        return "(couldn't be read)"
