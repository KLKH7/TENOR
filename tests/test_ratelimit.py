from tenor.sources.ratelimit import FakeClock, RateLimiter


def _send_times(n: int) -> list[float]:
    clock = FakeClock()
    limiter = RateLimiter(clock)
    times = []
    for _ in range(n):
        limiter.acquire()
        times.append(clock.now())
    return times


def test_burst_of_25_is_immediate() -> None:
    times = _send_times(25)
    assert times == [0.0] * 25


def test_never_more_than_25_in_any_5s_window() -> None:
    times = _send_times(200)
    for i, t in enumerate(times):
        in_window = [u for u in times[: i + 1] if t - u < 5.0]
        assert len(in_window) <= 25, f"request {i} at t={t}"


def test_sustained_rate_is_5_per_second() -> None:
    times = _send_times(225)
    # After the initial burst, 200 more requests need >= 40 s at 5 req/s.
    assert times[-1] >= 40.0 - 1e-9
    # ...and we don't throttle much harder than necessary.
    assert times[-1] <= 45.0


def test_penalize_pauses_and_drains_bucket() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock)
    limiter.acquire()
    limiter.penalize(3.0)
    assert clock.now() == 3.0
    # 3 s after a drain there are tokens again only via refill since t=3.0, so the next
    # request waits for one token (0.2 s at 5/s).
    waited = limiter.acquire()
    assert abs(waited - 0.2) < 1e-9
