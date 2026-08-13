import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")

MAX_BACKOFF_DELAY_SEC = 30.0


def retry_with_backoff(
    func: Callable[[], T],
    attempts: int,
    base_delay: float,
    logger=None,
    on_exception: tuple[type[Exception], ...] = (Exception,),
    max_delay: float = MAX_BACKOFF_DELAY_SEC,
) -> T:
    if attempts < 1:
        raise ValueError("retry_with_backoff requires attempts >= 1")

    last_exc: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except on_exception as exc:
            last_exc = exc
            if logger:
                logger.warning("Attempt %s/%s failed: %s", attempt, attempts, exc)
            if attempt == attempts:
                break
            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay += random.uniform(0, 0.25 * base_delay)
            time.sleep(delay)

    assert last_exc is not None  # attempts >= 1 oldugu icin buraya sadece hata ile gelinir
    raise last_exc
