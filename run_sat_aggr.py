"""
Saturday straddle selling: aggressive recovery sizing and a delta hedge that switches on after a 3% move.

Recovery (sell side, while below the high):
  dd   : extra notional = drawdown / (K x premium rate)      (K=1: one Saturday's premium on the extra recovers it all)
  last : extra notional = last Saturday's loss / (K x premium rate)   (only the most recent loss)
  cap  : total notional <= cap x normal (None = no cap); margin_cap: notional <= 3x equity (Deribit standard margin limit)
Trigger hedge: delta-hedge the short straddle with ETH-PERP only once ETH is >= trigger away from the strike (hourly
closes); 'static' = hedge once and hold, 'dynamic' = keep rebalancing, reset = remove the hedge if ETH comes back
within half the trigger. Hedge trades cost 0.05%.

  python3 run_sat_aggr.py   -> results/sat_aggr.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from run_daily_vol import load_days, simulate
from run_daily_vol_select import bootstrap
from run_sat_mon import detail

ROOT = os.path.dirname(os.path.abspath(__file__))
IS = dict(start="2021-03-24", end="2023-12-31")
OOS = dict(start="2024-01-01", end="2026-12-31")
SAT = dict(spread=0.015, side_by_dow={5: 1})
CAL = dict(spread=0.015, side_by_dow={5: 1, 6: -1})


def clean(r):
    return {k: v for k, v in r.items() if k not in ("equity", "trades")}


def three(days, paths, cfg):
    return {lab: clean(simulate(days, paths, **cfg, **per)) for lab, per in (("IS", IS), ("OOS", OOS), ("full", {}))}


def line(name, r):
    f = r["full"]
    wipe = " WIPED OUT" if f["final"] <= 0.05 else ""
    return (f"   {name:52s} IS {r['IS']['cagr']:+7.1f}%/{r['IS']['maxdd']:6.1f}%  OOS {r['OOS']['cagr']:+7.1f}%/{r['OOS']['maxdd']:6.1f}%  "
            f"full {f['cagr']:+7.1f}%/{f['maxdd']:7.1f}%  Sh {f['sharpe']:5.2f}  worst {f['worst_day']:+6.1f}%  max notional {f['max_notional_x']:5.1f}x{wipe}")


def main():
    days, paths = load_days()
    out = {}
    rec = {
        "No recovery": {},
        "Gentle: drawdown over 8 Saturdays, cap 1.5×": dict(recovery=True, rec_k=8, rec_cap=1.5),
        "Drawdown over 4 Saturdays, cap 2×": dict(recovery=True, rec_k=4, rec_cap=2.0),
        "Drawdown over 2 Saturdays, cap 3×": dict(recovery=True, rec_k=2, rec_cap=3.0),
        "Drawdown in 1 Saturday, cap 3×": dict(recovery=True, rec_k=1, rec_cap=3.0),
        "Drawdown in 1 Saturday, cap 5×": dict(recovery=True, rec_k=1, rec_cap=5.0),
        "Drawdown in 1 Saturday, cap 10×": dict(recovery=True, rec_k=1, rec_cap=10.0),
        "Drawdown in 1 Saturday, no cap": dict(recovery=True, rec_k=1, rec_cap=None),
        "Drawdown in 1 Saturday, no cap, margin limit 3×": dict(recovery=True, rec_k=1, rec_cap=None, margin_cap=3.0),
        "Last loss in 1 Saturday, cap 2×": dict(recovery=True, rec_k=1, rec_cap=2.0, rec_mode="last"),
        "Last loss in 1 Saturday, cap 3×": dict(recovery=True, rec_k=1, rec_cap=3.0, rec_mode="last"),
        "Last loss in 1 Saturday, no cap, margin limit 3×": dict(recovery=True, rec_k=1, rec_cap=None, rec_mode="last", margin_cap=3.0),
    }
    print("== 1. Recovery aggressiveness (Saturday sell, no hedge)")
    out["recovery"] = {}
    for name, kw in rec.items():
        r = three(days, paths, {**SAT, **kw})
        out["recovery"][name] = r
        print(line(name, r))

    hed = {
        "No hedge": {},
        "Hourly hedge all day (for reference)": dict(hedge=True, band=0.25, stop_h=1),
        "Hedge after 2% move, hold": dict(hedge_trigger=0.02, trigger_mode="static"),
        "Hedge after 3% move, hold": dict(hedge_trigger=0.03, trigger_mode="static"),
        "Hedge after 4% move, hold": dict(hedge_trigger=0.04, trigger_mode="static"),
        "Hedge after 5% move, hold": dict(hedge_trigger=0.05, trigger_mode="static"),
        "Hedge after 3% move, keep re-hedging": dict(hedge_trigger=0.03, trigger_mode="dynamic"),
        "Hedge after 3% move, hold, remove if back within 1.5%": dict(hedge_trigger=0.03, trigger_mode="static", trigger_reset=0.5),
        "Hedge after 3% move, keep re-hedging, remove if back within 1.5%": dict(hedge_trigger=0.03, trigger_mode="dynamic", trigger_reset=0.5),
    }
    print("\n== 2. Delta hedge after a move (Saturday sell, no recovery)")
    out["hedge"] = {}
    for name, kw in hed.items():
        r = three(days, paths, {**SAT, **kw})
        out["hedge"][name] = r
        print(line(name, r))

    print("\n== 3. Aggressive recovery + 3% hedge (Saturday sell)")
    h3 = dict(hedge_trigger=0.03, trigger_mode="static")
    combo = {
        "Gentle recovery + 3% hedge": {**rec["Gentle: drawdown over 8 Saturdays, cap 1.5×"], **h3},
        "Drawdown over 2 Saturdays, cap 3× + 3% hedge": {**rec["Drawdown over 2 Saturdays, cap 3×"], **h3},
        "Drawdown in 1 Saturday, cap 3× + 3% hedge": {**rec["Drawdown in 1 Saturday, cap 3×"], **h3},
        "Last loss in 1 Saturday, cap 3× + 3% hedge": {**rec["Last loss in 1 Saturday, cap 3×"], **h3},
        "Drawdown in 1 Saturday, no cap, margin 3× + 3% hedge": {**rec["Drawdown in 1 Saturday, no cap, margin limit 3×"], **h3},
    }
    out["combo"] = {}
    for name, kw in combo.items():
        r = three(days, paths, {**SAT, **kw})
        out["combo"][name] = r
        print(line(name, r))

    print("\n== 4. Weekend calendar (sell Sat + buy Sun) with these on the Saturday leg")
    cal = {
        "Calendar, plain": {},
        "Calendar + gentle recovery": rec["Gentle: drawdown over 8 Saturdays, cap 1.5×"],
        "Calendar + 3% hedge on Saturday": h3,
        "Calendar + gentle recovery + 3% hedge": {**rec["Gentle: drawdown over 8 Saturdays, cap 1.5×"], **h3},
        "Calendar + drawdown over 2 Sat, cap 3× + 3% hedge": {**rec["Drawdown over 2 Saturdays, cap 3×"], **h3},
        "Calendar + last loss in 1 Sat, cap 3× + 3% hedge": {**rec["Last loss in 1 Saturday, cap 3×"], **h3},
    }
    out["calendar"] = {}
    for name, kw in cal.items():
        r = three(days, paths, {**CAL, **kw})
        out["calendar"][name] = r
        print(line(name, r))

    print("\n== 5. Block bootstrap (2 years, 300 paths)")
    out["boot"] = {}
    for name, c in [("Sat: no recovery", SAT), ("Sat: gentle recovery", {**SAT, **rec["Gentle: drawdown over 8 Saturdays, cap 1.5×"]}),
                    ("Sat: drawdown over 2, cap 3×", {**SAT, **rec["Drawdown over 2 Saturdays, cap 3×"]}),
                    ("Sat: drawdown in 1, cap 3×", {**SAT, **rec["Drawdown in 1 Saturday, cap 3×"]}),
                    ("Sat: drawdown in 1, cap 10×", {**SAT, **rec["Drawdown in 1 Saturday, cap 10×"]}),
                    ("Sat: last loss in 1, cap 3×", {**SAT, **rec["Last loss in 1 Saturday, cap 3×"]}),
                    ("Sat: 3% hedge, no recovery", {**SAT, **h3}),
                    ("Sat: drawdown over 2, cap 3× + 3% hedge", {**SAT, **combo["Drawdown over 2 Saturdays, cap 3× + 3% hedge"]}),
                    ("Calendar + gentle recovery + 3% hedge", {**CAL, **cal["Calendar + gentle recovery + 3% hedge"]}),
                    ("Calendar + drawdown over 2 Sat, cap 3× + 3% hedge", {**CAL, **cal["Calendar + drawdown over 2 Sat, cap 3× + 3% hedge"]})]:
        b = bootstrap(days, paths, c)
        out["boot"][name] = b
        print(f"   {name:52s} median {b['cagr_median']:+6.1f}%/yr (5% {b['cagr_p5']:+6.1f}%)  lost money {b['loss_2y']:.0%}  "
              f"median DD {b['dd_median']:6.1f}% (5% {b['dd_p5']:6.1f}%)  DD>50% {b['dd_gt50']:.0%}  lost 80%+ {b['ruin']:.0%}")

    out["detail_aggr"] = detail(days, paths, {**SAT, **combo["Drawdown over 2 Saturdays, cap 3× + 3% hedge"]},
                                "Sat sell: drawdown over 2 Saturdays, cap 3× + 3% hedge")
    out["detail_hedge"] = detail(days, paths, {**SAT, **h3}, "Sat sell: 3% hedge, no recovery")
    out["detail_cal"] = detail(days, paths, {**CAL, **cal["Calendar + gentle recovery + 3% hedge"]}, "Calendar + gentle recovery + 3% hedge")
    with open(os.path.join(ROOT, "results", "sat_aggr.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
