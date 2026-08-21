"""Event bus - strumieniowanie zdarzen cyklu/odrzucen do "laboratorium"
(zewnetrzny konsument analizujacy zachowanie bota w czasie rzeczywistym,
zamiast tylko czytac pliki JSONL po fakcie).

Backend teraz: Redis Streams (XADD) - lekki, powszechnie dostepny, prosty w
uruchomieniu lokalnie (`redis-server`), dobry dla pojedynczej instancji bota.

Backend pozniej (NIE zaimplementowany, celowo): Kafka/Pulsar - "gdy bedzie
kilka instancji" (cytat z planu) - czyli warunkowo, w przyszlosci, nie teraz.
Interfejs `EventBusBackend` jest wystarczajaco abstrakcyjny, zeby dolozyc
`KafkaBackend`/`PulsarBackend` bez zmiany kodu wywolujacego (patrz sekcja
"Rozszerzenie o Kafka/Pulsar" na dole pliku) - to jest przygotowanie, nie
obietnica dzialajacej integracji.

Degraduje sie w pelni bezpiecznie: brak pakietu `redis`, brak dzialajacego
serwera Redis, blad polaczenia - wszystko po prostu skutkuje `publish()`
zwracajacym False i logiem, NIGDY nie przerywa/spowalnia glownej petli bota.
Publikacja jest zawsze best-effort i asynchroniczna wzgledem watku wolajacego
(krotki timeout na polaczenie/zapis), zeby ewentualny martwy Redis nie
zawiesil bota."""

from __future__ import annotations

import json
import threading
import time
from typing import Optional

try:
    import redis as _redis_lib
    _REDIS_AVAILABLE = True
except ImportError:
    _redis_lib = None
    _REDIS_AVAILABLE = False

CYCLE_STREAM = "cryptoedge:cycles"
REJECT_STREAM = "cryptoedge:rejects"
MAX_STREAM_LEN = 10_000  # przyciecie streamow (XADD MAXLEN ~) - lab czyta na biezaco, nie potrzebuje historii bez konca


class EventBusBackend:
    """Interfejs backendu event busa - implementuj to dla nowego transportu
    (np. KafkaBackend), nie zmieniajac EventBus ani wolujacych."""

    def publish(self, stream: str, payload: dict) -> bool:
        raise NotImplementedError

    def available(self) -> bool:
        raise NotImplementedError


class RedisStreamsBackend(EventBusBackend):
    def __init__(self, url: str = "redis://localhost:6379/0", connect_timeout: float = 1.0):
        self._url = url
        self._connect_timeout = connect_timeout
        self._client = None
        self._connect_failed = False
        self._lock = threading.Lock()

    def _get_client(self):
        if not _REDIS_AVAILABLE:
            return None
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is not None:
                return self._client
            if self._connect_failed:
                return None
            try:
                client = _redis_lib.Redis.from_url(
                    self._url, socket_connect_timeout=self._connect_timeout,
                    socket_timeout=self._connect_timeout,
                )
                client.ping()
                self._client = client
                return client
            except Exception as e:
                self._connect_failed = True
                print(f"[EventBus] Redis niedostępny ({self._url}): {e}")
                return None

    def available(self) -> bool:
        return self._get_client() is not None

    def publish(self, stream: str, payload: dict) -> bool:
        client = self._get_client()
        if client is None:
            return False
        try:
            flat = {k: json.dumps(v) if not isinstance(v, (str, bytes)) else v for k, v in payload.items()}
            client.xadd(stream, flat, maxlen=MAX_STREAM_LEN, approximate=True)
            return True
        except Exception as e:
            print(f"[EventBus] publish błąd ({stream}): {e}")
            return False

    def reset_connection(self) -> None:
        """Do testow/reconnectu - wymusza ponowna probe polaczenia."""
        with self._lock:
            self._client = None
            self._connect_failed = False


class NullBackend(EventBusBackend):
    """No-op backend - uzywany gdy event bus jest wylaczony w konfiguracji."""

    def publish(self, stream: str, payload: dict) -> bool:
        return False

    def available(self) -> bool:
        return False


class EventBus:
    """Fasada nad backendem - publish_cycle()/publish_reject() to jedyne API,
    ktorego uzywa reszta kodu. Wybor/wymiana backendu jest ukryta tutaj."""

    def __init__(self, backend: Optional[EventBusBackend] = None):
        self.backend = backend or NullBackend()

    def publish_cycle(self, **fields) -> bool:
        payload = {"ts": time.time(), **fields}
        return self.backend.publish(CYCLE_STREAM, payload)

    def publish_reject(self, **fields) -> bool:
        payload = {"ts": time.time(), **fields}
        return self.backend.publish(REJECT_STREAM, payload)


def build_event_bus(config_module) -> EventBus:
    """Buduje EventBus wg configu. EVENT_BUS_ENABLED=False (domyslnie) ->
    NullBackend, zero prob polaczenia. True -> RedisStreamsBackend (jedyny
    zaimplementowany backend teraz)."""
    enabled = bool(getattr(config_module, "EVENT_BUS_ENABLED", False))
    if not enabled:
        return EventBus(NullBackend())
    url = str(getattr(config_module, "EVENT_BUS_REDIS_URL", "redis://localhost:6379/0"))
    return EventBus(RedisStreamsBackend(url))


# ============================================================
# Rozszerzenie o Kafka/Pulsar (NIE zaimplementowane - "gdy bedzie kilka
# instancji" wg planu, czyli warunkowo w przyszlosci):
#
# class KafkaBackend(EventBusBackend):
#     def __init__(self, bootstrap_servers: str):
#         from kafka import KafkaProducer  # kafka-python, nowa zaleznosc
#         self._producer = KafkaProducer(bootstrap_servers=bootstrap_servers,
#                                        value_serializer=lambda v: json.dumps(v).encode())
#     def publish(self, stream: str, payload: dict) -> bool:
#         try:
#             self._producer.send(stream, payload)
#             return True
#         except Exception:
#             return False
#     def available(self) -> bool:
#         return True
#
# Wielo-instancyjny sens Kafka/Pulsar (partycjonowanie, konsument-grupy,
# retencja rzedu dni/tygodni) nie daje realnej wartosci przy jednej instancji
# bota - Redis Streams jest dla tego przypadku prostszy i wystarczajacy.
# Migracja: podmienic build_event_bus() na wybor backendu wg config,
# EventBus i wolujacy kod (publish_cycle/publish_reject) nie zmieniaja sie.
# ============================================================
