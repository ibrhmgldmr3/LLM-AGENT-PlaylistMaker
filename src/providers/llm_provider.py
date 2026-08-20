from __future__ import annotations

import json
import warnings
from typing import Any, Protocol

from src.config import AppConfig
from src.providers.errors import (
    ProviderPermanentError,
    ProviderRateLimitedError,
    ProviderTemporaryError,
)


DEFAULT_SUBTOPIC_COUNT = 6

# Yapilandirilan model kullanilamazsa sirayla denenecek modeller. Liste bilerek
# birden fazla nesil iceriyor: Google modelleri yeni projelere kapatabiliyor
# (ornegin `gemini-2.5-flash` "no longer available to new users" donuyor), tek
# bir yedek model bu durumda yetmiyor.
FALLBACK_MODELS = ("gemini-3.7-flash", "gemini-flash-latest", "gemini-2.5-flash")

# Cikti SEMASI. Istem tek basina yetmiyordu: dort anahtar istendiginde model
# sonuncusunu (`terms`) sessizce atliyordu -- olculdu, 3 denemede 18 alt
# konunun 18'inde de eksikti. Ustelik yanit zaman zaman JSON'un ortasinda
# kesiliyor ve ayristirma patliyordu (5 denemenin 1'i).
#
# Sema, alanlarin varligini ISTEM DEGIL PROTOKOL duzeyinde zorunlu kiliyor.
SUBTOPIC_SCHEMA: dict[str, Any] = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "title": {"type": "STRING"},
            "query": {"type": "STRING"},
            "query_en": {"type": "STRING"},
            "terms": {"type": "ARRAY", "items": {"type": "STRING"}},
        },
        "required": ["title", "query", "query_en", "terms"],
    },
}

# DUSUNME TOKENLARI DA BU BUTCEDEN HARCANIYOR -- olculdu: 6 alt konu icin
# `thoughts_token_count` 1288-1474, cevabin kendisi ise yalnizca ~400-470 token.
# Sinir 2048 iken toplam ona dayaniyor ve dusunme uzun surdugu denemelerde
# cevap JSON'un ORTASINDAN kesiliyordu; `finish_reason` yine STOP donduğu icin
# bu bir hata gibi de gorunmuyor, yalnizca ayristirma patliyordu.
#
# Genis birakildi: dusunme + cevap toplami ~2000, buradaki pay dort kati.
MAX_OUTPUT_TOKENS = 8192


def build_subtopic_prompt(topic: str, language: str, max_items: int) -> str:
    """Alt konu istemi. IKI saglayici da BUNU kullaniyor.

    Istem eskiden `GeminiLLMProvider`in icine gomuluydu; ikinci saglayici
    eklenince ya kopyalanacakti ya da ikisi sessizce ayrisacakti.

    ISTEM `terms`I ARTIK ACIKCA ISTIYOR. Onceden istemiyordu ama alan yine de
    geliyordu, cunku Gemini'de `response_schema` onu PROTOKOL duzeyinde zorunlu
    kiliyor -- yani istem ile sema CELISIYORDU ("exactly three keys" deyip dort
    alanli sema gonderiliyordu). Gemini'de sema kazandigi icin fark edilmemisti.
    OpenAI uyumlu uclarda sema zorlamasi modele gore degistigi icin ayni celiski
    Together'da alanin sessizce kaybolmasi demek olurdu.
    """
    return (
        "You are planning a YouTube learning playlist.\n"
        f"Return a JSON array of exactly {max_items} objects covering the topic "
        "end-to-end, ordered from foundational to advanced.\n"
        'Each object has exactly four keys: "title", "query", "query_en" and "terms".\n'
        f'- "title": the subtopic label, 2-6 words, written in {language}. '
        "Each title must name a DISTINCT concept, method or tool. Do not repeat the "
        "topic wording in every title.\n"
        '- "query": the YouTube search query most likely to surface good teaching '
        "videos for that subtopic. Use the terms people actually search for, include "
        "the distinctive keyword, and drop filler words. Keep it under 8 words. "
        f"Write it in {language}, keeping proper nouns and technical terms in their "
        "original form (product names, library names, algorithm acronyms).\n"
        '- "query_en": the same search intent expressed as an English query, under '
        "8 words. Used to widen the candidate pool with English teaching material.\n"
        '- "terms": a JSON array of 2-5 OTHER NAMES for the same concept - its '
        "English equivalent, its acronym, and widely used synonyms. These are matched "
        "against video titles, so give the forms that actually appear there. Example: "
        'for the Turkish title "Kokusuz Kalman Filtresi" return '
        '["Unscented Kalman Filter", "UKF"]. Return [] only when the title is already '
        "the single common name.\n"
        f"Topic: {topic}"
    )


STUDY_NOTE_SYSTEM_INSTRUCTION = (
    "You write study notes from a single video transcript. Use ONLY the "
    "transcript excerpt you are given -- never add outside facts, numbers, "
    "dates, or claims that are not stated in it. If the excerpt does not "
    "cover something, do not guess or fill the gap. Output plain Markdown "
    "with no preamble and no closing remarks."
)


def build_study_note_prompt(
    topic: str, subtopic: str, video_title: str, transcript: str, language: str, max_chars: int
) -> str:
    """Calisma notu istemi. IKI saglayici da BUNU kullaniyor (bkz. `build_subtopic_prompt`).

    ISTEM UYDURMAYI ACIKCA YASAKLIYOR: transkripti olmayan bir video icin not
    hic uretilmiyor (cagiran tarafta `status="no_transcript"`), ama transkripti
    OLAN bir videoda bile model transkriptte olmayan bir seyi ekleyebilir.
    "Yalnizca transkriptte olani kullan" talimati bunu azaltiyor, garantilemez
    -- calisma notlari bu yuzden UI'da "video ozetiyse dogrula" seklinde
    sunuluyor, otorite iddiasiyla degil.
    """
    excerpt = transcript[:max_chars].strip()
    truncated_note = (
        "\n\n[NOT: transkript burada kesildi; devaminda ne oldugunu VARSAYMA.]"
        if len(transcript) > max_chars
        else ""
    )
    return (
        "You are creating STUDY NOTES for a learner from a single YouTube video transcript.\n"
        f"Write entirely in {language}.\n"
        "Use ONLY the transcript excerpt below. Do not add outside facts, numbers, dates, "
        "or claims that are not stated in it. If the excerpt does not cover something, do "
        "not guess.\n"
        "Structure the output as Markdown:\n"
        "1. One sentence overview of what this video covers.\n"
        "2. 4-8 bullet points with the key ideas, in the order they appear in the transcript.\n"
        "3. If the transcript names specific terms, tools, or formulas, list them under a "
        "'Terms' heading.\n"
        "No preamble, no closing remarks, no meta-commentary about being an AI.\n\n"
        f"Playlist topic: {topic}\n"
        f"Subtopic this video was selected for: {subtopic}\n"
        f"Video title: {video_title}\n\n"
        f"Transcript excerpt:\n{excerpt}{truncated_note}"
    )


class LLMProvider(Protocol):
    def generate_subtopics(self, topic: str, language: str, max_items: int = DEFAULT_SUBTOPIC_COUNT) -> list[str]:
        raise NotImplementedError

    def generate_study_note(
        self, topic: str, subtopic: str, video_title: str, transcript_text: str, language: str
    ) -> str:
        raise NotImplementedError


class GeminiLLMProvider:
    def __init__(self, config: AppConfig):
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message=r".*<built-in function any> is not a Python type.*",
                category=UserWarning,
            )
            try:
                from google import genai
            except ImportError as exc:
                raise ProviderPermanentError(
                    "`google-genai` paketi kurulu değil. `pip install -r requirements.txt` çalıştırın."
                ) from exc

        self.config = config
        self.client = genai.Client(api_key=config.gemini_api_key)
        # Bir model `thinking_config`'i reddederse (400) o model icin bir daha gonderme.
        self._thinking_unsupported: set[str] = set()

    def generate_subtopics(
        self, topic: str, language: str, max_items: int = DEFAULT_SUBTOPIC_COUNT
    ) -> list[str]:
        prompt = build_subtopic_prompt(topic, language, max_items)

        errors: list[str] = []
        for model_name in self._candidate_models():
            try:
                text = self._generate(model_name, prompt, self._generation_config)
            except ProviderPermanentError:
                raise
            except Exception as exc:
                errors.append(f"{model_name}: {exc}")
                if _is_model_unavailable_error(exc):
                    # Bu model bu proje icin yok; siradakini dene.
                    continue
                raise _classify_gemini_error(exc) from exc
            return _parse_subtopics(text)[:max_items]
        raise ProviderPermanentError("Kullanılabilir Gemini modeli bulunamadı. " + " | ".join(errors))

    def _generate(self, model_name: str, prompt: str, config_factory) -> str:
        """`config_factory(model_name)` cagrilip Gemini'ye gonderilir.

        Fabrika olarak alinmasinin sebebi: `thinking_config` bazi modellerde
        400 doner ve modeli elemek yerine parametresiz TEKRAR denenir. Ikinci
        denemede config'in yeniden hesaplanmasi gerekiyor (`_thinking_unsupported`
        setine eklenen model artik farkli bir config aliyor); sabit bir dict
        yerine fabrika bunu tek yerde saglıyor. Iki cagiran (alt konu uretimi ve
        calisma notu) FARKLI config'ler kullaniyor, bu yuzden fabrika parametrik.
        """
        # Karar SORULDUGU AN aliniyor, `except` icinde degil: `_thinking_unsupported`
        # is parcaciklari arasinda PAYLASILIYOR ve iki thread ayni modeli ilk kez
        # es zamanli cagirirsa, A'nin basarili retry'i modeli sete ekleyip B'nin
        # kendi retry hakkini elinden aliyordu -- B, hic gondermedigi bir
        # parametre yuzunden "desteklenmiyor" diye `raise` ediyordu. Bu calisma
        # notu uretiminde (`max_transcript_workers > 1`) sahte kalici hataya
        # donusuyordu. Artik olcut "BU cagri thinking_config gonderdi mi".
        sent_thinking = model_name not in self._thinking_unsupported
        try:
            response = self.client.models.generate_content(
                model=model_name, contents=prompt, config=config_factory(model_name)
            )
        except Exception as exc:
            # Bazi modeller `thinking_config`'i hic kabul etmiyor ve 400 donuyor.
            # Bu bir yapilandirma uyumsuzlugu; modeli elemek yerine parametresiz tekrar dene.
            if _is_invalid_argument_error(exc) and sent_thinking:
                # `set.add` CPython'da atomik; ayrica kilide gerek yok.
                self._thinking_unsupported.add(model_name)
                response = self.client.models.generate_content(
                    model=model_name, contents=prompt, config=config_factory(model_name)
                )
            else:
                raise

        text = (response.text or "").strip()
        if not text:
            raise ProviderTemporaryError("Empty Gemini response")
        return text

    def generate_study_note(
        self, topic: str, subtopic: str, video_title: str, transcript_text: str, language: str
    ) -> str:
        prompt = build_study_note_prompt(
            topic, subtopic, video_title, transcript_text, language,
            self.config.study_note_transcript_char_limit,
        )

        errors: list[str] = []
        for model_name in self._candidate_models():
            try:
                text = self._generate(model_name, prompt, self._study_note_generation_config)
            except ProviderPermanentError:
                raise
            except Exception as exc:
                errors.append(f"{model_name}: {exc}")
                if _is_model_unavailable_error(exc):
                    continue
                raise _classify_gemini_error(exc) from exc
            return text
        raise ProviderPermanentError("Kullanılabilir Gemini modeli bulunamadı. " + " | ".join(errors))

    def _study_note_generation_config(self, model_name: str) -> dict[str, Any]:
        """Calisma notu icin ayri config: SERBEST METIN, JSON semasi YOK.

        Alt konu uretiminden ayrilmasinin sebebi: o sema-zorlamali (bkz.
        `_generation_config`), bu ise duz Markdown yaziyor. Ikisini tek config
        fonksiyonunda birlestirmek, birinin ayarinin digerine sizmasina yol acardi.
        """
        config: dict[str, Any] = {
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "system_instruction": STUDY_NOTE_SYSTEM_INSTRUCTION,
            "temperature": 0.3,
        }
        if self.config.gemini_thinking_budget >= 0 and model_name not in self._thinking_unsupported:
            config["thinking_config"] = {"thinking_budget": self.config.gemini_thinking_budget}
        return config

    def _generation_config(self, model_name: str) -> dict[str, Any]:
        config: dict[str, Any] = {
            "response_mime_type": "application/json",
            "response_schema": SUBTOPIC_SCHEMA,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "system_instruction": "Return only valid JSON. No prose, no markdown fences.",
            "temperature": 0.4,
        }
        # Negatif deger = ayari hic gonderme (varsayilan). Olcumlerde `thinking_budget=0`
        # kabul eden modelde hiz kazanci saglamadi, kabul etmeyen modelleri ise
        # tamamen kirdi; bu yuzden opt-in birakildi.
        if self.config.gemini_thinking_budget >= 0 and model_name not in self._thinking_unsupported:
            config["thinking_config"] = {"thinking_budget": self.config.gemini_thinking_budget}
        return config

    def _candidate_models(self) -> list[str]:
        models = [self.config.gemini_model.strip()]
        for fallback in FALLBACK_MODELS:
            if fallback not in models:
                models.append(fallback)
        return models


def _parse_subtopics(text: str) -> list[dict[str, str]]:
    """Gemini yanitini {"title", "query"} sozluklerine cevirir.

    Model bazen diziyi bir nesnenin icine sarmalayabiliyor, bazen de duz metin
    listesi donebiliyor; her bicimi kabul et.
    """
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProviderTemporaryError(f"Gemini response was not valid JSON: {exc}") from exc

    if isinstance(payload, dict):
        for value in payload.values():
            if isinstance(value, list):
                payload = value
                break
    if not isinstance(payload, list):
        raise ProviderTemporaryError("Gemini response did not contain a JSON array")

    items: list[dict[str, object]] = []
    for entry in payload:
        query_en = ""
        terms: list[str] = []
        if isinstance(entry, dict):
            title = entry.get("title") or entry.get("subtopic") or entry.get("name") or ""
            query = entry.get("query") or entry.get("search_query") or ""
            query_en = entry.get("query_en") or entry.get("english_query") or ""
            raw_terms = entry.get("terms") or entry.get("aliases") or []
            if isinstance(raw_terms, str):
                # Model bazen dizi yerine virgullu tek metin donuyor.
                raw_terms = [part for part in raw_terms.split(",")]
            if isinstance(raw_terms, list):
                terms = [str(x).strip() for x in raw_terms if str(x).strip()]
            if not title:
                # Sadece tek bir metin alani varsa onu baslik say.
                title = next((v for v in entry.values() if isinstance(v, str)), "")
        else:
            title, query = entry, ""
        title = str(title).strip()
        if title:
            items.append(
                {
                    "title": title,
                    "query": str(query).strip(),
                    "query_en": str(query_en).strip(),
                    "terms": terms,
                }
            )
    return items


# Tekrar denemenin fayda etmeyecegi Gemini hatalari. Ozellikle "krediler tukendi"
# 429 ile geliyor ama gecici DEGIL: bakiye yuklenene kadar her deneme basarisiz olur.
_PERMANENT_GEMINI_HINTS = (
    "credits are depleted",
    "billing",
    "api key not valid",
    "api_key_invalid",
    "permission_denied",
    "invalid_argument",
    "consumer_suspended",
    "quota exceeded for quota metric",
)


def _classify_gemini_error(exc: Exception) -> Exception:
    message = str(exc).lower()
    if any(hint in message for hint in _PERMANENT_GEMINI_HINTS):
        return ProviderPermanentError(f"Gemini isteği kalıcı olarak reddedildi: {exc}")
    return ProviderTemporaryError(f"Gemini request failed: {exc}")


def _is_model_unavailable_error(exc: Exception) -> bool:
    """Model bu proje/anahtar icin kullanilamiyor mu?

    404 NOT_FOUND'un yani sira "no longer available to new users" mesajini da
    kapsar: Google eski modelleri yeni projelere kapatiyor ve bu durumda tek bir
    yedek model yetmiyor.
    """
    message = str(exc).lower()
    return (
        "not_found" in message
        or "is not found for api version" in message
        or "no longer available" in message
        or "404" in message
    )


def _is_invalid_argument_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "invalid_argument" in message or "invalid argument" in message


# --------------------------------------------------------------------- #
# Together.ai
#
# Together OpenAI UYUMLU bir uc sunuyor, bu yuzden yeni bir SDK bagimliligi
# eklenmedi: `requests` zaten projede var. Tek bir POST istegi icin ayri bir
# istemci kutuphanesi tasimak kurulum yuzeyini bedelsiz buyuturdu.
# --------------------------------------------------------------------- #

TOGETHER_URL = "https://api.together.xyz/v1/chat/completions"

# Gemini'nin sema bicimi kendine ozgu (BUYUK harf tipler). OpenAI uyumlu uclar
# STANDART JSON Schema bekliyor. Ayni sey iki kez degil: ayni SOZLESMENIN iki
# lehcesi -- alanlar ve zorunluluklar birebir ayni.
SUBTOPIC_JSON_SCHEMA: dict[str, Any] = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "query": {"type": "string"},
            "query_en": {"type": "string"},
            "terms": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["title", "query", "query_en", "terms"],
    },
}

# 4xx'in tamami kalici DEGIL: 429 hiz siniri, 408/409 gecici cakisma. Ayrimi
# yapmamak, gecici bir sikisikligi kalici hata sayip calistirmayi bosuna
# dusururdu -- `youtube_search_service` icin de ayni ayrim yapilmisti.
_TOGETHER_RETRYABLE_STATUS = frozenset({408, 409, 500, 502, 503, 504})


def _classify_together_error(status_code: int, body: str) -> Exception:
    detail = body[:300]
    if status_code == 429:
        return ProviderRateLimitedError(f"Together hız sınırı: {detail}")
    if status_code in _TOGETHER_RETRYABLE_STATUS:
        return ProviderTemporaryError(f"Together geçici hata {status_code}: {detail}")
    return ProviderPermanentError(f"Together isteği reddedildi ({status_code}): {detail}")


class TogetherLLMProvider:
    """Together.ai uzerinden alt konu uretimi.

    `GeminiLLMProvider` ile AYNI protokolu ve AYNI istemi kullaniyor; cagiran
    taraf hangisinin devrede oldugunu bilmiyor.
    """

    def __init__(self, config: AppConfig):
        if not config.together_api_key:
            raise ProviderPermanentError("TOGETHER_API_KEY tanımlı değil")
        self.config = config

    def generate_subtopics(
        self, topic: str, language: str, max_items: int = DEFAULT_SUBTOPIC_COUNT
    ) -> list[dict[str, Any]]:
        prompt = build_subtopic_prompt(topic, language, max_items)
        text = self._generate(
            prompt,
            system_instruction="Return only valid JSON. No prose, no markdown fences.",
            response_format={"type": "json_object", "schema": SUBTOPIC_JSON_SCHEMA},
        )
        return _parse_subtopics(text)[:max_items]

    def generate_study_note(
        self, topic: str, subtopic: str, video_title: str, transcript_text: str, language: str
    ) -> str:
        prompt = build_study_note_prompt(
            topic, subtopic, video_title, transcript_text, language,
            self.config.study_note_transcript_char_limit,
        )
        # `response_format` verilmiyor: calisma notu SERBEST METIN, JSON degil.
        return self._generate(prompt, system_instruction=STUDY_NOTE_SYSTEM_INSTRUCTION)

    def _generate(
        self, prompt: str, system_instruction: str, response_format: dict[str, Any] | None = None
    ) -> str:
        import requests

        payload: dict[str, Any] = {
            "model": self.config.together_model,
            "messages": [
                {"role": "system", "content": system_instruction},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.4,
            "max_tokens": MAX_OUTPUT_TOKENS,
        }
        if response_format is not None:
            payload["response_format"] = response_format
        try:
            response = requests.post(
                TOGETHER_URL,
                json=payload,
                headers={"Authorization": f"Bearer {self.config.together_api_key}"},
                timeout=self.config.request_timeout_sec,
            )
        except requests.RequestException as exc:
            raise ProviderTemporaryError(f"Together isteği başarısız: {exc}") from exc

        if response.status_code >= 400:
            raise _classify_together_error(response.status_code, response.text)

        try:
            text = response.json()["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError) as exc:
            raise ProviderTemporaryError(f"Together yanıtı beklenen biçimde değil: {exc}") from exc

        text = (text or "").strip()
        if not text:
            raise ProviderTemporaryError("Empty Together response")
        return text


def create_llm_provider(config: AppConfig) -> LLMProvider:
    """Yapilandirmaya gore LLM saglayicisini kurar.

    Tek kurulum noktasi: `playlist_service` eskiden `GeminiLLMProvider`i
    DOGRUDAN kuruyordu, yani saglayici degistirmek servis kodunu elden gecirmek
    demekti.
    """
    if config.llm_provider == "together":
        return TogetherLLMProvider(config)
    return GeminiLLMProvider(config)
