"""Serwis gRPC - "drugi interfejs" obok istniejacego wbudowanego serwera HTTP
(app.py). Pozwala zewnetrznym narzedziom/klientom (rowniez w innych jezykach)
odpytywac stan bota przez gRPC zamiast wylacznie HTTP JSON.

WAZNA DECYZJA PROJEKTOWA: normalny gRPC wymaga zdefiniowania wiadomosci w
pliku .proto i wygenerowania stubow (`protoc`) - tego kroku nie da sie
wykonac/zweryfikowac w srodowisku, w ktorym to powstalo (brak protoc, brak
mozliwosci uruchomienia realnego serwera do testu koncowo-koncowego). Zamiast
ryzykowac recznie napisane, niemozliwe do zweryfikowania pliki *_pb2.py,
serwis uzywa gRPC-owego API generycznych handlerow (`grpc.GenericRpcHandler`)
z JSON jako serializacja zamiast protobuf. To wciaz prawdziwy gRPC (ramkowanie
HTTP/2, routing metod, deadliny, obsluga bledow), tylko payload to JSON, nie
protobuf. Docelowy schemat (do przyszlej migracji na prawdziwy protobuf)
udokumentowany na dole pliku jako cryptoedge.proto.

Degraduje sie w pelni bezpiecznie: brak pakietu grpcio, blad startu serwera -
GrpcServer.start() zwraca False i loguje, NIGDY nie przerywa dzialania bota.
Ten interfejs jest czystym dodatkiem do istniejacego HTTP API, nie zastepuje
go i nie jest wymagany do normalnej pracy bota.
"""

from __future__ import annotations

import json
from typing import Callable

try:
    import grpc
    _GRPC_AVAILABLE = True
except ImportError:
    grpc = None
    _GRPC_AVAILABLE = False

DEFAULT_PORT = 50061
SERVICE_NAME = "cryptoedge.BotStatus"
METHOD_GET_SNAPSHOT = "GetSnapshot"


def _json_serializer(obj) -> bytes:
    return json.dumps(obj, default=str, ensure_ascii=False).encode("utf-8")


def _json_deserializer(data: bytes):
    if not data:
        return {}
    return json.loads(data.decode("utf-8"))


class _GenericHandler:
    """Opakowuje callable jako grpc.GenericRpcHandler (grpc wymaga obiektu
    z metoda service(), nie samej funkcji)."""

    def __init__(self, fn):
        self._fn = fn

    def service(self, handler_call_details):
        return self._fn(handler_call_details)


class _ThreadPoolExecutorLazy:
    """Leniwy import concurrent.futures - unika kosztu importu, gdy grpc i
    tak jest niedostepny (najczestsza sciezka w tym projekcie)."""
    _pool = None

    @classmethod
    def get(cls):
        if cls._pool is None:
            from concurrent import futures
            cls._pool = futures.ThreadPoolExecutor(max_workers=4)
        return cls._pool


class GrpcServer:
    """Watek serwera gRPC udostepniajacy stan bota. `snapshot_provider` to
    callable zwracajace dict (zwykle rt.snapshot) - wolane na kazde
    wywolanie GetSnapshot, nie cache'owane tutaj (rt.snapshot() jest tanie,
    to tylko odczyt juz policzonego stanu)."""

    def __init__(self, snapshot_provider: Callable[[], dict], port: int = DEFAULT_PORT):
        self._snapshot_provider = snapshot_provider
        self._port = port
        self._server = None
        self._running = False

    @property
    def available(self) -> bool:
        return _GRPC_AVAILABLE

    def handle_get_snapshot(self, request, context):
        """Publiczna (nie-podkreslnikowa) - ulatwia bezposrednie testy bez
        przechodzenia przez cala warstwe grpc.server()."""
        try:
            return dict(self._snapshot_provider() or {})
        except Exception as e:
            if context is not None:
                try:
                    context.set_code(grpc.StatusCode.INTERNAL)
                    context.set_details(str(e))
                except Exception:
                    pass
            return {"error": str(e)}

    def _generic_handler(self, handler_call_details):
        # handler_call_details.method wyglada jak "/cryptoedge.BotStatus/GetSnapshot"
        method = (handler_call_details.method or "").rsplit("/", 1)[-1]
        if method != METHOD_GET_SNAPSHOT:
            return None
        return grpc.unary_unary_rpc_method_handler(
            self.handle_get_snapshot,
            request_deserializer=_json_deserializer,
            response_serializer=_json_serializer,
        )

    def start(self) -> bool:
        """Startuje serwer w watku w tle. Zwraca False (bez wyjatku), jesli
        grpcio nie jest zainstalowany albo start sie nie powiedzie z innego
        powodu - bot dziala dalej normalnie, ten interfejs jest opcjonalny."""
        if not _GRPC_AVAILABLE:
            print("[gRPC] pakiet grpcio niedostępny - drugi interfejs wyłączony, bot działa normalnie dalej")
            return False
        if self._running:
            return True
        try:
            self._server = grpc.server(_ThreadPoolExecutorLazy.get())
            handler = _GenericHandler(self._generic_handler)
            self._server.add_generic_rpc_handlers((handler,))
            self._server.add_insecure_port(f"[::]:{self._port}")
            self._server.start()
            self._running = True
            print(f"[gRPC] drugi interfejs uruchomiony na porcie {self._port}")
            return True
        except Exception as e:
            print(f"[gRPC] nie udało się uruchomić serwera: {e}")
            self._server = None
            return False

    def stop(self, grace: float = 1.0) -> None:
        if self._server is not None:
            try:
                self._server.stop(grace)
            except Exception:
                pass
        self._running = False

    def is_running(self) -> bool:
        return self._running


# ============================================================
# Docelowy schemat protobuf (cryptoedge.proto) - dokumentacja do przyszlej
# migracji z JSON-nad-gRPC na "prawdziwy" protobuf, gdy protoc bedzie
# dostepny do wygenerowania stubow:
#
#     syntax = "proto3";
#     package cryptoedge;
#
#     service BotStatus {
#       rpc GetSnapshot (SnapshotRequest) returns (SnapshotReply);
#     }
#
#     message SnapshotRequest {}
#
#     message SnapshotReply {
#       bool running = 1;
#       bool engine_enabled = 2;
#       int32 cycle = 3;
#       double equity = 4;
#       int32 open_positions = 5;
#       string last_error = 6;
#       // ... reszta pol, jak w runtime.BotRuntime.snapshot()
#     }
#
# Migracja: podmienic _json_serializer/_json_deserializer na
# SnapshotReply.SerializeToString/FromString wygenerowane przez protoc -
# reszta (GrpcServer, routing metod) nie musi sie zmienic.
# ============================================================
