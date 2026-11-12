"""
Exit through weekly covered calls: every week sell a weekly call on `lot` ETH (default 2 ETH).
If it expires in the money, short `lot` ETH-PERP at expiry (== those ETH are sold at the strike).
If not, keep the premium and nothing is hedged. No puts. Done when all 62.45 ETH are hedged.

  python3 run_weekly.py   -> results/weekly.json
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim.engine import Carry, Costs, Strategy, bs_call_put, run  # noqa: E402
from sim import data as D  # noqa: E402
from sim import scenarios as SC  # noqa: E402
from run_accumulate import mc_ou  # noqa: E402
from run_range import bridge, median_paths  # noqa: E402

OUT = os.path.join(D.ROOT, "results")
WEEK = 7 / 365
N = 10000
WEEKS = 104
DAYS = WEEKS * 7
# weekly IV relative to DVOL by strike, from the live 20NOV26 chain (ATM 53.5, +5% 52.2, +10% 53.9 vs DVOL 56.2)
IV_F = {0.0: 0.95, 0.05: 0.93, 0.10: 0.96}


def strategies(Q0):
    f = lambda lot: lot / Q0
    return {
        "2 ETH/wk, ATM": (Strategy("W0", f(2), 1.0, sell_puts=False, mode="exit", call_moneyness=0.0), 0.0),
        "2 ETH/wk, +5%": (Strategy("W5", f(2), 1.0, sell_puts=False, mode="exit", call_moneyness=0.05), 0.05),
        "2 ETH/wk, +10%": (Strategy("W10", f(2), 1.0, sell_puts=False, mode="exit", call_moneyness=0.10), 0.10),
        "2 ETH/wk, +5%, strike ≥ $2,000": (Strategy("W5f", f(2), 1.0, sell_puts=False, mode="exit", call_moneyness=0.05,
                                                     call_strike_floor_rel=1.0), 0.05),
        "2 ETH/wk, ATM, strike ≥ $2,000": (Strategy("W0f", f(2), 1.0, sell_puts=False, mode="exit", call_moneyness=0.0,
                                                     call_strike_floor_rel=1.0), 0.0),
        "5 ETH/wk, +5%": (Strategy("W5x", f(5), 1.0, sell_puts=False, mode="exit", call_moneyness=0.05), 0.05),
        "5 ETH/wk, +5%, strike ≥ $2,000": (Strategy("W5xf", f(5), 1.0, sell_puts=False, mode="exit", call_moneyness=0.05,
                                                     call_strike_floor_rel=1.0), 0.05),
        "Sell 2 ETH/wk, no options": (Strategy("DCA", f(2), 1.0, sell_puts=False, mode="exit", dca=True), 0.0),
    }


def summarize(o, h, P0, Q0, weeks_1y=52):
    pos = Q0 * P0
    rel = o["rel"] / o["bench"] * 100
    d = (o["rel"] - h["rel"]) / o["bench"] * 100
    cl = o["cleared"]
    pn = o["pnl"]
    sold = o["hedge_eth"]
    avg_k = np.where(sold > 0, o["hedge_val"] / np.maximum(sold, 1e-9) / o["bench"] * Q0 * (1 - 0.0005), np.nan)  # avg strike / start price
    q = lambda x, p: float(np.nanpercentile(x, p))
    return dict(
        mean=float(rel.mean()), median=q(rel, 50), p5=q(rel, 5), p95=q(rel, 95), beat=float(np.mean(rel > 0)),
        vs_hodl_mean=float(d.mean()), vs_hodl_med=q(d, 50), vs_hodl_p5=q(d, 5), beat_hodl=float(np.mean(d > 0)),
        out_1y=float(np.mean(cl & (o["tau"] <= weeks_1y))), out_2y=float(cl.mean()),
        weeks_med=q(np.where(cl, o["tau"], np.nan), 50) if cl.any() else None,
        eth_left_med=q(np.where(cl, 0, o["E_T"]), 50), eth_left_p95=q(np.where(cl, 0, o["E_T"]), 95),
        sold_med=q(sold, 50), avg_strike_rel_med=(q(avg_k, 50) - 1) * 100 if np.isfinite(avg_k).any() else None,
        allin_rel_med=q(rel, 50),
        premium=float(pn["premium"].mean() / o["bench"].mean() * 100), edge=float((pn["premium"] - pn["payout"]).mean() / o["bench"].mean() * 100),
        staking=float(pn["staking"].mean() / o["bench"].mean() * 100), funding=float(pn["funding"].mean() / o["bench"].mean() * 100),
        premium_usd=float((pn["premium"] / o["bench"] * pos).mean()), edge_usd=float(((pn["premium"] - pn["payout"]) / o["bench"] * pos).mean()),
    )


def main():
    live = D.load_live()
    hist = D.load_history()
    P0 = float(live["eth_index"])
    Q0 = 50 * float(live["wsteth_ratio"])
    start_dow = pd.Timestamp(live["ts_utc"]).dayofweek
    costs = Costs()
    ST = strategies(Q0)
    hodl = Strategy("H", hold=True, mode="income")
    pools = SC.z_pools(hist)
    t = np.arange(DAYS)
    scen = {
        "No view (random walk)": dict(gen=lambda s: SC.mc_paths(pools, P0, start_dow, np.full(DAYS, 56.0), np.zeros(DAYS), N, DAYS, seed=s),
                                      vol=56, fund=0.015, desc="Zero drift, today's vol."),
        "Range around $2,000": dict(gen=lambda s: mc_ou(pools, P0, start_dow, 56, 2000.0, 60, N, DAYS, seed=s), vol=56, fund=0.015,
                                    desc="Pulled back to $2,000 (half-life 60 days)."),
        "Bull: doubles in 2 years": dict(gen=lambda s: median_paths(pools, P0, start_dow, 56, np.full(DAYS, np.log(2) / DAYS), N, DAYS, s),
                                         vol=56, fund=0.06, desc="Typical path rises steadily to ~$4,000."),
        "Rally +40% in 3 months, then flat": dict(gen=lambda s: median_paths(pools, P0, start_dow, 60, np.where(t < 90, np.log(1.4) / 90, 0.0), N, DAYS, s),
                                                  vol=60, fund=0.08, desc="Typical path to ~$2,800 by February, then sideways."),
        "Dips to ~$1,200, back at $2,000": dict(gen=lambda s: bridge(median_paths(pools, P0, start_dow, 60, np.where(t < 180, np.log(0.6) / 180, 0.0), N, DAYS, s), 2000.0, 180),
                                                vol=60, fund=0.01, desc="Falls 40% over 6 months; back at $2,000 after 2 years."),
        "2022 replay: −60%, no recovery": dict(gen=lambda s: median_paths(pools, P0, start_dow, 70, np.where(t < 180, np.log(0.4) / 180, 0.0), N, DAYS, s),
                                               vol=70, fund=0.0, desc="Typical path slides to ~$800 in 6 months and stays."),
    }
    R = dict(P0=P0, Q0=Q0, weeks=WEEKS, mc={}, hist={})
    for i, (name, sc) in enumerate(scen.items()):
        Sd = sc["gen"](1300 + i)
        S = Sd[:, ::7][:, :WEEKS + 1]
        carry = Carry(stake_apr=float(live["steth_apr"]), fund_apr=sc["fund"])
        h = run(S, np.full(S[:, :-1].shape, 0.5), Q0, hodl, costs, carry, dt=WEEK)
        row = dict(desc=sc["desc"], eth_end_med=float(np.median(S[:, -1])), eth_1y_med=float(np.median(S[:, 52])))
        hold_rel = h["rel"] / h["bench"] * 100
        row["hodl"] = dict(mean=float(hold_rel.mean()), median=float(np.median(hold_rel)), p5=float(np.percentile(hold_rel, 5)))
        for k, (st, m) in ST.items():
            iv = np.full(S[:, :-1].shape, sc["vol"] * IV_F[m] / 100)
            row[k] = summarize(run(S, iv, Q0, st, costs, carry, dt=WEEK), h, P0, Q0)
        R["mc"][name] = row
        print(name, "done", flush=True)

    # history: every daily start, weekly steps
    px, dv, fu, sk = (hist[c].to_numpy() for c in ("price", "dvol", "fund_apr", "stake_apr"))
    for weeks in (52, 104):
        starts = np.arange(0, len(px) - 7 * weeks)
        idx = starts[:, None] + 7 * np.arange(weeks + 1)[None, :]
        S = px[idx]
        h = run(S, np.full(S[:, :-1].shape, 0.5), Q0, hodl, costs, Carry(), fund=fu[idx[:, :-1]], stake=sk[idx[:, :-1]], dt=WEEK)
        hold_rel = h["rel"] / h["bench"] * 100
        blk = dict(n=int(len(starts)), start=str(hist.index[starts[0]].date()), end=str(hist.index[starts[-1]].date()),
                   hodl=dict(mean=float(hold_rel.mean()), median=float(np.median(hold_rel)), p5=float(np.percentile(hold_rel, 5))))
        ts = dict(date=[str(hist.index[s].date()) for s in starts], price=S[:, 0].round(2).tolist(), hodl=hold_rel.round(2).tolist())
        for k, (st, m) in ST.items():
            iv = dv[idx[:, :-1]] / 100 * IV_F[m]
            o = run(S, iv, Q0, st, costs, Carry(), fund=fu[idx[:, :-1]], stake=sk[idx[:, :-1]], dt=WEEK)
            blk[k] = summarize(o, h, P0, Q0)
            ts[k] = (o["rel"] / o["bench"] * 100).round(2).tolist()
            ts[k + "|left"] = np.where(o["cleared"], 0, o["E_T"]).round(1).tolist()
        R["hist"][str(weeks)] = blk
        if weeks == 104:
            R["hist_ts"] = ts
        print("history", weeks, "done", flush=True)

    # historical weekly call edge (premium vs payout per strike)
    edge = {}
    starts = np.arange(0, len(px) - 7)
    for m, f in IV_F.items():
        S0, S1 = px[starts], px[starts + 7]
        K = S0 * (1 + m)
        c, _ = bs_call_put(S0, K, dv[starts] / 100 * f, WEEK)
        net = c * (1 - costs.spread_haircut) - np.minimum(costs.opt_fee * S0, costs.opt_fee_cap * c)
        pay = np.maximum(S1 - K, 0)
        edge[str(m)] = dict(premium_pct=float(np.mean(c / S0) * 100), net_pct=float(np.mean(net / S0) * 100),
                            payout_pct=float(np.mean(pay / S0) * 100), itm=float(np.mean(S1 > K)),
                            edge_pct_of_premium=float((1 - pay.sum() / net.sum()) * 100))
    R["call_edge"] = edge
    with open(os.path.join(OUT, "weekly.json"), "w") as fh:
        json.dump(R, fh, indent=1)
    show(R)


def show(R):
    cols = ["mean", "median", "p5", "beat", "vs_hodl_mean", "vs_hodl_med", "beat_hodl", "out_1y", "out_2y", "weeks_med",
            "eth_left_med", "sold_med", "avg_strike_rel_med", "premium_usd", "edge_usd"]
    print("call edge:", json.dumps(R["call_edge"], indent=0))
    for name, row in list(R["mc"].items()) + [("HIST " + k, v) for k, v in R["hist"].items()]:
        print(f"\n== {name}" + (f" | ETH 1y median {row['eth_1y_med']:.0f}, 2y median {row['eth_end_med']:.0f}" if "eth_end_med" in row else "")
              + f" | HODL mean {row['hodl']['mean']:+.1f}% med {row['hodl']['median']:+.1f}% p5 {row['hodl']['p5']:+.1f}%")
        df = pd.DataFrame({k: v for k, v in row.items() if isinstance(v, dict) and "out_2y" in v}).T
        print(df[cols].round(1).to_string())


if __name__ == "__main__":
    main()
