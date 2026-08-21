import unittest

from rate_limiter import TokenBucket


class TestTokenBucket(unittest.TestCase):
    def test_starts_full(self):
        b = TokenBucket(capacity=5, refill_per_sec=5)
        self.assertEqual(5.0, b.tokens)
        self.assertAlmostEqual(1.0, b.level())

    def test_try_acquire_consumes_tokens_without_blocking(self):
        b = TokenBucket(capacity=2, refill_per_sec=0.001)  # praktycznie brak odnowy w czasie testu
        self.assertTrue(b.try_acquire())
        self.assertTrue(b.try_acquire())
        self.assertFalse(b.try_acquire())  # wiadro puste, refill zaniedbywalny

    def test_level_reflects_remaining_fraction(self):
        b = TokenBucket(capacity=4, refill_per_sec=0.001)
        b.try_acquire()
        b.try_acquire()
        self.assertAlmostEqual(0.5, b.level(), places=2)

    def test_refill_over_time_restores_tokens(self):
        b = TokenBucket(capacity=2, refill_per_sec=2.0)  # 2 tokeny/s
        b.try_acquire()
        b.try_acquire()
        self.assertFalse(b.try_acquire())
        import time
        time.sleep(0.6)  # ~1.2 tokena powinno sie odnowic
        self.assertTrue(b.try_acquire())

    def test_refill_never_exceeds_capacity(self):
        b = TokenBucket(capacity=3, refill_per_sec=100.0)
        import time
        time.sleep(0.1)
        self.assertLessEqual(b.level(), 1.0)
        self.assertAlmostEqual(1.0, b.level(), places=2)

    def test_acquire_blocks_until_token_available_then_returns_true(self):
        b = TokenBucket(capacity=1, refill_per_sec=10.0)  # 1 token/0.1s
        self.assertTrue(b.try_acquire())  # wiadro puste
        import time
        t0 = time.monotonic()
        ok = b.acquire(max_wait=2.0)
        elapsed = time.monotonic() - t0
        self.assertTrue(ok)
        self.assertLess(elapsed, 1.0)  # nie czekalo cale 2s, tylko do odnowy

    def test_acquire_gives_up_after_max_wait_and_returns_false(self):
        b = TokenBucket(capacity=1, refill_per_sec=0.01)  # bardzo wolny refill
        b.try_acquire()
        ok = b.acquire(max_wait=0.2)
        self.assertFalse(ok)

    def test_reset_restores_full_capacity(self):
        b = TokenBucket(capacity=3, refill_per_sec=0.001)
        b.try_acquire()
        b.try_acquire()
        b.reset()
        self.assertEqual(3.0, b.tokens)
        self.assertAlmostEqual(1.0, b.level())


class TestPublicBucketCalibration(unittest.TestCase):
    """20.08.2026: realny 429 na Cyklu #1 (cold start) przy PUBLIC_BUCKET=5
    req/s ujawnil, ze ta wartosc byla kalibrowana DOKLADNIE na
    niedokumentowanym, praktycznym limicie Blofin ("~5-6 req/s juz powoduje
    problemy" wg ich GitHuba), nie WYRAZNIE ponizej niego. Obnizone do 3
    req/s dla realnego marginesu bezpieczenstwa."""

    def test_public_bucket_calibrated_meaningfully_below_reported_practical_limit(self):
        from rate_limiter import PUBLIC_BUCKET
        # Zgloszony praktyczny limit Blofin (nie dokumentowany oficjalnie,
        # ale potwierdzony realnym 429) to ~5-6 req/s - nasz budzet musi
        # zostawiac realny margines ponizej tego, nie byc rowny/blisko.
        self.assertLessEqual(PUBLIC_BUCKET.refill_per_sec, 4.0)
        self.assertLessEqual(PUBLIC_BUCKET.capacity, 4.0)


if __name__ == "__main__":
    unittest.main()
