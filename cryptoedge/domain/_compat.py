"""Small immutable/legacy conversion helpers; no application dependencies."""
from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from types import MappingProxyType
from typing import Any

# Uwaga wydajnosciowa (zmierzone 2026-09-04, profil parity BTC 30d):
# 'Mapping' MUSI pochodzic z collections.abc, nie z typing. isinstance()
# wobec aliasu z typing przechodzi przez typing.__instancecheck__ ->
# typing.__subclasscheck__ -> abc -> _abc_subclasscheck, czyli cztery
# warstwy zamiast jednej. freeze/thaw wolaja to rekurencyjnie ~30 mln razy
# na jeden przebieg symbolu, co dawalo ~175 s z 315 s calego przebiegu.
# Dodatkowo sprawdzamy najpierw konkretne typy (dict/list/tuple), bo one
# stanowia praktycznie caly ruch, a exact-type check jest najtanszy.
# Semantyka bez zmian: dict jest Mapping, wiec kolejnosc testow ta sama.
# Zero arytmetyki => wyniki bit w bit identyczne => bramki nie drgna.


# Typy, ktore sa juz niezmienne - nie ma w nich czego zamrazac ani odmrazac.
# Trzymane jako frozenset, bo test przynaleznosci jest wstawiany WPROST
# w komprehensje: profil pokazal, ze samo wywolanie funkcji _is_scalar
# kosztowalo 25,5 s przy 144,9 mln wywolan na symbol.
_SCALARS = frozenset({float, int, str, bool, type(None)})


def _is_scalar(v) -> bool:
    """Wersja funkcyjna dla sciezek zimnych. W goracych - test wstawiony."""
    return v.__class__ in _SCALARS


def freeze(value: Any) -> Any:
    cls = value.__class__
    if cls is dict:
        return MappingProxyType({
            str(k): (v if v.__class__ in _SCALARS else freeze(v))
            for k, v in value.items()})
    if cls is list or cls is tuple:
        # Test skalara W MIEJSCU WYWOLANIA, nie przez rekursje. Profil
        # (cProfile, HYPE 90d): freeze mial 72,6 mln wywolan na symbol,
        # z czego 72,2 mln to byla rekursja na pojedynczych floatach z ramek
        # OHLCV, zwracajaca je bez zmiany. Kazde takie wywolanie kosztowalo
        # ramke stosu i sprawdzenie trzech warunkow, zeby nie zrobic nic.
        # Ramki to 5 interwalow x 8 kolumn x 300 barow - same liczby.
        # ZERO arytmetyki: freeze przebudowuje wylacznie kontenery, nie
        # dotyka wartosci. Wynik jest identyczny co do bitu z definicji.
        return tuple(v if v.__class__ in _SCALARS else freeze(v) for v in value)
    if cls is set:
        return frozenset(v if v.__class__ in _SCALARS else freeze(v) for v in value)
    # sciezka wolna: podtypy i egzotyka (MappingProxyType, OrderedDict, ...)
    if isinstance(value, Mapping):
        return MappingProxyType({str(k): freeze(v) for k, v in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze(v) for v in value)
    if isinstance(value, set):
        return frozenset(freeze(v) for v in value)
    return value


def thaw(value: Any) -> Any:
    cls = value.__class__
    if cls is dict:
        return {str(k): (v if v.__class__ in _SCALARS else thaw(v))
                for k, v in value.items()}
    if cls is list or cls is tuple:
        # jak w freeze: skalar rozpoznany w miejscu, bez wywolania funkcji
        return [v if v.__class__ in _SCALARS else thaw(v) for v in value]
    if cls in _SCALARS:
        return value
    # sciezka wolna: Enum, podtypy, frozenset/set
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): thaw(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, frozenset, set)):
        return [thaw(v) for v in value]
    return value


def enum_value(enum_type, value: Any, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, enum_type):
        return value
    raw = str(getattr(value, "value", value))
    for candidate in (raw, raw.upper(), raw.lower()):
        try:
            return enum_type(candidate)
        except ValueError:
            pass
    return default


def legacy_dict(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    data = getattr(value, "__dict__", None)
    return dict(data) if isinstance(data, dict) else {}

