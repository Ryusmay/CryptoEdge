"""OOS attribution, score calibration and portfolio-aware candidate ranking."""
from __future__ import annotations

from collections import defaultdict
import math
from typing import Iterable, Sequence

from portfolio_risk import cluster_of


def marginal_filter_contribution(rows: Sequence[dict]) -> dict:
    """Compare OOS net-R with a filter active vs counterfactual pass records.

    Telemetry rows may carry ``counterfactual_filters`` produced by replay.  No
    causal claim is made when the counterfactual sample is absent.
    """
    buckets = defaultdict(lambda: {"kept": [], "rejected_cf": []})
    for row in rows or []:
        r = float(row.get("net_r") if row.get("net_r") is not None else row.get("r") or 0)
        for name in row.get("passed_filters") or []:
            buckets[str(name)]["kept"].append(r)
        for name in row.get("counterfactual_filters") or []:
            buckets[str(name)]["rejected_cf"].append(r)
    out = {}
    for name, values in buckets.items():
        kept, rejected = values["kept"], values["rejected_cf"]
        ek = sum(kept) / len(kept) if kept else None
        er = sum(rejected) / len(rejected) if rejected else None
        out[name] = {"kept_n": len(kept), "counterfactual_n": len(rejected),
                     "kept_expectancy_r": ek, "counterfactual_expectancy_r": er,
                     "marginal_r": (ek - er) if ek is not None and er is not None else None}
    return out


def calibrate_score_probability(rows: Sequence[dict], bins: int = 10,
                                min_bin_n: int = 20) -> list[dict]:
    """Empirical, monotonic probability of positive net-R by score bin."""
    clean = sorted((float(r["score"]), float(r.get("net_r", r.get("r", 0))))
                   for r in (rows or []) if r.get("score") is not None)
    if not clean:
        return []
    width = max(min_bin_n, math.ceil(len(clean) / max(1, bins)))
    raw = []
    for i in range(0, len(clean), width):
        chunk = clean[i:i + width]
        raw.append({"score_min": chunk[0][0], "score_max": chunk[-1][0], "n": len(chunk),
                    "prob_positive": sum(1 for _, r in chunk if r > 0) / len(chunk),
                    "expectancy_r": sum(r for _, r in chunk) / len(chunk)})
    # pooled-adjacent-violators: higher score may not have lower calibrated P.
    blocks = [dict(x, weight=x["n"]) for x in raw]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i]["prob_positive"] <= blocks[i + 1]["prob_positive"]:
            i += 1
            continue
        a, b = blocks[i], blocks[i + 1]
        n = a["weight"] + b["weight"]
        merged = {"score_min": a["score_min"], "score_max": b["score_max"], "n": a["n"] + b["n"],
                  "weight": n,
                  "prob_positive": (a["prob_positive"] * a["weight"] + b["prob_positive"] * b["weight"]) / n,
                  "expectancy_r": (a["expectancy_r"] * a["weight"] + b["expectancy_r"] * b["weight"]) / n}
        blocks[i:i + 2] = [merged]
        i = max(0, i - 1)
    for block in blocks:
        block.pop("weight", None)
    return blocks


def candidate_quality(signal: dict, open_positions: Iterable[dict] = ()) -> float:
    expected_net_r = float(signal.get("expected_net_r") or signal.get("expected_r") or 0)
    fill_p = min(1.0, max(0.0, float(signal.get("fill_probability") or 0.5)))
    adverse = min(1.0, max(0.0, float(signal.get("adverse_selection_score") or 0)))
    inv = max(0.0, float(signal.get("distance_from_invalidation_atr") or 0))
    inv_quality = min(1.0, inv / 2.0)
    cid = cluster_of(str(signal.get("symbol") or ""))
    cluster_count = sum(1 for p in open_positions if cluster_of(str(p.get("symbol") or "")) == cid)
    independence = 1.0 / (1.0 + cluster_count)
    return expected_net_r * fill_p * (1.0 - adverse) * (0.5 + 0.5 * inv_quality) * independence


def rank_candidates(signals: Sequence[dict], open_positions: Iterable[dict] = ()) -> list[dict]:
    rows = []
    for signal in signals or []:
        row = dict(signal)
        row["portfolio_quality"] = candidate_quality(row, open_positions)
        rows.append(row)
    return sorted(rows, key=lambda x: (x["portfolio_quality"], float(x.get("strength") or 0)), reverse=True)
