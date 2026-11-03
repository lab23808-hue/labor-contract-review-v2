from infra.rate_limiter import TokenBucket


def test_token_bucket_waits_when_empty():
    now = [0.0]
    sleeps = []

    def clock():
        return now[0]

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    bucket = TokenBucket(
        rate=2.0, capacity=1.0, clock=clock, sleep=sleep
    )

    bucket.acquire()
    bucket.acquire()
    bucket.acquire()

    assert sleeps == [0.5, 0.5]


def test_token_bucket_allows_burst_up_to_capacity():
    now = [0.0]
    sleeps = []

    def clock():
        return now[0]

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    bucket = TokenBucket(
        rate=2.0, capacity=2.0, clock=clock, sleep=sleep
    )

    bucket.acquire()
    bucket.acquire()
    assert sleeps == []
    bucket.acquire()
    assert sleeps == [0.5]
