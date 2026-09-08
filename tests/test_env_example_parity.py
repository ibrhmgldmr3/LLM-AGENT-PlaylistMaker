"""`.env.example` ile `ENV_TO_FIELD` arasindaki kaymayi teste baglar.

Neden gerekli: `.env.example` bu projenin ayar YUZEYI -- 12 KB'lik, her
degiskeni gerekcesiyle anlatan bir belge, ve kurulum yapan herkesin okudugu
tek yer. Ayar eklemek IKI dosyaya dokunmak demek ve ikincisi unutuldugunda
hicbir sey patlamiyor: ozellik calisiyor, yalnizca kimse varligini bilmiyor.

Olcum: uc ayar (`CHANNEL_REPEAT_PENALTY`, `ENABLE_STUDY_NOTES`,
`STUDY_NOTE_TRANSCRIPT_CHAR_LIMIT`) tam boyle kaymisti -- readme'de
belgeliydiler ama sablonda yoklardi.

Ters yon de kilitleniyor: sablonda VAR olup kodda OKUNMAYAN bir degisken,
ayarladigini saniyorken hicbir sey degistirmeyen bir dugmedir.
"""

from __future__ import annotations

import re
from pathlib import Path

from src.config.settings import ENV_TO_FIELD

_ROOT = Path(__file__).resolve().parents[1]

# Sablonda mesru olarak bulunan ama `ENV_TO_FIELD` uzerinden OKUNMAYAN
# degiskenler. Her biri baska bir katmanin girdisi; listeye eklemek bilincli
# bir karar olmali, bu yuzden gerekceleri burada duruyor.
_NOT_IN_ENV_TO_FIELD = {
    # Depo/kuyruk secimi `create_store` ve is yurutucusu tarafindan dogrudan
    # okunuyor; uygulama yapilandirmasinin bir alani degil.
    "DATABASE_URL",
    "JOB_BACKEND",
    "REDIS_URL",
    "JOB_KEY_PREFIX",
    "JOB_TTL_SEC",
    # Surec ortamina ait; `AppConfig` alani degil.
    "ALLOW_UNSAFE_OPENMP_WORKAROUND",
    "PYTHONUTF8",
    "KMP_DUPLICATE_LIB_OK",
}


def _template_keys() -> set[str]:
    text = (_ROOT / ".env.example").read_text(encoding="utf-8")
    # Yorum satirina alinmis ornekler de sayiliyor (`# DATABASE_URL=...`):
    # belgelenmis olmalari yeterli, degeri bos birakmak mesru bir varsayilan.
    return set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]*)=", text, re.MULTILINE))


def test_every_setting_is_documented_in_the_template():
    missing = sorted(set(ENV_TO_FIELD) - _template_keys())
    assert not missing, (
        "Bu ayarlar koda eklendi ama .env.example'a eklenmedi, yani kurulum "
        f"yapan kimse varliklarini gormeyecek: {missing}"
    )


def test_the_template_has_no_variable_the_code_never_reads():
    extra = sorted(_template_keys() - set(ENV_TO_FIELD) - _NOT_IN_ENV_TO_FIELD)
    assert not extra, (
        "Bu degiskenler .env.example'da duruyor ama kod onlari okumuyor -- "
        f"ayarlandigini sanip hicbir sey degistirmeyen dugmeler: {extra}"
    )
