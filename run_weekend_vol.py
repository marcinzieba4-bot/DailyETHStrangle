"""
Weekend daily-vol calendar on ETH, built from real Deribit daily-option IVs (2021-03 .. 2026-11).

Finding: at real prices, selling the daily ATM straddle has no edge on most weekdays, but
  - Sat 08:00 -> Sun 08:00 straddles were priced above what ETH moved (seller kept ~20% of premium)
  - Sun 08:00 -> Mon 08:00 straddles were priced below what ETH moved (buyer earned ~23% of premium)
Strategy: SELL the Saturday straddle, BUY the Sunday straddle. Each component (legs, hedging, IV filters,
loss-recovery size-up, leverage) is chosen on 2021-03..2023-12 by CAGR / |max drawdown| and then tested on
2024-01..2026-11, with a block bootstrap and cost / price sensitivities.

  python3 run_weekend_vol.py   -> results/weekend_vol.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from run_daily_vol import load_days, simulate
from run_daily_vol_select import bootstrap

ROOT = os.path.dirname(os.path.abspath(__file__))
IS = dict(start="2021-03-24", end="2023-12-31")
OOS = dict(start="2024-01-01", end="2026-12-31")
FULL = dict(start=None, end=None)
EXEC = dict(spread=0.015)


def mar(r):
    return r["cagr"] / max(abs(r["maxdd"]), 1.0)


def clean(r):
    return {k: v for k, v in r.items() if k != "equity"}


def run3(days, paths, cfg):
    return {lab: clean(simulate(days, paths, **cfg, **per)) for lab, per in (("IS", IS), ("OOS", OOS), ("full", FULL))}


def stage(days, paths, base, grid, label, log):
    rows, best, best_kw = [], None, None
    for name, kw in grid:
        cfg = {**base, **kw}
        r = simulate(days, paths, **cfg, **IS)
        o = simulate(days, paths, **cfg, **OOS)
        rows.append(dict(name=name, IS=clean(r), OOS=clean(o)))
        if best is None or mar(r) > mar(best):
            best, best_kw, best_name = r, kw, name
    print(f"\n== {label}")
    for x in rows:
        mk = " <" if x["name"] == best_name else ""
        print(f"   {x['name']:46s} IS {x['IS']['cagr']:+6.1f}%/{x['IS']['maxdd']:6.1f}%  OOS {x['OOS']['cagr']:+6.1f}%/{x['OOS']['maxdd']:6.1f}%{mk}")
    log[label] = dict(rows=rows, chosen=best_name)
    return {**base, **best_kw}


def main():
    days, paths = load_days()
    log = {}
    cfg = dict(EXEC)
    cfg = stage(days, paths, cfg, [
        ("Sell every day", dict(side_by_dow={d: 1 for d in range(7)})),
        ("Sell Sat", dict(side_by_dow={5: 1})),
        ("Buy Sun", dict(side_by_dow={6: -1})),
        ("Sell Sat + buy Sun", dict(side_by_dow={5: 1, 6: -1})),
        ("Sell Fri + Sat + buy Sun", dict(side_by_dow={4: 1, 5: 1, 6: -1})),
    ], "1. Which days, which side", log)
    cfg = stage(days, paths, cfg, [
        ("No hedge", dict(hedge=False, hedge_buy=False)),
        ("Hedge the Saturday short hourly", dict(hedge=True, hedge_buy=False, band=0.25, stop_h=1)),
        ("Hedge the Sunday long hourly", dict(hedge=False, hedge_buy=True, band=0.25, stop_h=1)),
        ("Hedge both", dict(hedge=True, hedge_buy=True, band=0.25, stop_h=1)),
    ], "2. Delta hedging", log)
    cfg = stage(days, paths, cfg, [
        ("No filters", dict(iv_filter=None, buy_filter=None)),
        ("Sell Sat only if IV >= 0.8x 7-day realised", dict(iv_filter=0.8, buy_filter=None)),
        ("Sell Sat only if IV >= 1.0x", dict(iv_filter=1.0, buy_filter=None)),
        ("Buy Sun only if IV <= 1.0x 7-day realised", dict(iv_filter=None, buy_filter=1.0)),
        ("Buy Sun only if IV <= 1.2x", dict(iv_filter=None, buy_filter=1.2)),
    ], "3. Price filters", log)
    cfg = stage(days, paths, cfg, [
        ("No recovery", dict(recovery=False)),
        ("Recover over 2 Saturdays, cap 1.5x", dict(recovery=True, rec_k=2, rec_cap=1.5)),
        ("Recover over 4 Saturdays, cap 1.5x", dict(recovery=True, rec_k=4, rec_cap=1.5)),
        ("Recover over 4 Saturdays, cap 2x", dict(recovery=True, rec_k=4, rec_cap=2.0)),
        ("Recover over 8 Saturdays, cap 2x", dict(recovery=True, rec_k=8, rec_cap=2.0)),
        ("Recover over 4 Saturdays, cap 3x", dict(recovery=True, rec_k=4, rec_cap=3.0)),
    ], "4. Loss recovery on the Saturday sell", log)
    lev_rows = []
    print("\n== 5. Leverage (notional per leg / equity)")
    for ls, lb in ((0.5, 0.5), (1.0, 0.5), (1.0, 1.0), (1.5, 1.0), (2.0, 1.0), (2.0, 2.0)):
        r = run3(days, paths, {**cfg, "lev": ls, "lev_buy": lb})
        lev_rows.append(dict(sell=ls, buy=lb, **r))
        print(f"   sell {ls}x / buy {lb}x: IS {r['IS']['cagr']:+.1f}%/{r['IS']['maxdd']:.1f}%  OOS {r['OOS']['cagr']:+.1f}%/{r['OOS']['maxdd']:.1f}%  "
              f"full {r['full']['cagr']:+.1f}%/{r['full']['maxdd']:.1f}% worst day {r['full']['worst_day']:+.1f}%")
    final = dict(cfg)
    res_final = run3(days, paths, final)
    eq = simulate(days, paths, **final)["equity"]
    sens = {}
    for lab, kw in (("Sell/buy at mark (maker)", dict(spread=0.0)), ("1.5% from mark (base)", dict(spread=0.015)),
                    ("3% from mark (taker)", dict(spread=0.03)), ("Options 5% cheaper", dict(iv_mult=0.95)),
                    ("Options 5% richer", dict(iv_mult=1.05))):
        sens[lab] = clean(simulate(days, paths, **{**final, **kw}))
    print("\n== Sensitivity (full period)")
    for k, v in sens.items():
        print(f"   {k:28s} CAGR {v['cagr']:+.1f}%  DD {v['maxdd']:.1f}%  Sharpe {v['sharpe']:.2f}")
    print("\n== Block bootstrap, 2 years, 300 paths")
    boot = {}
    for lab, c in (("Sell Sat + buy Sun", {**EXEC, "side_by_dow": {5: 1, 6: -1}}), ("Final", final),
                   ("Sell every day (for contrast)", {**EXEC, "side_by_dow": {d: 1 for d in range(7)}})):
        boot[lab] = bootstrap(days, paths, c)
        b = boot[lab]
        print(f"   {lab:32s} CAGR median {b['cagr_median']:+.1f}% (5% {b['cagr_p5']:+.1f}%, 95% {b['cagr_p95']:+.1f}%)  loss after 2y {b['loss_2y']:.0%}  "
              f"maxDD median {b['dd_median']:.1f}% (5% {b['dd_p5']:.1f}%)")
    # per-leg contribution in the final configuration
    legs = {}
    for lab, sb in (("Saturday sell only", {5: 1}), ("Sunday buy only", {6: -1})):
        legs[lab] = clean(simulate(days, paths, **{**final, "side_by_dow": sb}))
    out = dict(final_cfg={k: (dict(v) if isinstance(v, dict) else v) for k, v in final.items()}, stages=log, leverage=lev_rows,
               final=res_final, sensitivity=sens, bootstrap=boot, legs=legs,
               series=(eq.resample("W").last() / 1000).round(2).tolist(), dates=[str(d.date()) for d in eq.resample("W").last().index])
    print("\nFINAL", final)
    print({k: (round(v['cagr'], 1), round(v['maxdd'], 1), round(v['sharpe'], 2)) for k, v in res_final.items()}, res_final["full"]["yearly"])
    with open(os.path.join(ROOT, "results", "weekend_vol.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
