"""
"1 ETH a day, never below 50 wstETH under $2,000" — the third version of the plan.

Every day sell an ATM put and an ATM call of `lot` ETH (default 1 ETH).
  * Down day: put assigned -> take `lot` ETH (cover a perp short first, otherwise buy new wstETH and keep it).
  * Up day:   call assigned -> short `lot` ETH-PERP against the wstETH.
  * Below the start price (~$2,000) calls are only sold on ETH bought above the 50 wstETH base,
    so exposure never drops below 50 wstETH there. Above it, the base is hedged gradually too.
  * Done when fully hedged (only possible above the start price). Horizon 2 years.

  python3 run_range.py   -> results/range.json
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
from run_accumulate import mc_ou  # noqa: E402

OUT = os.path.join(D.ROOT, "results")
N = 10000
T = 730


def strategies(Q0):
    return {
        "1 ETH/day": Strategy("R1", lot_frac=1.0 / Q0, cap_mult=None, mode="exit", base_floor_rel=1.0),
        "3 ETH/day": Strategy("R3", lot_frac=3.0 / Q0, cap_mult=None, mode="exit", base_floor_rel=1.0),
    }


def bridge(S, target, t0=0):
    """Rescale paths from day t0 on so each ends exactly at `target` (log-linear drift correction)."""
    T = S.shape[1] - 1
    w = np.clip((np.arange(T + 1) - t0) / (T - t0), 0, 1)
    return S * np.exp(np.log(target / S[:, -1:]) * w)


def median_paths(pools, P0, start_dow, vol, mu, n, T, seed):
    """Bootstrap paths whose MEDIAN follows the log-drift mu (no martingale correction)."""
    rng = np.random.default_rng(seed)
    sd = vol / 100 / np.sqrt(365)
    r = np.empty((n, T))
    for t in range(T):
        pool = pools[(start_dow + t) % 7]
        r[:, t] = pool[rng.integers(0, len(pool), n)] * sd + mu[t]
    return P0 * np.exp(np.concatenate([np.zeros((n, 1)), np.cumsum(r, axis=1)], axis=1))


def scenarios(P0):
    t = np.arange(T)
    sc = {}
    sc["Range around $2,000"] = dict(kind="ou", vol=56, anchor=2000.0, hl=60, fund=0.015,
                                     desc="Pulled back to $2,000 (half-life 60 days), today's vol.")
    sc["Calm range around $2,000"] = dict(kind="ou", vol=40, anchor=2000.0, hl=30, fund=0.015,
                                          desc="Tighter range, vol 40.")
    sc["Wanders, back at $2,000 after 2 years"] = dict(kind="bridge", vol=56, target=2000.0, fund=0.015,
                                                       desc="Free random walk that happens to end at $2,000.")
    sc["No view (random walk)"] = dict(kind="drift", vol=56, mu=np.zeros(T), fund=0.015, desc="Zero drift, today's vol.")
    sc["Dips to ~$1,200, back at $2,000"] = dict(kind="dipbridge", vol=60, fund=0.01, target=2000.0, t0=180,
                                                mu=np.where(t < 180, np.log(0.6) / 180, 0.0),
                                                desc="Typical path falls 40% over 6 months; every path is back at $2,000 after 2 years.")
    sc["2022 replay: −60%, no recovery"] = dict(kind="median", vol=70, fund=0.0, mu=np.where(t < 180, np.log(0.4) / 180, 0.0),
                                                desc="Typical path slides to ~$800 in 6 months and stays there.")
    sc["Bull: doubles in 2 years"] = dict(kind="median", vol=56, fund=0.06, mu=np.full(T, np.log(2.0) / T),
                                          desc="Typical path rises steadily to ~$4,000.")
    return sc


def summarize(o, h, P0, Q0):
    pos = Q0 * P0
    rel = o["rel"]
    d = o["rel"] - h["rel"]                    # vs keeping the 50 wstETH, USD
    pn, ph = o["pnl"], h["pnl"]
    edge = pn["premium"] - pn["payout"]
    extra_stake = pn["staking"] - ph["staking"]
    grid = d - edge - extra_stake - pn["funding"] + (pn["costs"] - ph["costs"])
    add_px = np.where(o["add_eth"] > 0, o["add_val"] / np.maximum(o["add_eth"], 1e-9), np.nan)
    hed_px = np.where(o["hedge_eth"] > 0, o["hedge_val"] / np.maximum(o["hedge_eth"], 1e-9), np.nan)
    E_T = np.where(o["cleared"], 0.0, o["E_T"])
    q = lambda x, p: float(np.nanpercentile(x, p))
    return dict(
        vs_sell_mean=float(rel.mean() / pos * 100), vs_sell_med=q(rel / pos * 100, 50), vs_sell_p5=q(rel / pos * 100, 5),
        vs_hodl_mean_usd=float(d.mean()), vs_hodl_med_usd=q(d, 50), vs_hodl_p5_usd=q(d, 5), vs_hodl_p95_usd=q(d, 95),
        beat_hodl=float(np.mean(d > 0)),
        hodl_vs_sell_mean=float(h["rel"].mean() / pos * 100), hodl_vs_sell_med=q(h["rel"] / pos * 100, 50),
        hodl_vs_sell_p5=q(h["rel"] / pos * 100, 5),
        premium_usd=float(pn["premium"].mean()), payout_usd=float(pn["payout"].mean()), edge_usd=float(edge.mean()),
        extra_stake_usd=float(extra_stake.mean()), funding_usd=float(pn["funding"].mean()),
        costs_usd=float(-(pn["costs"] - ph["costs"]).mean()), grid_usd=float(grid.mean()),
        added_eth_med=q(o["add_eth"], 50), add_px_med=q(add_px, 50),
        hedged_eth_med=q(o["hedge_eth"], 50), hedge_px_med=q(hed_px, 50),
        spread_med=q(hed_px - add_px, 50),
        E_T_med=q(E_T, 50), E_T_p5=q(E_T, 5), E_T_p95=q(E_T, 95),
        maxW_p95=q(o["maxW"], 95), cash_med=-q(o["min_cash"], 50), cash_p95=-q(o["min_cash"], 5),
        cleared=float(o["cleared"].mean()),
    )


def main():
    live = D.load_live()
    hist = D.load_history()
    P0 = float(live["eth_index"])
    Q0 = 50 * float(live["wsteth_ratio"])
    start_dow = pd.Timestamp(live["ts_utc"]).dayofweek
    day1 = float(live["daily_atm_iv"])
    costs = Costs()
    ST = strategies(Q0)
    hodl = Strategy("H", hold=True, mode="income")
    pools = SC.z_pools(hist)
    R = dict(P0=P0, Q0=Q0, T=T, mc={}, hist={})
    for i, (name, sc) in enumerate(scenarios(P0).items()):
        if sc["kind"] == "ou":
            S = mc_ou(pools, P0, start_dow, sc["vol"], sc["anchor"], sc["hl"], N, T, seed=900 + i)
        elif sc["kind"] in ("median", "dipbridge"):
            S = median_paths(pools, P0, start_dow, sc["vol"], sc["mu"], N, T, seed=900 + i)
            if sc["kind"] == "dipbridge":
                S = bridge(S, sc["target"], sc["t0"])
        else:
            S = SC.mc_paths(pools, P0, start_dow, np.full(T, sc["vol"]), sc.get("mu", np.zeros(T)), N, T, seed=900 + i)
            if sc["kind"] == "bridge":
                S = bridge(S, sc["target"])
        iv = np.broadcast_to(SC.iv_path(np.full(T, sc["vol"]), start_dow, T, day1 * sc["vol"] / 56), S[:, :-1].shape)
        carry = Carry(stake_apr=float(live["steth_apr"]), fund_apr=sc["fund"])
        h = run(S, iv, Q0, hodl, costs, carry)
        row = dict(desc=sc["desc"], eth_end_med=float(np.median(S[:, -1])), eth_min_med=float(np.median(S.min(1))),
                   eth_max_med=float(np.median(S.max(1))), eth_p5_low=float(np.percentile(S.min(1), 5)))
        for k, st in ST.items():
            row[k] = summarize(run(S, iv, Q0, st, costs, carry), h, P0, Q0)
        if name.startswith("Range around"):
            c4 = Carry(stake_apr=float(live["steth_apr"]), fund_apr=sc["fund"], usd_apr=0.04)
            h4 = run(S, iv, Q0, hodl, costs, c4)
            row["usd4"] = summarize(run(S, iv, Q0, ST["1 ETH/day"], costs, c4), h4, P0, Q0)
            # what if IV is 10% richer/cheaper than assumed
            for lab, m in [("iv_rich", 1.1), ("iv_cheap", 0.9)]:
                ivm = np.broadcast_to(SC.iv_path(np.full(T, sc["vol"] * m), start_dow, T, day1 * sc["vol"] / 56 * m), S[:, :-1].shape)
                row[lab] = summarize(run(S, ivm, Q0, ST["1 ETH/day"], costs, carry), run(S, ivm, Q0, hodl, costs, carry), P0, Q0)
        R["mc"][name] = row
        print(name, "done", flush=True)

    for horizon in (365, 730):
        Sx, ivx, fx, sx, dates = SC.hist_windows(hist, horizon)
        h = run(Sx, ivx, Q0, hodl, costs, Carry(), fund=fx, stake=sx)
        blk = dict(n=int(len(dates)), start=str(dates[0].date()), end=str(dates[-1].date()))
        # scale USD figures to today's position size (history starts at other prices)
        scale = (Q0 * P0) / (Q0 * Sx[:, 0])
        ts = dict(date=[str(x.date()) for x in dates], price=Sx[:, 0].round(2).tolist(),
                  hodl=(h["rel_pct"] * 100).round(2).tolist())
        for k, st in ST.items():
            o = run(Sx, ivx, Q0, st, costs, Carry(), fund=fx, stake=sx)
            for key in ("rel",):
                o[key] = o[key] * scale
            for key in ("premium", "payout", "staking", "funding", "costs"):
                o["pnl"][key] = o["pnl"][key] * scale
            hh = dict(h)
            hh["rel"] = h["rel"] * scale
            hh["pnl"] = {kk: v * scale for kk, v in h["pnl"].items()}
            o["min_cash"] = o["min_cash"] * scale
            s = summarize(o, hh, P0, Q0)
            for key in ("add_px_med", "hedge_px_med", "spread_med"):
                s[key] = None   # historical prices are not comparable to today's
            px_add = o["add_val"] / np.maximum(o["add_eth"], 1e-9) / Sx[:, 0] * 100 - 100
            px_hed = o["hedge_val"] / np.maximum(o["hedge_eth"], 1e-9) / Sx[:, 0] * 100 - 100
            s["add_vs_start_med"] = float(np.nanmedian(np.where(o["add_eth"] > 0, px_add, np.nan)))
            s["hedge_vs_start_med"] = float(np.nanmedian(np.where(o["hedge_eth"] > 0, px_hed, np.nan)))
            blk[k] = s
            ts[k] = (o["rel"] / (Q0 * P0) * 100).round(2).tolist()
            ts[k + "|vs_hodl"] = ((o["rel"] - hh["rel"]) / (Q0 * P0) * 100).round(2).tolist()
        R["hist"][str(horizon)] = blk
        if horizon == 730:
            R["hist_ts"] = ts
        print("history", horizon, "done", flush=True)

    with open(os.path.join(OUT, "range.json"), "w") as fh:
        json.dump(R, fh, indent=1)
    show(R)


def show(R):
    cols = ["vs_sell_mean", "vs_sell_med", "vs_sell_p5", "hodl_vs_sell_mean", "vs_hodl_mean_usd", "vs_hodl_med_usd", "vs_hodl_p5_usd",
            "vs_hodl_p95_usd", "beat_hodl", "premium_usd", "payout_usd", "edge_usd", "extra_stake_usd", "funding_usd", "costs_usd", "grid_usd",
            "added_eth_med", "add_px_med", "hedged_eth_med", "hedge_px_med", "E_T_med", "E_T_p95", "maxW_p95", "cash_med", "cash_p95", "cleared"]
    for name, row in list(R["mc"].items()) + [("HIST " + k, v) for k, v in R["hist"].items()]:
        print(f"\n== {name}" + (f": ETH end median {row['eth_end_med']:.0f}, low median {row['eth_min_med']:.0f}, high median {row['eth_max_med']:.0f}" if 'eth_end_med' in row else ""))
        df = pd.DataFrame({k: v for k, v in row.items() if isinstance(v, dict) and k not in ("usd4", "iv_rich", "iv_cheap")}).T
        print(df[[c for c in cols if c in df]].round(1).T.to_string())
        for extra in ("usd4", "iv_rich", "iv_cheap"):
            if extra in row:
                print(extra, {k: round(row[extra][k], 1) for k in ["vs_hodl_mean_usd", "vs_sell_mean", "edge_usd", "grid_usd"]})


if __name__ == "__main__":
    main()
