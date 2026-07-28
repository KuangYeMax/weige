from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from app.services.dispatch_generation import run_provider_generation


class _BlockingProvider:
    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0
        self.lock = threading.Lock()

    def generate(self, *_args, **_kwargs):
        with self.lock:
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)
        time.sleep(0.05)
        with self.lock:
            self.active -= 1
        return object()


def test_provider_calls_are_serialized_across_callers():
    provider = _BlockingProvider()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run_provider_generation, provider, "mock", None, "prompt", "1x1", None, True)
            for _ in range(2)
        ]
        for future in futures:
            future.result()

    assert provider.maximum_active == 1
