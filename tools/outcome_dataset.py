"""Czysty zbior wynikow transakcji - jedna konfiguracja, jedno wywolanie.

POWOD. Zmierzone w v20.52.0: model `_gross_expected_r` ma zly KSZTALT.
Zaklada dwa stany (TP1 albo pelna strata -1R), a naprawde 7.8% transakcji
bierze pelna strate, 45% konczy w przedziale +/-0.25R, a cala dodatnia strona
wyniku siedzi w `hard_time_stop` (+0.352R). Zeby przeprojektowac ten model,
potrzebny jest zbior wynikow - ale ten z v20.52.0 pochodzil z 35 raportow
o ROZNYCH wersjach, konfiguracjach i zakresach dat. 193 wiersze z takiego
zrodla wystarczyly, zeby OBALIC ksztalt modelu, ale nie wystarcza, zeby
dopasowac nowy. Dopasowanie na heterogenicznej probce to zamiana jednej
niezweryfikowanej liczby na druga, tyle ze z falszywym poczuciem pewnosci.

CO TO ROBI. Jedno przejscie na symbol, na zamrozonych bundlach, przy JEDNEJ
konfiguracji (jej hash jest zapisany w naglowku). Bez portfela: `max_positions`
i limit kierunku odsialyby czesc wejsc, a do modelowania wyniku POJEDYNCZEJ
transakcji chcemy populacje nieprzycieta przez ograniczenia portfelowe.

Zapis strumieniowy do JSONL, symbol po symbolu. Dlugi przebieg na tej maszynie
potrafi zostac przerwany rozlaczeniem - to, co juz policzone, ma przetrwac.

CZEGO NIE ROBI. Nie dopasowuje modelu i nie zapisuje niczego do kalibratora.
Produkuje dane; wnioski sa osobnym krokiem.

    python tools/outcome_dataset.py --days 90
    python tools/outcome_dataset.py --symbols BTC ETH --days 30 --out plik.jsonl
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

from parity import (  # noqa: E402
    CACHE, SKIP_BARS, FEE_RT, SLIPPAGE_RT, config_fingerprint, _iso,
)

# Bundle 90d o IDENTYCZNYCH oknach czasowych (start 2026-06-02 03:35, koniec
# 2026-08-31 21:50, 26140 barow). Zostawione jako nazwany podzbior, ale UWAGA:
# do TEGO pomiaru wyrownanie okien jest NIEISTOTNE. Kazdy symbol jest
# odtwarzany NIEZALEZNIE przez replay_daytrading_v2, bez sprzezenia
# portfelowego - rozjechane okna nie maja czego zanieczyscic. Wyrownanie bylo
# istotne dla parity.py, gdzie limit slotow i limit kierunku porownuja symbole
# po WSPOLNYM indeksie baru. Domyslnie bierzemy wiec wszystko, co jest.
ALIGNED_90D = ["AAVE", "BTC", "DOGE", "ETH", "LINK", "SOL", "SUI", "XMR",
               "XRP", "ZEC"]


def available_symbols(days: int) -> list:
    """Wszystkie symbole, dla ktorych jest zamrozony bundle na tyle dni."""
    out = []
    for path in sorted(CACHE.glob(f"*_{days}d.json")):
        name = path.name[: -len(f"_{days}d.json")]
        if name:
            out.append(name)
    return out


def _bot_version() -> str:
    """Wersja kodu, nie ustawien. Model kosztu potrafi sie zmienic bez
    tkniecia config.py, a to zmienia realised_r kazdej transakcji."""
    try:
        return (ROOT / "VERSION.txt").read_text(encoding="utf-8").splitlines()[0].strip()
    except (OSError, IndexError):
        return "unknown"


def _row(trade, symbol: str, ts5: list) -> dict:
    entry = float(getattr(trade, "entry", 0.0) or 0.0)
    # UWAGA. `trade.sl` jest MUTOWANE w trakcie zycia transakcji: BE po TP1
    # przesuwa stop na wejscie, trailing przesuwa go dalej. Zapisane samo,
    # bylo cecha KONCOWA udajaca wejsciowa - i to nie jest teoretyczny
    # problem. W zbiorze 180d wszystkie 111 transakcji z tp1=True mialo
    # przesuniety stop (78 dokladnie na wejsciu, 33 dalej), a filtr liczacy
    # tp1_r = |TP1-entry|/|entry-sl| na tym polu wybieral wylacznie zwyciezcow,
    # bo maly tp1_r braly tylko te, ktorym trailing zdazyl odsunac stop.
    # Filtr wygladal wtedy na genialny (100% trafien) i byl czysta cyrkularnoscia.
    #
    # `initial_risk` jest ustawiane przy otwarciu i NIE jest ruszane - to
    # wzgledem niego normalizowane jest R. Z niego odtwarzamy SL z wejscia.
    sl = float(getattr(trade, "sl", 0.0) or 0.0)
    ryzyko0 = float(getattr(trade, "initial_risk", 0.0) or 0.0)
    kierunek = getattr(trade, "direction", None)
    if ryzyko0 > 0 and entry > 0 and kierunek in ("LONG", "SHORT"):
        sl_wejscie = entry - ryzyko0 if kierunek == "LONG" else entry + ryzyko0
    else:
        sl_wejscie = None          # "nie wiem" zamiast zmyslonej liczby
    entry_i = getattr(trade, "entry_i", None)
    exit_i = getattr(trade, "exit_i", None)
    out = {
        "symbol": symbol,
        "direction": getattr(trade, "direction", None),
        "profile": getattr(trade, "v2_profile", "unknown"),
        "regime": getattr(trade, "market_regime", "unknown"),
        "entry_i": entry_i,
        "exit_i": exit_i,
        "entry": entry,
        # sl_koncowy: po BE i trailingu. NIE uzywac jako cechy wejsciowej.
        "sl_koncowy": sl,
        # sl: stop Z WEJSCIA, odtworzony z initial_risk. To jest cecha znana
        # w barze sygnalu i jedyna, na ktorej wolno budowac filtry wejscia.
        "sl": sl_wejscie,
        "initial_risk": round(ryzyko0, 10) if ryzyko0 > 0 else None,
        # sl_dist to mianownik KAZDEGO czlonu kosztu w expected_net_r -
        # bez niego nie da sie potem porownac wyniku z rachunkiem.
        # Liczony z SL WEJSCIOWEGO, bo koszt jest ponoszony wzgledem ryzyka,
        # ktore przyjelismy przy otwarciu, a nie tego, co zostalo po trailingu.
        "sl_dist": (round(ryzyko0 / entry, 6)
                    if ryzyko0 > 0 and entry > 0 else None),
        "realised_r": round(float(getattr(trade, "realised_r", 0.0) or 0.0), 6),
        "mfe_r": round(float(getattr(trade, "mfe_r", 0.0) or 0.0), 6),
        "mae_r": round(float(getattr(trade, "mae_r", 0.0) or 0.0), 6),
        "tp1": bool(getattr(trade, "tp1_done", False)),
        "tp2": bool(getattr(trade, "tp2_done", False)),
        "remaining": round(float(getattr(trade, "remaining", 0.0) or 0.0), 6),
        "exit_reason": getattr(trade, "exit_reason", ""),
        "fill_kind": getattr(trade, "fill_kind", ""),
        # Cena limitu i cena wyjscia. Bez nich zbioru nie da sie uzyc do
        # przeprojektowania strony brutto: exit_price mowi GDZIE lada
        # wyjscia (45% transakcji konczy sie w +/-0.25R, czego model
        # dwustanowy nie widzi), a limit_price vs entry mowi, czy fill byl
        # lepszy od planowanego limitu.
        "limit_price": getattr(trade, "limit_price", None),
        "exit_price": getattr(trade, "exit_price", None),
        # Ceny celow i ich odleglosc w R. Bez tego nie da sie ocenic sitka
        # typu "TP1 najwyzej w polowie drogi do stopa" inaczej niz przez
        # pelny przebieg - a to godziny zamiast sekund.
        "tp1_price": getattr(trade, "tp1", None),
        "tp2_price": getattr(trade, "tp2", None),
        # tp1_r / tp2_r: odleglosc celu od wejscia w jednostkach ryzyka
        # WEJSCIOWEGO. Mianownikiem jest initial_risk, nie abs(entry - sl):
        # `sl` jest zmutowany (BE, trailing), wiec dzielenie przez niego
        # dawalo male tp1_r wylacznie transakcjom, ktorym trailing zdazyl
        # odsunac stop - czyli zwyciezcom. Sitko "tp1_r <= 0.5" wybieralo
        # wtedy wynik, nie sygnal.
        "tp1_r": (round(abs(float(getattr(trade, "tp1", 0.0)) - entry) / ryzyko0, 6)
                  if ryzyko0 > 0 and entry and getattr(trade, "tp1", None) else None),
        "tp2_r": (round(abs(float(getattr(trade, "tp2", 0.0)) - entry) / ryzyko0, 6)
                  if ryzyko0 > 0 and entry and getattr(trade, "tp2", None) else None),
        "fee_rt": getattr(trade, "fee_rt", None),
        "slip_rt": getattr(trade, "slip_rt", None),
        "funding_r": round(float(getattr(trade, "funding_r", 0.0) or 0.0), 6),
    }
    if entry_i is not None and 0 <= int(entry_i) < len(ts5):
        out["entry_utc"] = _iso(ts5[int(entry_i)])
    if exit_i is not None and 0 <= int(exit_i) < len(ts5):
        out["exit_utc"] = _iso(ts5[int(exit_i)])
    if entry_i is not None and exit_i is not None:
        out["duration_bars_5m"] = int(exit_i) - int(entry_i)
    return out


def zastosuj_overrides(overrides: dict | None) -> None:
    """Nadpisuje stale w module `config` PRZED policzeniem czegokolwiek.

    Uzywane do ramion eksperymentu: jedno wywolanie narzedzia = jeden zestaw
    parametrow. Nadpisania musza byc zastosowane ZANIM policzy sie
    config_fingerprint(), inaczej naglowek zbioru opisywalby inna konfiguracje
    niz ta, ktora policzyla wiersze - i dwa ramiona wygladalyby na porownywalne,
    nie bedac nimi.

    W procesach potomnych (spawn) wolane przez initializer puli, bo dziecko
    importuje `config` od nowa i nie widzi nadpisan rodzica.
    """
    if not overrides:
        return
    import config
    for nazwa, wartosc in overrides.items():
        if not nazwa.isupper():
            raise ValueError(f"override musi byc stala pisana wielkimi literami: {nazwa}")
        setattr(config, nazwa, wartosc)


def run_symbol(symbol: str, days: int,
               tp1_frac: float | None = None,
               tp2_frac: float | None = None) -> dict:
    """Jeden symbol, jeden przebieg. Zwraca naglowek + wiersze.

    UWAGA NA SEMANTYKE, zmieniona w v20.73.0. `tp1_frac` to udzial CALEJ
    pozycji, ale `tp2_frac` to udzial RESZTY po TP1 - dokladnie tak, jak
    liczy bot na zywo (paper_trader.py:1472 + :1508). Wczesniej replay
    traktowal oba jako udzial calosci i przez to zamykal na TP2 wiecej niz
    bot: przy 0.30 zamykal 30% calosci tam, gdzie bot zamykal 25%.

    Drabina bota (50% / 50% reszty / trailing) to zatem tp1_frac=0.50,
    tp2_frac=0.50, co daje 50/25/25 w ujeciu pierwotnej pozycji.

    None = wez z configu (`DAYTRADING_V2_TP1_FRAC` / `_TP2_FRAC`), czyli to
    samo, co robi bot. Zaszytych domyslek replayu juz nie ma.
    """
    from daytrading_backtester import (
        production_signal_provider_v2, htf_bias_provider_v2,
        htf_trail_anchor_provider_v2, replay_daytrading_v2,
    )

    path = CACHE / f"{symbol}_{days}d.json"
    if not path.exists():
        return {"symbol": symbol, "error": f"brak {path.name}"}
    payload = json.loads(path.read_text(encoding="utf-8"))
    bundle = payload["bundle"]
    # Stawki fundingu leza OBOK klucza "bundle", na najwyzszym poziomie pliku.
    # Samo payload["bundle"] je gubilo, przez co funding_r bylo zerem w 560/560
    # transakcji - a zero znaczylo "nie bylo czego liczyc", nie "funding nic nie
    # kosztuje". Dwie sciezki potrzebuja tej listy w dwoch roznych miejscach:
    #   1) AsOfBlofinFeed czyta bundle["funding"] (daytrading_backtester.py:34),
    #      zeby fetch_funding_rate zwrocilo cokolwiek do expected_net_r,
    #   2) replay_daytrading_v2 przyjmuje ja osobnym argumentem funding= i
    #      ksieguje settlementy miedzy entry a exit (apply_observed_funding).
    # walk_forward_v2.py:407 i historical_replay.py:764 robia dokladnie to samo.
    funding = payload.get("funding") or []
    bundle["funding"] = funding
    ts5 = bundle["5m"]["timestamps"]
    bar_count = len(bundle["5m"]["opens"])

    signal_at_raw, engine = production_signal_provider_v2(symbol, bundle)

    def gated(index, _raw=signal_at_raw, _ts=ts5, _end=bar_count):
        # Ta sama kadencja decyzji co parity.py: 15m boundary + SKIP_BARS.
        if not (SKIP_BARS <= index < _end):
            return None
        if int(_ts[index]) % 900_000 != 0:
            return None
        return _raw(index)

    started = time.time()
    result = replay_daytrading_v2(
        bundle["5m"], gated,
        htf_bias_at=htf_bias_provider_v2(symbol, bundle),
        htf_trail_anchor_at=htf_trail_anchor_provider_v2(symbol, bundle),
        notify_exit=getattr(engine, "notify_exit", None),
        fee_frac_round_trip=FEE_RT,
        slippage_frac_round_trip=SLIPPAGE_RT,
        funding=funding,
        **({} if tp1_frac is None else {"tp1_frac": float(tp1_frac)}),
        **({} if tp2_frac is None else {"tp2_frac": float(tp2_frac)}),
    )
    elapsed = round(time.time() - started, 1)

    trades = result.get("trades") or []
    rows = [_row(t, symbol, ts5) for t in trades]
    return {
        "symbol": symbol,
        "bars": bar_count,
        "window": {"start": _iso(ts5[0]), "end": _iso(ts5[-1])} if ts5 else {},
        "trades": len(rows),
        "elapsed_s": elapsed,
        "rows": rows,
    }


def completed_symbols(out_path: Path, cfg_hash: str) -> set | None:
    """Symbole juz policzone w istniejacym pliku - albo None, gdy nie wolno dopisywac.

    Wznawianie jest tu potrzebne z konkretnego powodu: przebieg na dziesieciu
    symbolach trwa okolo godziny, a most do maszyny potrafi paść w trakcie.
    Bez tego kazde rozlaczenie kosztuje caly przebieg (raz juz kosztowalo trzy
    symbole i dwadziescia minut).

    Dopisujemy WYLACZNIE gdy hash konfiguracji sie zgadza. Zbior sklejony
    z dwoch roznych konfiguracji wygladalby jak jeden pomiar i nie bylby nim -
    dokladnie ten blad mielismy w probce 193 transakcji z mieszanych raportow.
    """
    if not out_path.exists():
        return None
    header = None
    done = set()
    data_rows = 0
    try:
        with out_path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                kind = row.get("_type")
                if kind == "header":
                    header = row
                elif kind == "symbol_done":
                    done.add(row.get("symbol"))
                else:
                    data_rows += 1
    except OSError:
        return None
    if header is None or header.get("config_hash") != cfg_hash:
        return None
    if header.get("bot_version") != _bot_version():
        # config_hash pilnuje USTAWIEN, ale nie KODU. Zmiana modelu kosztu
        # (np. poslizgu) nie rusza config.py, a zmienia realised_r kazdej
        # transakcji. Zbior sklejony z dwoch wersji kodu wygladalby jak jeden
        # pomiar - to ten sam blad co probka 193 transakcji z mieszanych
        # raportow, tylko trudniejszy do zauwazenia.
        return None
    if data_rows and not done:
        # Plik ze starego formatu: ma transakcje, ale nie ma znacznikow konca
        # symbolu, wiec nie da sie powiedziec, ktore symbole sa KOMPLETNE.
        # Dopisanie do niego zdublowaloby to, co juz jest. Zaczynamy od zera.
        return None
    return done


def _pin_determinism() -> None:
    """Przypina srodowisko PRZED startem procesow potomnych.

    Windows uzywa spawn, wiec dziecko dziedziczy os.environ rodzica i czyta
    te zmienne przy starcie interpretera oraz przy imporcie numpy.

    OMP/MKL/OPENBLAS/NUMEXPR na 1: bez tego kazdy z czterech workerow
    odpalilby wlasna pule watkow BLAS i mielibysmy 16 watkow na 4 rdzenie.
    Nadsubskrypcja spowalnia, a wielowatkowy BLAS dzieli redukcje na
    fragmenty zaleznie od stanu puli - czyli INNA KOLEJNOSC SUMOWANIA
    i inne ostatnie bity. To zapalIloby bramki bez zmiany logiki.

    PYTHONHASHSEED=0: w jednym procesie jest jeden losowy seed na caly
    przebieg, przy spawn kazde dziecko dostaje wlasny. Gdziekolwiek kod
    iteruje po zbiorze stringow, kolejnosc bylaby inna w kazdym procesie.
    """
    for k, v in (("OMP_NUM_THREADS", "1"), ("MKL_NUM_THREADS", "1"),
                 ("OPENBLAS_NUM_THREADS", "1"), ("NUMEXPR_NUM_THREADS", "1"),
                 ("PYTHONHASHSEED", "0")):
        os.environ[k] = v


def _worker_init(overrides, pin):
    """Startuje w KAZDYM procesie potomnym, zanim policzy cokolwiek."""
    if pin:
        _pin_determinism()
    zastosuj_overrides(overrides)


def _iter_results(todo, days, workers, overrides=None,
                  tp1_frac=None, tp2_frac=None):
    """Zwraca (symbol, wynik) w KOLEJNOSCI KANONICZNEJ, nie w kolejnosci
    ukonczenia - dzieki temu plik wyjsciowy jest bajt-w-bajt taki sam jak
    przy przebiegu sekwencyjnym, niezaleznie od tego, ktory worker skonczyl
    pierwszy.

    Zapis idzie strumieniowo: gdy tylko nastepny oczekiwany symbol jest
    gotowy, leci do pliku razem z markerem. Ubicie procesu w polowie
    zostawia wiec poprawny, wznawialny plik - a nie bufor w pamieci.
    """
    if workers <= 1 or len(todo) <= 1:
        for symbol in todo:
            yield symbol, run_symbol(symbol, days, tp1_frac, tp2_frac)
        return

    from concurrent.futures import ProcessPoolExecutor

    _pin_determinism()
    print(f"[zbior] pula {workers} procesow, zapis w kolejnosci kanonicznej",
          flush=True)
    ready, nxt = {}, 0
    ex = ProcessPoolExecutor(max_workers=workers,
                             initializer=_worker_init,
                             initargs=(overrides, True))
    try:
        futs = {ex.submit(run_symbol, s, days, tp1_frac, tp2_frac): s
                for s in todo}
        for fut in as_completed(futs):
            sym = futs[fut]
            try:
                ready[sym] = fut.result()
            except Exception as exc:                    # jeden zly symbol
                ready[sym] = {"error": repr(exc)}       # nie kasuje reszty
            while nxt < len(todo) and todo[nxt] in ready:
                s = todo[nxt]
                yield s, ready.pop(s)
                nxt += 1
    except KeyboardInterrupt:
        print("\n[zbior] Ctrl+C: koncze biezace symbole, nowych nie startuje",
              flush=True)
        ex.shutdown(wait=True, cancel_futures=True)
        raise
    finally:
        ex.shutdown(wait=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Czysty zbior wynikow transakcji z zamrozonych bundli.")
    ap.add_argument("--symbols", nargs="*", default=None,
                    help="domyslnie WSZYSTKIE bundle dostepne na tyle dni")
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--out", type=Path,
                    default=ROOT / "docs" / "analysis" / "outcomes.jsonl")
    ap.add_argument("--fresh", action="store_true",
                    help="zacznij od zera zamiast wznawiac")
    ap.add_argument("--workers", type=int, default=1,
                    help="ile procesow rownolegle (1 = sekwencyjnie, jak dotad)")
    ap.add_argument("--overrides", type=str, default=None,
                    help='JSON ze stalymi config do nadpisania, np. '
                         '\'{"DAYTRADING_V2_TP1_R": 1.0}\'. Nadpisania ida '
                         'takze do procesow potomnych i WCHODZA do odcisku '
                         'konfiguracji, wiec ramiona eksperymentu maja rozne hashe.')
    ap.add_argument("--tp1-frac", type=float, default=None,
                    help="udzial CALEJ pozycji zamykany na TP1 (nie reszty)")
    ap.add_argument("--tp2-frac", type=float, default=None,
                    help="udzial CALEJ pozycji zamykany na TP2")
    ap.add_argument("--arm", type=str, default=None,
                    help="nazwa ramienia, zapisywana w naglowku")
    args = ap.parse_args(argv)

    overrides = json.loads(args.overrides) if args.overrides else None
    # Nadpisania MUSZA byc zastosowane przed config_fingerprint(), inaczej
    # naglowek opisywalby inna konfiguracje niz ta, ktora policzy wiersze.
    zastosuj_overrides(overrides)

    symbols = args.symbols if args.symbols else available_symbols(args.days)
    if not symbols:
        print(f"[zbior] brak bundli na {args.days}d w {CACHE}", flush=True)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    cfg = config_fingerprint()
    cfg_hash = cfg.get("hash")

    done = None if args.fresh else completed_symbols(args.out, cfg_hash)
    if done is None:
        header = {
            "_type": "header",
            "config_hash": cfg_hash,
            "bot_version": _bot_version(),
            "config_keys": cfg.get("keys"),
            "days": args.days,
            "skip_bars": SKIP_BARS,
            "fee_rt": FEE_RT,
            "slippage_rt": SLIPPAGE_RT,
            "arm": args.arm,
            "overrides": overrides,
            "tp1_frac": args.tp1_frac,
            "tp2_frac": args.tp2_frac,
            "note": ("jeden symbol na raz, bez ograniczen portfelowych; "
                     "kazdy symbol ma WLASNE okno kalendarzowe - to jest w porzadku "
                     "dla modelowania pojedynczej transakcji i zle dla wnioskow portfelowych"),
        }
        with args.out.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(header, ensure_ascii=False) + "\n")
        done = set()
        print(f"[zbior] nowy plik, config {cfg_hash}", flush=True)
    else:
        print(f"[zbior] WZNAWIAM: {len(done)} symboli juz policzonych"
              f" ({', '.join(sorted(done)) or '-'}), config {cfg_hash}", flush=True)

    todo = [s for s in symbols if s not in done]
    print(f"[zbior] do policzenia {len(todo)} z {len(symbols)}", flush=True)

    total = 0
    for symbol, got in _iter_results(todo, args.days, max(1, int(args.workers)),
                                     overrides, args.tp1_frac, args.tp2_frac):
        if got.get("error"):
            print(f"[zbior] {symbol:<9} POMINIETY: {got['error']}", flush=True)
            continue
        with args.out.open("a", encoding="utf-8") as fh:
            for row in got["rows"]:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            # Znacznik konca symbolu: symbol z ZEREM transakcji tez jest
            # policzony i nie ma byc liczony po raz drugi.
            fh.write(json.dumps({
                "_type": "symbol_done", "symbol": symbol,
                "trades": got["trades"], "bars": got["bars"],
                "window": got.get("window") or {},
            }, ensure_ascii=False) + "\n")
        total += got["trades"]
        w = got.get("window") or {}
        print(f"[zbior] {symbol:<9} transakcji {got['trades']:>4}"
              f" | barow {got['bars']} | {w.get('start')} -> {w.get('end')}"
              f" | {got['elapsed_s']}s", flush=True)

    print(f"[zbior] dopisano {total} transakcji -> {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
