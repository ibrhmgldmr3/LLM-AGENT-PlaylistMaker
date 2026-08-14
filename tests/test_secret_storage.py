"""Faz 5: saklanan sirlarin sifrelenmesi.

`oauth_token` tablosu YouTube jetonlarini tutuyor; duz metin saklamak veritabani
dosyasina erisen herkesin kullanicinin hesabina yazabilmesi demek.
"""

import pytest

from src.storage import SQLiteStore
from src.storage.crypto import SecretBox, generate_key


@pytest.fixture
def key():
    return generate_key()


# --------------------------------------------------------------- SecretBox

def test_round_trip(key):
    box = SecretBox(key)
    assert box.decrypt(box.encrypt("gizli-jeton")) == "gizli-jeton"


def test_ciphertext_does_not_contain_the_plaintext(key):
    box = SecretBox(key)
    encrypted = box.encrypt("COK-GIZLI-DEGER")
    assert "COK-GIZLI-DEGER" not in encrypted


def test_same_plaintext_encrypts_differently(key):
    """Nonce sayesinde ayni deger her seferinde farkli sifrelenir."""
    box = SecretBox(key)
    assert box.encrypt("aynı") != box.encrypt("aynı")


def test_wrong_key_is_rejected():
    encrypted = SecretBox(generate_key()).encrypt("gizli")
    with pytest.raises(ValueError):
        SecretBox(generate_key()).decrypt(encrypted)


def test_tampering_is_detected(key):
    box = SecretBox(key)
    encrypted = box.encrypt("gizli")
    tampered = encrypted[:-4] + ("AAAA" if not encrypted.endswith("AAAA") else "BBBB")
    with pytest.raises(ValueError):
        box.decrypt(tampered)


def test_unicode_survives(key):
    box = SecretBox(key)
    value = '{"türkçe": "ığüşöç", "emoji": "🎵"}'
    assert box.decrypt(box.encrypt(value)) == value


def test_without_key_values_stay_plaintext():
    box = SecretBox(None)
    assert box.enabled is False
    assert box.encrypt("düz") == "düz"
    assert box.decrypt("düz") == "düz"


def test_plaintext_records_still_readable_after_enabling_encryption(key):
    """Anahtar SONRADAN eklenebilmeli: eski kayitlar okunmaya devam etsin."""
    assert SecretBox(key).decrypt("sifrelenmemis-eski-kayit") == "sifrelenmemis-eski-kayit"


def test_encrypted_record_without_key_gives_a_clear_error(key):
    encrypted = SecretBox(key).encrypt("gizli")
    with pytest.raises(ValueError, match="SECRET_ENCRYPTION_KEY"):
        SecretBox(None).decrypt(encrypted)


# ------------------------------------------------------------------- store

def test_store_encrypts_tokens_at_rest(tmp_path, key):
    """En kritik test: veritabani dosyasinda jeton duz metin GORUNMEMELI."""
    db = str(tmp_path / "app.db")
    store = SQLiteStore(db, encryption_key=key)
    store.save_oauth_token("local", "youtube", '{"refresh_token": "COK-GIZLI"}')

    with store.connect() as conn:
        stored = conn.execute("SELECT token_json FROM oauth_token").fetchone()["token_json"]

    assert "COK-GIZLI" not in stored
    assert stored.startswith("enc1:")
    # Ayni store dogru degeri geri vermeli
    assert store.get_oauth_token("local", "youtube") == '{"refresh_token": "COK-GIZLI"}'


def test_store_without_key_keeps_previous_behaviour(tmp_path):
    db = str(tmp_path / "app.db")
    store = SQLiteStore(db)
    store.save_oauth_token("local", "youtube", '{"t": 1}')

    with store.connect() as conn:
        stored = conn.execute("SELECT token_json FROM oauth_token").fetchone()["token_json"]

    assert stored == '{"t": 1}'


def test_key_can_be_added_to_an_existing_database(tmp_path, key):
    """Anahtarsiz yazilmis kayitlar, anahtar eklendikten sonra da okunabilmeli."""
    db = str(tmp_path / "app.db")
    SQLiteStore(db).save_oauth_token("local", "youtube", "eski-duz-jeton")

    upgraded = SQLiteStore(db, encryption_key=key)

    assert upgraded.get_oauth_token("local", "youtube") == "eski-duz-jeton"
    # Yeniden yazinca artik sifreli olmali
    upgraded.save_oauth_token("local", "youtube", "yeni-jeton")
    with upgraded.connect() as conn:
        stored = conn.execute("SELECT token_json FROM oauth_token").fetchone()["token_json"]
    assert stored.startswith("enc1:")


def test_generated_keys_are_unique():
    assert generate_key() != generate_key()
