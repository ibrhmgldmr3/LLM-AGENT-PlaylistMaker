"""Parca metnindeki YONERGE BENZERI kaliplarin taranmasi.

Neden gerekli: parca metnini kullanici yazmiyor. Bir videoyu yayinlayan ya da
bir PDF'i hazirlayan kisi, icine MODELE HITAP EDEN cumleler koyabilir ve o
metin dogrudan isteme giriyor (bkz. `docs/rag-plan.md` §7).

BU BIR ENGEL DEGIL, OLCU ALETI. Bulgular yalnizca loglaniyor; iki sebepten:

1. **Yanlis pozitif kacinilmaz VE MESRU.** Bu bir ogrenme araci: prompt
   injection ANLATAN bir video ya da makale bu kaliplarin hepsini icerir ve
   kullanicinin tam da onu sormaya hakki var. Boyle bir kaynagi sessizce
   baglamdan dusurmek, ozelligi en cok isine yarayacak kisi icin bozardi.
2. **Engelleme zaten BASKA katmanda.** Enjeksiyonun ise yaramasini onleyen sey
   bu tarayici degil; ayraclar (`build_rag_answer_prompt`) ve 3b kapisi
   (`rag_service._answer_grounding`) -- ikisi de deterministik ve ikisi de
   metnin ne dedigine bakmadan calisiyor. Tarayici "ne siklikta oluyor"
   sorusunu yanitlayip o katmanlarin kalibrasyonunu besliyor.

Yani `docs/rag-plan.md` §8'in dedigi sirayla: once olc, sonra karar ver.

Kaliplar IKI SINYAL ariyor (ornegin "yoksay" TEK BASINA yetmiyor): "bu terimi
yoksayabiliriz" diyen mesru bir ders metni gurultu uretmesin. Olcu aletinin en
kotu arizasi, herkesi surekli isaretleyip okunmaz hale gelmesi olurdu.
"""

from __future__ import annotations

import re

from src.utils.text_utils import transliterate


# Metin `transliterate().lower()` ile normalize ediliyor: Turkce `ı/ğ/ş/ç/ö/ü`
# Latin karsiligina iniyor, dolayisiyla kaliplar ASCII yazilabiliyor. Aramanin
# geri kalaninda kullanilan `search_key` BURADA KULLANILMIYOR cunku o bosluklari
# da sadelestiriyor ve satir basi capalari (`^system:`) kayboluyordu.
_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # "ignore the above instructions", "disregard all previous rules"
    (
        "ignore-instructions",
        re.compile(r"\b(ignore|disregard|forget|override)\b[^.\n]{0,40}"
                   r"\b(instruction|prompt|rule|context|above|previous|prior)"),
    ),
    # Turkce'de eylem sonda: "yukaridaki talimatlari yoksay", "tum kurallari unut"
    (
        "talimat-yoksay",
        re.compile(r"\b(onceki|yukarida\w*|tum|butun|hersey\w*)\b[^.\n]{0,40}"
                   r"\b(talimat\w*|yonerge\w*|kural\w*)\b[^.\n]{0,40}"
                   r"\b(yoksay\w*|unut\w*|gormezden|dikkate alma)"),
    ),
    # Rol taklidi. Satir basinda olmasi sart: normal metinde "system:" gecebilir.
    ("rol-taklidi", re.compile(r"(?m)^\s*(system|assistant|user|sistem|asistan)\s*:")),
    # "you are now a ...", "act as ...", "artik sen bir ...", "rolunu ustlen"
    (
        "persona-degistir",
        re.compile(r"\b(you are now|act as|pretend to be|artik sen\b|rolunu ustlen)"),
    ),
    # Istemi sizdirmaya yonelik istek.
    (
        "istem-sizdir",
        re.compile(r"\b(reveal|show|print|repeat|output)\b[^.\n]{0,30}"
                   r"\b(system prompt|your instruction|these instruction)"
                   r"|\b(sistem istemi\w*|talimatlarini)\b[^.\n]{0,20}\b(goster|yaz|sizdir)"),
    ),
    # Modelin CIKTISINI yonlendiren emirler: enjeksiyonun asil amaci genelde bu.
    (
        "cikti-yonlendir",
        re.compile(r"\b(always|instead|must)\s+(answer|say|reply|respond)\b"
                   r"|\b(su cumleyi yaz|sunu soyle|soyle cevap ver|yalnizca sunu)"),
    ),
)


def scan(text: str) -> list[str]:
    """Metinde eslesen kalip ADLARINI dondurur. Bos liste = isaret yok.

    Ad donuyor, eslesen METIN degil: log satirina kullanici kaynagindan alinti
    tasimak, `redact_secrets`in tum kod tabaninda engellemeye calistigi seyin
    ta kendisi olurdu.
    """
    if not text:
        return []
    normalized = transliterate(text).lower()
    return [name for name, pattern in _MARKERS if pattern.search(normalized)]
