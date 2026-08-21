"""Proaktywny rate limiter (token bucket) po naszej stronie.

Zamiast czekac na HTTP 429 od Blofin i wtedy sie wycofywac (co i tak jest za
pozno - zapytanie juz zuzylo budzet), liczymy zapytania z gory. Przy niskim
stanie "wiadra" po prostu nie odpalamy zapytania (albo krotko czekamy),
zanim w ogole wyslemy je do sieci.

Blofin dokumentuje ~500 zapytan/min publicznych po IP (przekroczenie -> 5 min
bana) i ~1500/5min (przekroczenie -> 1h bana), oraz ~30 zapytan/10s na
UserID dla endpointow tradingowych. Zgloszony na ich GitHubie, niedokumentowany
limit w praktyce bywa ostrzejszy (~5 req/s) - liczymy wiec konserwatywnie,
wyraznie ponizej udokumentowanego sufitu, nie pod sam jego brzeg.
"""

from __future__ import annotations

import threading
import time


class TokenBucket:
    def __init__(self, capacity: float, refill_per_sec: float):
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.tokens = float(capacity)
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def _refill_locked(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
            self._last_refill = now

    def level(self) -> float:
        """0..1 - jak pelne jest wiadro. Do decyzji o degradacji (np. <20% ->
        pomijaj zapytania dyskrecjonalne zamiast czekac)."""
        with self._lock:
            self._refill_locked()
            return self.tokens / self.capacity if self.capacity else 1.0

    def try_acquire(self, tokens: float = 1.0) -> bool:
        """Nieblokujace: True i od razu zabiera tokeny, jesli sa dostepne;
        inaczej False bez czekania."""
        with self._lock:
            self._refill_locked()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def acquire(self, tokens: float = 1.0, max_wait: float = 8.0) -> bool:
        """Blokujace/proaktywne: czeka az beda dostepne tokeny (zamiast
        odpalac zapytanie i dostawac 429 po fakcie), z twardym sufitem
        czekania (zeby nie zawiesic watku bota w nieskonczonosc)."""
        deadline = time.monotonic() + max_wait
        while True:
            with self._lock:
                self._refill_locked()
                if self.tokens >= tokens:
                    self.tokens -= tokens
                    return True
                missing = tokens - self.tokens
                wait_needed = missing / self.refill_per_sec if self.refill_per_sec > 0 else max_wait
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(max(0.01, min(wait_needed, remaining, 0.5)))

    def reset(self) -> None:
        """Do testow - pelne wiadro, zerowanie zegara."""
        with self._lock:
            self.tokens = self.capacity
            self._last_refill = time.monotonic()


# Wspoldzielone (modulowe) wiadra - limit Blofin jest per-IP/per-UserID, wiec
# wszystkie instancje BlofinFeed w tym procesie musza dzielic jeden budzet,
# nie miec kazda wlasny (inaczej limit efektywnie mnozylby sie razy liczba
# instancji, tracac caly sens rate-limitowania).
#
# PUBLIC_BUCKET=3 req/s (bylo 5 - obnizone 20.08.2026 po realnym 429 na
# Cyklu #1 z cold-startem, mimo dzialajacego throttlingu). 5 req/s bylo
# kalibrowane DOKLADNIE na niedokumentowanym, praktycznym limicie zgloszonym
# na GitHubie Blofin ("~5-6 req/s juz powoduje problemy") - to byla
# kalibracja NA granicy, nie WYRAZNIE ponizej niej. 3 req/s daje realny
# margines bezpieczenstwa, nie tylko teoretyczny.
PUBLIC_BUCKET = TokenBucket(capacity=3.0, refill_per_sec=3.0)
TRADING_BUCKET = TokenBucket(capacity=10.0, refill_per_sec=2.5)
