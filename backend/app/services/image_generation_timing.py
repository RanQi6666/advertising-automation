import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from time import perf_counter

logger = logging.getLogger(__name__)


def record_image_generation_timing(**fields: object) -> None:
    clean_fields = {key: value for key, value in fields.items() if value is not None}
    logger.info(
        "image_generation_timing",
        extra={"image_generation": clean_fields},
    )


@contextmanager
def image_generation_timer(**fields: object) -> Iterator[Callable[..., int]]:
    started = perf_counter()
    finished = False

    def finish(**finish_fields: object) -> int:
        nonlocal finished
        duration_ms = max(int((perf_counter() - started) * 1000), 0)
        record_image_generation_timing(
            **fields,
            **finish_fields,
            duration_ms=duration_ms,
        )
        finished = True
        return duration_ms

    try:
        yield finish
    except Exception as exc:
        if not finished:
            finish(
                status="failed",
                error_type=exc.__class__.__name__,
                error_message=str(exc),
            )
        raise
    else:
        if not finished:
            finish(status="succeeded")
