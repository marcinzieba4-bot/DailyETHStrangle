"""
"Accumulate on dips, distribute on rallies" — the clarified plan.

Every day sell an ATM call (only on ETH that is still unhedged) and an ATM put, both of `lot` ETH.
  * Down day: the put is assigned -> buy `lot` more exposure (cover perp first, otherwise new wstETH that is kept).
  * Up day:   the call is assigned -> short `lot` ETH-PERP against the wstETH (staking keeps running).
  * Fully hedged (net ETH = 0) -> done: sell the wstETH, close the perp.
No cap on adding unless stated. Horizon 1 year (MC) and 1 / 2 years (history); paths not flat by then are marked to market.

  python3 run_accumulate.py      -> results/accumulate.json
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim.engine import Carry, Costs, Strategy, run  # noqa: E402
from sim import data as D  # noqa: E402
from sim import scenarios as SC  # noqa: E402

OUT = os.path.join(D.ROOT, "results")
N = 10000
T = 365

LOTS = [2.5, 6.25, 15.6, 62.45]   # ETH per day (4%, 10%, 25%, 100% of the 62.45 ETH position)


def strategies(Q0):
    st = {f"{l:g} ETH/day": Strategy(f"U{l:g}", lot_frac=l / Q0, cap_mult=None, mode="exit") for l in LOTS}
    st["6.25 ETH/day, max 2x"] = Strategy("U6c", lot_frac=6.25 / Q0, cap_mult=2.0, mode="exit")
    st["6.25 ETH/day, sell only >= start, max 2x"] = Strategy("U6f", lot_frac=6.25 / Q0, cap_mult=2.0, mode="exit", call_floor_rel=1.0)
    st["6.25 ETH/day, sell only >= start, no cap"] = Strategy("U6g", lot_frac=6.25 / Q0, cap_mult=None, mode="exit", call_floor_rel=1.0)
    return st


def mc_ou(pools, P0, start_dow, vol, anchor, half_life, n, T, seed):
    """Mean-reverting log price around `anchor` (Ornstein-Uhlenbeck drift), bootstrapped shocks."""
    rng = np.random.default_rng(seed)
    k = np.log(2) / half_life
    logS = np.full(n, np.log(P0))
    out = np.empty((n, T + 1))
    out[:, 0] = P0
    sd = vol / 100 / np.sqrt(365)
    for t in range(T):
        pool = pools[(start_dow + t) % 7]
        z = pool[rng.integers(0, len(pool), n)]
        corr = np.log(np.mean(np.exp(pool * sd)))
        logS = logS + k * (np.log(anchor) - logS) + z * sd - corr
        out[:, t + 1] = np.exp(logS)
    return out


def scenarios(P0, T):
    t = np.arange(T)
    sc = {}
    sc["No view (random walk)"] = dict(kind="drift", rv=56, iv=56, mu=np.zeros(T), fund=0.015,
                                      desc="Zero drift, today's vol (56).")
    sc["Mean-reverts to $2,000"] = dict(kind="ou", rv=56, iv=56, anchor=2000.0, half_life=60, fund=0.015,
                                       desc="Your thesis, strict form: price is pulled back to $2,000 (half-life 60 days).")
    sc["Dip -35%, then recovers to $2,400"] = dict(kind="drift", rv=60, iv=60, fund=0.02,
                                                  mu=np.where(t < 90, np.log(0.65) / 90, np.log(1.2 / 0.65) / (T - 90)),
                                                  desc="Average path falls to ~$1,300 over 3 months and ends the year at ~$2,400.")
    sc["Bull +80% in a year"] = dict(kind="drift", rv=56, iv=56, mu=np.full(T, np.log(1.8) / T), fund=0.08,
                                     desc="Average path rises steadily to ~$3,600.")
    sc["2022 replay: -60% in 6 months, then flat"] = dict(kind="drift", rv=70, iv=70, fund=0.0,
                                                          mu=np.where(t < 180, np.log(0.4) / 180, 0.0),
                                                          desc="Average path slides to ~$800 and stays there.")
    sc["Slow bleed -50% in a year"] = dict(kind="drift", rv=65, iv=65, mu=np.full(T, np.log(0.5) / T), fund=0.0,
                                           desc="Average path grinds down to ~$1,000 by year end.")
    return sc


def summarize(o, P0, Q0, hodl_rel=None):
    rel = o["rel_pct"] * 100
    cl = o["cleared"]
    pn = o["pnl"]
    edge = pn["premium"] - pn["payout"]
    inv = o["rel"] - (edge + pn["staking"] + pn["funding"] - pn["costs"])
    pos = Q0 * P0
    s = dict(
        mean=float(rel.mean()), median=float(np.median(rel)), p5=float(np.percentile(rel, 5)),
        p1=float(np.percentile(rel, 1)), p95=float(np.percentile(rel, 95)), beat=float(np.mean(rel > 0)),
        cleared=float(cl.mean()), cleared_90=float(np.mean(cl & (o["tau"] <= 90))),
        days_med=float(np.median(o["tau"][cl])) if cl.any() else None,
        exit_prem_cleared_med=float(np.median(rel[cl])) if cl.any() else None,
        exit_prem_cleared_p10=float(np.percentile(rel[cl], 10)) if cl.any() else None,
        rel_notcleared_med=float(np.median(rel[~cl])) if (~cl).any() else None,
        exit_px_cleared_med=float(np.median(o["V_tau"][cl] / Q0)) if cl.any() else None,
        exit_px_cleared_p10=float(np.percentile(o["V_tau"][cl] / Q0, 10)) if cl.any() else None,
        exit_px_cleared_p90=float(np.percentile(o["V_tau"][cl] / Q0, 90)) if cl.any() else None,
        mkt_px_cleared_med=float(np.median(o["S_tau"][cl])) if cl.any() else None,
        value_px_all_med=float(np.median(o["V_T"] / Q0)), value_px_all_p5=float(np.percentile(o["V_T"] / Q0, 5)),
        maxW_med=float(np.median(o["maxW"])), maxW_p95=float(np.percentile(o["maxW"], 95)),
        cash_need_med=float(-np.median(o["min_cash"])), cash_need_p95=float(-np.percentile(o["min_cash"], 5)),
        eth_end_med=float(np.median(np.where(cl, 0, o["E_T"]))), eth_end_p95=float(np.percentile(np.where(cl, 0, o["E_T"]), 95)),
        pnl_edge=float(edge.mean() / pos * 100), pnl_staking=float(pn["staking"].mean() / pos * 100),
        pnl_funding=float(pn["funding"].mean() / pos * 100), pnl_costs=float(-pn["costs"].mean() / pos * 100),
        pnl_inventory=float(inv.mean() / pos * 100), premium=float(pn["premium"].mean() / pos * 100),
    )
    if hodl_rel is not None:
        d = rel - hodl_rel
        s.update(vs_hodl_mean=float(d.mean()), vs_hodl_median=float(np.median(d)), beat_hodl=float(np.mean(d > 0)))
    return s


def main():
    live = D.load_live()
    hist = D.load_history()
    P0 = float(live["eth_index"])
    Q0 = 50 * float(live["wsteth_ratio"])
    start_dow = pd.Timestamp(live["ts_utc"]).dayofweek
    day1 = float(live["daily_atm_iv"])
    costs = Costs()
    ST = strategies(Q0)
    pools = SC.z_pools(hist)
    R = dict(P0=P0, Q0=Q0, lots=LOTS, mc={}, hist={})

    hodl = Strategy("H", hold=True, mode="income")
    for i, (name, sc) in enumerate(scenarios(P0, T).items()):
        if sc["kind"] == "ou":
            S = mc_ou(pools, P0, start_dow, sc["rv"], sc["anchor"], sc["half_life"], N, T, seed=500 + i)
        else:
            S = SC.mc_paths(pools, P0, start_dow, np.full(T, sc["rv"]), sc["mu"], N, T, seed=500 + i)
        iv = np.broadcast_to(SC.iv_path(np.full(T, sc["iv"]), start_dow, T, day1 * sc["iv"] / 56), S[:, :-1].shape)
        carry = Carry(stake_apr=float(live["steth_apr"]), fund_apr=sc["fund"])
        h = run(S, iv, Q0, hodl, costs, carry)
        row = dict(desc=sc["desc"], eth_end_med=float(np.median(S[:, -1])), eth_end_p5=float(np.percentile(S[:, -1], 5)),
                   eth_end_p95=float(np.percentile(S[:, -1], 95)), eth_min_med=float(np.median(S.min(1))),
                   hodl=dict(mean=float(h["rel_pct"].mean() * 100), median=float(np.median(h["rel_pct"]) * 100),
                             p5=float(np.percentile(h["rel_pct"], 5) * 100)))
        for k, st in ST.items():
            row[k] = summarize(run(S, iv, Q0, st, costs, carry), P0, Q0, h["rel_pct"] * 100)
        if name.startswith("No view"):
            c4 = Carry(stake_apr=float(live["steth_apr"]), fund_apr=sc["fund"], usd_apr=0.04)
            row["usd4"] = {k: summarize(run(S, iv, Q0, ST[k], costs, c4), P0, Q0) for k in ["6.25 ETH/day", "2.5 ETH/day"]}
        R["mc"][name] = row
        print(name, "done")

    for horizon in (365, 730):
        Sx, ivx, fx, sx, dates = SC.hist_windows(hist, horizon)
        h = run(Sx, ivx, Q0, hodl, costs, Carry(), fund=fx, stake=sx)
        blk = dict(n=int(len(dates)), start=str(dates[0].date()), end=str(dates[-1].date()),
                   hodl=dict(mean=float(h["rel_pct"].mean() * 100), median=float(np.median(h["rel_pct"]) * 100),
                             p5=float(np.percentile(h["rel_pct"], 5) * 100), beat=float(np.mean(h["rel_pct"] > 0))))
        ts = {"date": [str(d.date()) for d in dates], "price": Sx[:, 0].round(2).tolist(),
              "hodl": (h["rel_pct"] * 100).round(2).tolist()}
        for k, st in ST.items():
            o = run(Sx, ivx, Q0, st, costs, Carry(), fund=fx, stake=sx)
            blk[k] = summarize(o, P0, Q0, h["rel_pct"] * 100)
            ts[k] = (o["rel_pct"] * 100).round(2).tolist()
            ts[k + "|maxW"] = o["maxW"].round(1).tolist()
            ts[k + "|cash"] = (-o["min_cash"] / Sx[:, 0] * P0).round(0).tolist()   # rescaled to today's price
        blk["ts"] = ts
        R["hist"][str(horizon)] = blk
        print("history", horizon, "done")

    with open(os.path.join(OUT, "accumulate.json"), "w") as fh:
        json.dump(R, fh, indent=1)
    show(R)


def show(R):
    cols = ["mean", "median", "p5", "beat", "cleared", "days_med", "exit_prem_cleared_med", "rel_notcleared_med", "exit_px_cleared_med",
            "maxW_med", "maxW_p95", "cash_need_med", "cash_need_p95", "vs_hodl_mean", "pnl_edge", "pnl_staking", "pnl_inventory"]
    for name, row in R["mc"].items():
        print(f"\n== {name}: ETH end median {row['eth_end_med']:.0f} (p5 {row['eth_end_p5']:.0f}, p95 {row['eth_end_p95']:.0f}); HODL mean {row['hodl']['mean']:+.1f}% p5 {row['hodl']['p5']:+.1f}%")
        df = pd.DataFrame({k: v for k, v in row.items() if isinstance(v, dict) and "mean" in v and k not in ("hodl",)}).T
        print(df[[c for c in cols if c in df]].round(2).to_string())
        if "usd4" in row:
            print("with 4% USD yield:", {k: round(v["mean"], 2) for k, v in row["usd4"].items()})
    for hz, blk in R["hist"].items():
        print(f"\n== HISTORY {hz}d windows ({blk['n']} starts {blk['start']}..{blk['end']}); HODL mean {blk['hodl']['mean']:+.1f}% med {blk['hodl']['median']:+.1f}% p5 {blk['hodl']['p5']:+.1f}%")
        df = pd.DataFrame({k: v for k, v in blk.items() if isinstance(v, dict) and "cleared" in v}).T
        print(df[[c for c in cols if c in df]].round(2).to_string())


if __name__ == "__main__":
    main()
