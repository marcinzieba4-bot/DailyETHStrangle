"""
Buy a daily ATM straddle (put + call, next 08:00 UTC expiry) every day, with a recovery size-up.

Each day: buy a straddle on (base + extra) notional at the 08:00 settlement price; next day it pays |S1 - S0|.
A losing day (move smaller than the premium paid) adds to the accumulated loss L. The next day's extra
notional is sized so that a move of `target` x premium recovers L:
    extra = L / ((target - 1) x premium_rate)       target=2 -> a 2x-premium move wins back L
Wins pay down L; when L reaches 0 the size resets to base ("cycle closed"). Optional cap on total size.

Pricing: daily IV = DVOL (or 1.11 x 30-day realised before Mar 2021) x weekday factor (sim/scenarios.DOW_IV),
Black-Scholes 1-day straddle, bought 3.5% above value, 0.03% fee per leg. Prices: Deribit 08:00 settlements.
Base notional is fixed in USD (a % of starting capital), so results are additive and easy to read.

  python3 run_long_straddle.py   -> results/long_straddle.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from sim.engine import bs_call_put
from sim.scenarios import DOW_IV
from run_annual import load_prices

ROOT = os.path.dirname(os.path.abspath(__file__))
CAP = 125_000.0
HAIR = 0.035
FEE = 0.0003


def daily_iv(p):
    r = np.log(p).diff()
    rv = r.rolling(30, min_periods=10).std() * np.sqrt(365) * 100
    dv = pd.read_csv(os.path.join(ROOT, "data", "eth_dvol.csv"), parse_dates=["date"]).set_index("date")["dvol"]
    iv30 = dv.reindex(p.index).combine_first(rv * 1.11).ffill().bfill()
    return iv30 / 100 * DOW_IV[p.index.dayofweek]


def run(p, iv, base_frac=0.10, rule="martingale", target=2.0, cap=None):
    S, IV = p.to_numpy(), iv.to_numpy()
    base = base_frac * CAP
    L = 0.0
    pnl = np.zeros(len(S))
    size_x = np.zeros(len(S))
    Ls = np.zeros(len(S))
    cash = CAP
    ruined = None
    cycles, cur_len, cycle_lens, cycle_peakL = 0, 0, [], []
    peakL_cycle = 0.0
    streak, max_streak = 0, 0
    wins = 0
    traded = 0
    for t in range(len(S) - 1):
        s0, s1 = S[t], S[t + 1]
        c, pu = bs_call_put(s0, s0, IV[t], 1 / 365)
        cost_eth = (c + pu) * (1 + HAIR) + 2 * FEE * s0
        rate = cost_eth / s0
        notional = base
        if rule == "martingale" and L > 0:
            notional = base + L / ((target - 1) * rate)
            if cap is not None:
                notional = min(notional, cap * base)
        size = notional / s0
        if cash < size * cost_eth:                      # cannot pay the premium any more
            ruined = str(p.index[t].date())
            break
        res = size * (abs(s1 - s0) - cost_eth)
        traded += 1
        cash += res
        pnl[t + 1] = res
        size_x[t] = notional / base
        if res < 0:
            L += -res
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            wins += 1
            streak = 0
            L = max(0.0, L - res)
        peakL_cycle = max(peakL_cycle, L)
        cur_len += 1
        if L == 0.0:
            cycles += 1
            cycle_lens.append(cur_len)
            cycle_peakL.append(peakL_cycle)
            cur_len, peakL_cycle = 0, 0.0
        Ls[t] = L
    e = pd.Series(CAP + np.cumsum(pnl), index=p.index)
    if ruined:
        e[e.index > pd.Timestamp(ruined)] = e[pd.Timestamp(ruined)]
    yr = e.resample("YE").last()
    yearly = ((yr - yr.shift(1).fillna(CAP)) / CAP * 100)
    dd = e - e.cummax()
    days = len(S) - 1
    return dict(
        final=float(e.iloc[-1]), total_pct=float((e.iloc[-1] / CAP - 1) * 100),
        yearly={int(k.year): float(v) for k, v in yearly.items()},
        maxdd_usd=float(dd.min()), maxdd_pct=float((e / e.cummax() - 1).min() * 100),
        max_size_x=float(size_x.max()), max_L=float(Ls.max()), ruined=ruined,
        win_rate=float(wins / max(traded, 1)), max_losing_streak=int(max_streak), days_traded=int(traded),
        cycles=int(cycles), cycle_len_median=float(np.median(cycle_lens)) if cycle_lens else None,
        cycle_len_max=int(max(cycle_lens)) if cycle_lens else None,
        open_cycle_len=int(cur_len), open_L=float(L),
        cycle_peakL_p95=float(np.percentile(cycle_peakL, 95)) if cycle_peakL else None,
        series=(e.resample("W").last() / CAP * 100).round(2).tolist(),
        size_series=pd.Series(size_x, index=p.index).resample("W").max().round(2).tolist(),
    )


VARIANTS = {
    "Buy daily straddle, fixed size": dict(rule="fixed"),
    "Buy + size-up to recover losses (2× move), no cap": dict(rule="martingale", target=2.0),
    "Buy + size-up (2× move), cap 10× base": dict(rule="martingale", target=2.0, cap=10),
    "Buy + size-up (2× move), cap 5× base": dict(rule="martingale", target=2.0, cap=5),
    "Buy + size-up (1.5× move), no cap": dict(rule="martingale", target=1.5),
}


def show(name, r):
    print(f"{name:52s} total {r['total_pct']:+8.1f}%  maxDD {r['maxdd_pct']:6.1f}% (${r['maxdd_usd']:,.0f})  max size {r['max_size_x']:6.1f}x  "
          f"max accumulated loss ${r['max_L']:,.0f}  ruined {r['ruined']}  win days {r['win_rate']:.0%}  longest losing run {r['max_losing_streak']}  "
          f"cycles {r['cycles']} (median {r['cycle_len_median']} d, longest {r['cycle_len_max']} d)")
    print("       yearly % of capital:", {k: round(v, 1) for k, v in r["yearly"].items()})


def monte_carlo(n_paths=2000, days=730, base_frac=0.10, seed=11):
    """Bootstrap ETH daily moves (2021-2026 history, by weekday) at today's vol level; same pricing at DVOL 56."""
    from sim import data as D, scenarios as SC
    hist = D.load_history()
    pools = SC.z_pools(hist)
    S = SC.mc_paths(pools, 2009.1, 3, np.full(days, 56.0), np.zeros(days), n_paths, days, seed=seed)
    idx = pd.date_range("2026-11-12", periods=days + 1, freq="D")
    iv = pd.Series(0.56 * DOW_IV[idx.dayofweek], index=idx)
    res = {}
    for name, kw in VARIANTS.items():
        finals, ruin_1y, ruin_2y, maxdd = [], 0, 0, []
        for k in range(n_paths):
            r = run(pd.Series(S[k], index=idx), iv, base_frac=base_frac, **kw)
            finals.append(r["total_pct"])
            maxdd.append(r["maxdd_pct"])
            if r["ruined"]:
                dt = (pd.Timestamp(r["ruined"]) - idx[0]).days
                ruin_2y += 1
                ruin_1y += dt <= 365
        f = np.array(finals)
        res[name] = dict(ruin_1y=ruin_1y / n_paths, ruin_2y=ruin_2y / n_paths, median=float(np.median(f)),
                         p5=float(np.percentile(f, 5)), p95=float(np.percentile(f, 95)), profit=float(np.mean(f > 0)),
                         maxdd_median=float(np.median(maxdd)))
        print(f"  MC {name:52s} ruined within 1y {res[name]['ruin_1y']:.0%}, 2y {res[name]['ruin_2y']:.0%}  profitable {res[name]['profit']:.0%}  "
              f"median {res[name]['median']:+.0f}%  5% {res[name]['p5']:+.0f}%  95% {res[name]['p95']:+.0f}%")
    return res


def main():
    pall = load_prices()                       # Deribit 08:00 settlements, daily since Feb 2020
    ivall = daily_iv(pall)
    out = dict(cap=CAP, base_frac=0.10, variants={}, variants_2020={})
    p = pall[pall.index >= "2021-03-24"]       # DVOL era: real implied vol
    iv = ivall.reindex(p.index)
    out.update(start=str(p.index[0].date()), end=str(p.index[-1].date()),
               dates=[str(d.date()) for d in p.resample("W").last().index], price=p.resample("W").last().round(2).tolist())
    print("== From Mar 2021 (real DVOL)")
    for name, kw in VARIANTS.items():
        r = run(p, iv, **kw)
        out["variants"][name] = r
        show(name, r)
    print("== From Feb 2020 (IV estimated from realised vol before Mar 2021)")
    for name, kw in VARIANTS.items():
        r = run(pall, ivall, **kw)
        out["variants_2020"][name] = {k: v for k, v in r.items() if k not in ("series", "size_series")}
        print(f"   {name:52s} total {r['total_pct']:+.1f}%  ruined {r['ruined']}")
    out["base_sens"] = {}
    for bf in (0.02, 0.05, 0.10, 0.20):
        r = run(p, iv, base_frac=bf, rule="martingale", target=2.0)
        out["base_sens"][bf] = dict(total_pct=r["total_pct"], maxdd_pct=r["maxdd_pct"], ruined=r["ruined"], max_size_x=r["max_size_x"], max_L=r["max_L"])
        print(f"   base {bf:.0%} of capital: total {r['total_pct']:+.1f}%  maxDD {r['maxdd_pct']:.1f}%  ruined {r['ruined']}  max size {r['max_size_x']:.1f}x")
    S = p.to_numpy()
    c, pu = bs_call_put(S[:-1], S[:-1], iv.to_numpy()[:-1], 1 / 365)
    cost = (c + pu) * (1 + HAIR) + 2 * FEE * S[:-1]
    move = np.abs(np.diff(S))
    out["edge"] = dict(avg_cost_pct=float(np.mean(cost / S[:-1]) * 100), avg_move_pct=float(np.mean(move / S[:-1]) * 100),
                       win_days=float(np.mean(move > cost)), big_win_days=float(np.mean(move > 2 * cost)),
                       payback_ratio=float(move.sum() / cost.sum()))
    print("buyer edge:", out["edge"])
    print("== Monte Carlo, 2 years from today, 2,000 paths")
    out["mc"] = monte_carlo()
    with open(os.path.join(ROOT, "results", "long_straddle.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
