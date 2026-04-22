import random
import time
from typing import Callable, TypeVar

T = TypeVar("T")


def retry_with_backoff(
    func: Callable[[], T],
    attempts: int,
    base_delay: float,
    logger=None,
    on_exception: tuple[type[Exception], ...] = (Exception,),
) -> T:
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return func()
        except on_exception as exc:
            last_exc = exc
            if logger:
                logger.warning("Attempt %s/%s failed: %s", attempt, attempts, exc)
            if attempt == attempts:
                break
            delay = base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, 0.25 * base_delay)
            time.sleep(delay)
    raise last_exc
