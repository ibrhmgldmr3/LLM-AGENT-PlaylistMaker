"""Saklanan sirlarin sifrelenmesi.

`oauth_token` tablosu YouTube jetonlarini tutuyor. Duz metin saklamak, veritabani
dosyasina erisen herkesin kullanicinin YouTube hesabina yazabilmesi demek.

Neden harici bir kripto kutuphanesi degil: `cryptography` agir bir bagimlilik ve
burada ihtiyac duyulan sey dar — bilinen bir anahtarla simetrik sifreleme ve
kurcalanma tespiti. Standart kutuphanedeki HMAC + SHA-256 anahtar akisi bunu
karsiliyor ve bagimlilik eklemiyor.

SINIR: bu, veritabanini calan birine karsi korur; sunucuya kod calistirma
yetkisiyle giren birine karsi KORUMAZ (anahtar da orada). Gercek koruma icin
anahtari bir KMS/secret manager'da tutup surece disaridan verin.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

_MAGIC = b"enc1:"
_NONCE_BYTES = 16
_TAG_BYTES = 32


class SecretBox:
    """Anahtar tabanli sifreleme. Anahtar yoksa deger duz metin kalir."""

    def __init__(self, key: str | None):
        self._key = key.encode("utf-8") if key else None

    @property
    def enabled(self) -> bool:
        return self._key is not None

    def encrypt(self, plaintext: str) -> str:
        if self._key is None:
            return plaintext
        nonce = secrets.token_bytes(_NONCE_BYTES)
        data = plaintext.encode("utf-8")
        cipher = bytes(a ^ b for a, b in zip(data, self._keystream(nonce, len(data))))
        tag = hmac.new(self._subkey(b"auth", nonce), nonce + cipher, hashlib.sha256).digest()
        return (_MAGIC + base64.b64encode(nonce + tag + cipher)).decode("ascii")

    def decrypt(self, stored: str) -> str:
        raw = stored.encode("ascii", errors="ignore")
        if not raw.startswith(_MAGIC):
            # Sifrelemeden once yazilmis kayit: oldugu gibi don (geriye donuk uyum).
            return stored
        if self._key is None:
            raise ValueError(
                "Kayıt şifreli ama SECRET_ENCRYPTION_KEY tanımlı değil. "
                "Anahtarı geri koyun ya da bağlı hesapları yeniden yetkilendirin."
            )
        blob = base64.b64decode(raw[len(_MAGIC) :])
        nonce, tag, cipher = (
            blob[:_NONCE_BYTES],
            blob[_NONCE_BYTES : _NONCE_BYTES + _TAG_BYTES],
            blob[_NONCE_BYTES + _TAG_BYTES :],
        )
        expected = hmac.new(self._subkey(b"auth", nonce), nonce + cipher, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("Şifreli kayıt doğrulanamadı (anahtar yanlış ya da veri bozulmuş)")
        data = bytes(a ^ b for a, b in zip(cipher, self._keystream(nonce, len(cipher))))
        return data.decode("utf-8")

    # ------------------------------------------------------------- ic isler
    def _subkey(self, label: bytes, nonce: bytes) -> bytes:
        return hmac.new(self._key, label + nonce, hashlib.sha256).digest()

    def _keystream(self, nonce: bytes, length: int) -> bytes:
        """HMAC-SHA256 sayac modunda anahtar akisi."""
        key = self._subkey(b"stream", nonce)
        out = bytearray()
        counter = 0
        while len(out) < length:
            out += hmac.new(key, counter.to_bytes(8, "big"), hashlib.sha256).digest()
            counter += 1
        return bytes(out[:length])


def generate_key() -> str:
    """Yeni bir sifreleme anahtari uretir (`.env`'e konacak)."""
    return base64.urlsafe_b64encode(os.urandom(32)).decode("ascii").rstrip("=")


if __name__ == "__main__":  # pragma: no cover - kolaylik betigi
    print(f"SECRET_ENCRYPTION_KEY={generate_key()}")
