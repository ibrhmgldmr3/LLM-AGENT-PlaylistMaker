from __future__ import annotations


class ProviderError(Exception):
    """Tum saglayici hatalarinin ortak atasi."""


class ProviderTemporaryError(ProviderError, RuntimeError):
    """Altyapi kaynakli, tekrar denenebilir hata (429, 5xx, ag hatasi, IP blogu).

    Bu hata saglayicinin ardisik hata sayacini arttirir ve esik asilirsa
    cooldown'a yol acar.
    """


class ProviderRateLimitedError(ProviderError):
    """Sunucu acikca "cok fazla istek" diyor (HTTP 429 / IP blogu).

    BILEREK `ProviderTemporaryError` turevi DEGILDIR: retry sarmalayicisi bunu
    yakalamamali. Hiz sinirina takilmisken tekrar denemek sorunu buyutur; dogru
    davranis saglayiciyi hemen dinlendirmektir.
    """

    def __init__(self, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class ProviderPermanentError(ProviderError):
    """Tekrar denemenin fayda etmeyecegi hata (gecersiz anahtar, kapali API).

    Bilerek `RuntimeError` turevi degildir; retry sarmalayicisi bunu yakalamaz.
    """


class VideoUnavailableError(ProviderError):
    """Videoya ozgu kalici durum (altyazi kapali, video silinmis, ozel).

    Saglayicinin saglik puanini ETKILEMEZ; sadece o video icin gecerlidir.
    """
