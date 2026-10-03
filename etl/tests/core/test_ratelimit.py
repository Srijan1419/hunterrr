"""ratelimit: token bucket with injectable clock, no real waiting."""

import time

from etl.core.ratelimit import TokenBucket


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept: list[float] = []

    def __call__(self):
        return self.t

    async def sleep(self, delay: float):
        self.slept.append(delay)
        self.t += delay


def test_take_and_refill():
    clock = FakeClock()
    bucket = TokenBucket(rate=2.0, capacity=2.0, clock=clock)
    assert bucket.take() is True
    assert bucket.take() is True
    assert bucket.take() is False
    clock.t += 0.5  # +1 token
    assert bucket.take() is True
    assert bucket.take() is False


def test_time_until():
    clock = FakeClock()
    bucket = TokenBucket(rate=1.0, capacity=1.0, clock=clock)
    assert bucket.time_until() == 0.0
    assert bucket.take() is True
    assert bucket.time_until() == 1.0


async def test_acquire_advances_fake_clock_without_real_sleep():
    clock = FakeClock()
    bucket = TokenBucket(rate=10.0, capacity=1.0, clock=clock)
    assert bucket.take() is True
    real_start = time.perf_counter()
    await bucket.acquire(sleep=clock.sleep)
    assert time.perf_counter() - real_start < 0.2
    assert clock.slept and abs(clock.slept[0] - 0.1) < 1e-6
