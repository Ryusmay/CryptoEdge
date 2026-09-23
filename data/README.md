# `data/` — zamrożone dane, na których liczą bramki i replay

Wszystko tutaj jest **wejściem**, nie wynikiem analizy. `docs/analysis/` to
katalog roboczy i nie jest publikowany; te pliki są, bo silnik czyta je
w czasie działania albo bramki odtwarzają z nich swoje przypadki.

## `replay/` — bundle 30-dniowe

Pięć symboli, 4,6 MB. Tyle wystarczy, żeby ktoś z zewnątrz powtórzył pomiar
bramki parytetu. Bundle 90-dniowe nie są publikowane ze względu na rozmiar,
dlatego jedna asercja w `tests/test_outcome_dataset.py` pomija się jawnie,
gdy ich nie ma.

## `venue_microstructure_*.json` — zmierzona mikrostruktura

Sześć plików, dwie giełdy. **Kod czyta dokładnie jeden** —
`venue_microstructure.py` wskazuje na `venue_microstructure_20260903.json`
(`DEFAULT_PATH`). Pozostałe pięć jest opublikowane jako materiał pomiarowy
i nie jest przez nic wczytywane.

| plik | venue | symboli | okno | metoda |
|---|---|---:|---|---|
| `20260903` | binance_swap | 19 | pojedyncze migawki, 03:32–07:55Z | CryptoStruct `get_market_snapshot`, średnie 60-minutowe |
| `20260907_blofin` | blofin | 55 | 3 minuty | REST `/market/books` size=20, 5 migawek co 12 s |
| `20260909_blofin` | blofin | 118 | 5 minut | REST `/market/books` size=20, 5 migawek co 12 s |
| `20260909_doba_blofin` | blofin | 118 | pełna doba | REST `/market/books` size=20, 24 migawki co 1 h |
| `20260913_doba1_blofin` | blofin | 118 | pełna doba | REST `/market/books` size=100, 24/24 migawki |
| `20260915_doba2_blofin` | blofin | 118 | pełna doba | REST `/market/books` size=100, 24/24 migawki |

W plikach BloFina spread jest **średnią** z migawek, a głębokość
**minimum** — czyli ujęciem zachowawczym, nie typowym. Dwa ostatnie pliki
niosą dodatkowo `koszt_przejscia_wg_notionalu`: koszt round-trip przechodzony
przez realną książkę dla 50, 75, 250, 1000, 5000 i 25000 USD. To jest ta
wielkość, której model kosztu nie ma — i jedyna, którą da się uczciwie
zestawić ze stałą `DEFAULT_SPREAD_FRAC`.

## Co z tych plików wynika, a czego jeszcze nie zrobiono

`venue_microstructure_20260903.json` mówi o sobie, że mierzy **proxy**:
Binance USDT-M, a bot handluje na BloFinie, więc te liczby są *dolnym
ograniczeniem* kosztu. Pliki BloFina pozwalają sprawdzić, o ile dolnym.

Poniżej koszt round-trip z dwóch pełnych dób BloFina, przy notionale, jaki
model faktycznie zakłada (75 USD dla BTC/ETH/SOL, 50 USD dla reszty),
zestawiony ze spreadem z proxy. Tabela jest **wygenerowana z tych plików**,
nie przepisana:

| symbol | notional USD | binance spread bps | blofin rt 09-13 | blofin rt 09-15 | stala 4 bps |
|---|---:|---:|---:|---:|---|
| 1000BONK | 50 | 3.3730 | 9.6106 | 7.5676 | **zanizona** |
| AAVE | 50 | 0.7887 | 2.2718 | 2.1741 | zawyzona |
| BTC | 75 | 0.0133 | 0.3543 | 0.0408 | zawyzona |
| DOGE | 50 | 1.2030 | 2.7302 | 2.7551 | zawyzona |
| ENA | 50 | 0.6596 | 3.5689 | 4.3495 | **zanizona** |
| ETH | 75 | 0.0418 | 0.9152 | 0.3372 | zawyzona |
| HYPE | 50 | 0.1229 | 0.7605 | 1.4533 | zawyzona |
| LINK | 50 | 0.8907 | 2.3907 | 2.1208 | zawyzona |
| PENGU | 50 | 1.2040 | 4.6732 | 4.5569 | **zanizona** |
| PEPE | 50 | 0.2960 | 6.9370 | 5.5029 | **zanizona** |
| PUMP | 50 | 2.3260 | 11.1195 | 9.0866 | **zanizona** |
| SOL | 75 | 0.9915 | 1.4161 | 1.2851 | zawyzona |
| SUI | 50 | 1.3010 | 4.1939 | 3.3372 | **zanizona** |
| TAO | 50 | 0.4606 | 2.8580 | 4.1834 | **zanizona** |
| TRUMP | 50 | 4.4800 | 13.0781 | 10.7295 | **zanizona** |
| XAU | 50 | 0.0227 | 0.3752 | 0.1359 | zawyzona |
| XMR | 50 | 0.3398 | 3.4198 | 2.8017 | zawyzona |
| XRP | 50 | 0.7316 | 2.2332 | 4.9442 | **zanizona** |
| ZEC | 50 | 0.1229 | 1.8862 | 1.7667 | zawyzona |

ZANIZA dla 9 z 19, zawyza dla 10.

**To odwraca wniosek opublikowany w pliku proxy.** Tam zapisano: stała 4 bps
zawyża dla 18 z 19 symboli i zaniża dla jednego, więc jest błędem *kształtu*
przez zawyżanie. Na giełdzie, na której bot naprawdę handluje, stała
**zaniża dla 9 z 19 i zawyża dla 10** — mniej więcej rzut monetą.

Błąd kształtu zostaje, ale ma inny znak, niż myśleliśmy. Nie jest tak, że
model przepłaca i wystarczy go obniżyć; model jest po prostu niezwiązany
z rynkiem w obie strony, a na najdroższych symbolach (`PUMP` 9–11 bps,
`TRUMP` 11–13, `1000BONK` 8–10, `PEPE` 5,5–6,9) rozmija się o 2–3×
w kierunku, który kosztuje pieniądze.

**Nic z tego nie jest wpięte w silnik.** `expected_net_r` dalej czyta plik
proxy. Podmiana zmieniałaby decyzje wejścia, a ROADMAP zamraża to do M1
i `AGENTS.md` wymaga dla takiej zmiany walk-forwardu albo bramki parytetu.
To jest osobne ramię pomiarowe, nie poprawka przy okazji.

## Czego te pliki nie mówią

- Są z września 2026. Okno replayu to czerwiec–sierpień 2026. Użycie ich
  w replayie historycznym jest przybliżeniem — lepszym niż stała wzięta
  znikąd, ale przybliżeniem.
- Głębokość w plikach BloFina to minimum z migawek, więc pojedyncze
  wartości `top1_depth_usd` bywają rzędu kilkudziesięciu USD. To jest
  najgorszy moment doby, a nie stan typowy; koszt przejścia liczony przez
  książkę (`koszt_przejscia_wg_notionalu`) jest właściwszą miarą i mówi
  coś znacznie łagodniejszego.
- Żaden plik nie ma producenta w repozytorium. Powstały narzędziem, którego
  tu nie ma, więc nie da się ich dziś odtworzyć z samego kodu — w odróżnieniu
  od wszystkiego innego, co ten projekt publikuje.
