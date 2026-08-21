"""Multiple-testing-aware diagnostics for OOS trade returns.

Formula zrodlowe: Bailey, D.H. i M. Lopez de Prado (2014), "The Deflated
Sharpe Ratio: Correcting for Selection Bias, Backtest Overfitting and
Non-Normality", Journal of Portfolio Management. PSR: rownanie z sekcji
"Probabilistic Sharpe Ratio". DSR / E[max SR]: Eq.(2) i Eq.(6)/Appendix A.1
(dokladnie ten sam wzor, ktory autorzy podaja jako referencyjny kod Pythona
w Snippet 1 tej pracy - `getExpMaxSR`).
"""

from __future__ import annotations

import math
from statistics import NormalDist
from typing import Optional, Sequence


_NORMAL = NormalDist()
_EULER_MASCHERONI = 0.5772156649


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def sharpe_stats(returns: Sequence[float], periods_per_year: float = 365.0) -> dict:
    xs = [float(x) for x in returns]
    n = len(xs)
    if n < 3:
        return {"n": n, "sharpe": None, "reason": "insufficient_observations"}
    mean = sum(xs) / n
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    std = math.sqrt(var)
    if std <= 1e-12:
        return {"n": n, "sharpe": None, "reason": "zero_variance"}
    sr_period = mean / std
    skew = sum((x - mean) ** 3 for x in xs) / n / (std ** 3)
    kurt = sum((x - mean) ** 4 for x in xs) / n / (std ** 4)
    return {"n": n, "sharpe": sr_period * math.sqrt(periods_per_year),
            "sharpe_period": sr_period, "skew": skew, "kurtosis": kurt}


def probabilistic_sharpe_ratio(returns: Sequence[float], benchmark_sr_period: float = 0.0) -> float:
    stats = sharpe_stats(returns, periods_per_year=1.0)
    if stats.get("sharpe") is None:
        return 0.0
    sr, n = stats["sharpe_period"], stats["n"]
    denominator = math.sqrt(max(1e-12, 1.0 - stats["skew"] * sr +
                                ((stats["kurtosis"] - 1.0) / 4.0) * sr * sr))
    return _cdf((sr - benchmark_sr_period) * math.sqrt(n - 1) / denominator)


def expected_max_sharpe_z(n_trials: int) -> float:
    """E[max z] dla n_trials i.i.d. N(0,1) - Bailey/Lopez de Prado Eq.(5),
    dokladnie ten wzor co ich referencyjny `getExpMaxSR` (Snippet 1 w pracy):

        maxZ = (1 - gamma) * Phi^-1(1 - 1/N) + gamma * Phi^-1(1 - 1/(N*e))

    Uzywamy statistics.NormalDist().inv_cdf zamiast klasycznego przyblizenia
    z teorii wartosci ekstremalnych (sqrt(2 ln N) - ...), ktore daje
    systematycznie zawyzony wynik (sprawdzone numerycznie: +0.02 do +0.09
    w typowym zakresie N=10..1000 wzgledem wzoru z pracy).
    """
    if n_trials <= 1:
        return 0.0
    z1 = _NORMAL.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _NORMAL.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return (1.0 - _EULER_MASCHERONI) * z1 + _EULER_MASCHERONI * z2


def deflated_sharpe_ratio(returns: Sequence[float], trials: int = 1,
                           trial_sharpes: Optional[Sequence[float]] = None) -> dict:
    """DSR = PSR liczone wzgledem progu selekcji SR0 = mu + sigma * E[max z].

    `trial_sharpes`: Sharpe ratios z NIEZALEZNYCH prob (np. innych symboli,
    innych konfiguracji parametrow przetestowanych w tym samym przebiegu
    strojenia) - to jest sigma[{SR_k}] z pracy, czyli faktyczny rozrzut
    wynikow miedzy probami, NIE dlugosc probki jednej wybranej strategii.
    Podawaj to zawsze, gdy masz wyniki wiecej niz jednej proby (np. z
    counterfactual/parametrycznego audytu) - inaczej DSR jest tylko
    przyblizeniem, nie pelnym wzorem z pracy.
    """
    stats = sharpe_stats(returns, periods_per_year=1.0)
    n_trials = max(1, int(trials))
    if stats.get("sharpe") is None:
        return {**stats, "trials": n_trials, "dsr": 0.0}

    if n_trials == 1:
        benchmark = 0.0
        benchmark_note = "single trial - no multiple-testing correction needed"
    else:
        max_z = expected_max_sharpe_z(n_trials)
        trial_xs = [float(x) for x in (trial_sharpes or []) if x is not None]
        if len(trial_xs) >= 2:
            trial_mean = sum(trial_xs) / len(trial_xs)
            trial_var = sum((x - trial_mean) ** 2 for x in trial_xs) / (len(trial_xs) - 1)
            sigma_sr = math.sqrt(trial_var)
            benchmark = sigma_sr * max_z  # E[{SR_k}]=0 pod hipoteza zerowa (brak realnego edge'a)
            benchmark_note = f"sigma_sr z {len(trial_xs)} zarejestrowanych SR niezaleznych prob"
        else:
            # Brak zarejestrowanych SR z osobnych prob (trial_sharpes nie
            # podane). Fallback: przyblizamy sigma_sr bledem standardowym
            # estymatora SR pojedynczej probki (1/sqrt(n-1)). To NIE jest
            # to samo co rozrzut SR MIEDZY probami z pracy - jest zazwyczaj
            # bardziej optymistyczne (nizsza sigma), bo ignoruje realny
            # rozrzut wynikow ze strojenia parametrow. Traktuj to jako
            # przyblizenie, nie pelny DSR z pracy.
            se_sr = 1.0 / math.sqrt(max(stats["n"] - 1, 1))
            benchmark = se_sr * max_z
            benchmark_note = "sigma_sr przyblizone z dlugosci probki (brak trial_sharpes - mniej rygorystyczne)"

    dsr = probabilistic_sharpe_ratio(returns, benchmark)
    return {**stats, "trials": n_trials, "selection_benchmark_sr": benchmark,
            "benchmark_note": benchmark_note, "dsr": dsr, "passes_95pct": dsr >= 0.95}
