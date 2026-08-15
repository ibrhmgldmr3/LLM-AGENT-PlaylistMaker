from __future__ import annotations

import json
import warnings
from typing import Any, Protocol

from src.config import AppConfig
from src.providers.errors import ProviderPermanentError, ProviderTemporaryError


DEFAULT_SUBTOPIC_COUNT = 6

# Yapilandirilan model kullanilamazsa sirayla denenecek modeller. Liste bilerek
# birden fazla nesil iceriyor: Google modelleri yeni projelere kapatabiliyor
# (ornegin `gemini-2.5-flash` "no longer available to new users" donuyor), tek
# bir yedek model bu durumda yetmiyor.
FALLBACK_MODELS = ("gemini-3.7-flash", "gemini-flash-latest", "gemini-2.5-flash")


class LLMProvider(Protocol):
    def generate_subtopics(self, topic: str, language: str, max_items: int = DEFAULT_SUBTOPIC_COUNT) -> list[str]:
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
        prompt = (
            "You are planning a YouTube learning playlist.\n"
            f"Return a JSON array of exactly {max_items} objects covering the topic "
            "end-to-end, ordered from foundational to advanced.\n"
            'Each object has exactly three keys: "title", "query" and "query_en".\n'
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
            f"Topic: {topic}"
        )

        errors: list[str] = []
        for model_name in self._candidate_models():
            try:
                text = self._generate(model_name, prompt)
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

    def _generate(self, model_name: str, prompt: str) -> str:
        try:
            response = self.client.models.generate_content(
                model=model_name, contents=prompt, config=self._generation_config(model_name)
            )
        except Exception as exc:
            # Bazi modeller `thinking_config`'i hic kabul etmiyor ve 400 donuyor.
            # Bu bir yapilandirma uyumsuzlugu; modeli elemek yerine parametresiz tekrar dene.
            if _is_invalid_argument_error(exc) and model_name not in self._thinking_unsupported:
                self._thinking_unsupported.add(model_name)
                response = self.client.models.generate_content(
                    model=model_name, contents=prompt, config=self._generation_config(model_name)
                )
            else:
                raise

        text = (response.text or "").strip()
        if not text:
            raise ProviderTemporaryError("Empty Gemini response")
        return text

    def _generation_config(self, model_name: str) -> dict[str, Any]:
        config: dict[str, Any] = {
            "response_mime_type": "application/json",
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
