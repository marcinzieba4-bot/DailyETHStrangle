"""
Monthly ladder of 1-year ATM calls on 50 ETH that are never sold.

Every month sell a 1-year ATM call on 50/12 ETH for `premium` x spot. After 12 months there are always
12 calls open; each month one expires (pay 50/12 x max(S - K, 0) in cash if it ends in the money, keep the
ETH) and a new 1-year ATM call is sold on that slice. Calls still open at the end are bought back at
Black-Scholes value, with the volatility implied by the premium (26% -> 66%, 20% -> 51%).

Compared with selling one 1-year call on all 50 ETH each year (run_annual.py) and with plain holding.

  python3 run_ladder.py   -> results/ladder.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from sim.engine import bs_call_put
from run_annual import load_prices, run_path

ROOT = os.path.dirname(os.path.abspath(__file__))
N_ETH = 50.0
SLICE = N_ETH / 12
YEARS = 4
FEE = 0.0003
PREMS = [0.26, 0.20]


def implied_vol(prem):
    return brentq(lambda s: bs_call_put(1.0, 1.0, s, 1.0)[0] - prem, 0.01, 3.0)


def ladder(sale_px, sale_t, exp_px, end_px, end_t, prem, sigma):
    """One path. sale_px[k]: price at k-th monthly sale; exp_px[k]: price at its expiry (nan if after end)."""
    cash_events = []   # (time, amount)
    open_mtm = 0.0
    n_itm = n_exp = 0
    for k in range(len(sale_px)):
        K = sale_px[k]
        cash_events.append((sale_t[k], SLICE * (prem - FEE) * K))
        if np.isfinite(exp_px[k]):
            pay = SLICE * max(exp_px[k] - K, 0.0)
            cash_events.append((sale_t[k] + 365, -pay))
            n_exp += 1
            n_itm += exp_px[k] > K
        else:
            T_left = (sale_t[k] + 365 - end_t) / 365
            open_mtm += SLICE * bs_call_put(end_px, K, sigma, T_left)[0]
    cash_events.sort()
    running = np.cumsum([a for _, a in cash_events])
    cash = running[-1]
    return dict(cash=cash, open_mtm=open_mtm, diff=cash - open_mtm, min_cash=min(0.0, running.min()),
                n_itm=n_itm, n_exp=n_exp, events=cash_events, running=running.tolist())


def main():
    p = load_prices()
    px = p.to_numpy()
    idx = p.index
    out = dict(n_eth=N_ETH, years=YEARS, premiums=PREMS, sigma={str(x): implied_vol(x) for x in PREMS})

    # ---- exact last 4 years, calendar months ----
    end = idx[-1]
    start = end - pd.DateOffset(years=YEARS)
    sale_d = [start + pd.DateOffset(months=k) for k in range(12 * YEARS)]
    sale_px = np.array([p.asof(d) for d in sale_d])
    exp_d = [d + pd.DateOffset(years=1) for d in sale_d]
    exp_px = np.array([p.asof(d) if d <= end else np.nan for d in exp_d])
    t = lambda d: (d - start).days
    sale_t = np.array([t(d) for d in sale_d])
    end_px, end_t = float(p.iloc[-1]), t(end)
    L = dict(dates=[str(d.date()) for d in sale_d], sale_px=sale_px.tolist(),
             exp_dates=[str(d.date()) for d in exp_d], exp_px=[None if np.isnan(x) else float(x) for x in exp_px],
             hodl=N_ETH * end_px, start_px=float(sale_px[0]), end_px=end_px)
    yearly = [str(start.date())] + [str((start + pd.DateOffset(years=y)).date()) for y in range(1, YEARS + 1)]
    annual_S = np.array([p.asof(pd.Timestamp(d)) for d in yearly])
    for prem in PREMS:
        r = ladder(sale_px, sale_t, exp_px, end_px, end_t, prem, out["sigma"][str(prem)])
        # by year of cash flow
        yrs = []
        for y in range(YEARS):
            lo, hi = 365 * y, 365 * (y + 1)
            ev = [a for tt, a in r["events"] if lo <= tt < hi or (y == YEARS - 1 and tt == hi)]
            yrs.append(dict(premium=sum(a for a in ev if a > 0), payout=-sum(a for a in ev if a < 0)))
        _, ann = run_path(annual_S, prem)
        L[str(prem)] = dict(cash=r["cash"], open_mtm=r["open_mtm"], diff=r["diff"], min_cash=r["min_cash"], n_itm=r["n_itm"],
                            n_exp=r["n_exp"], by_year=yrs, running=r["running"], event_t=[tt for tt, _ in r["events"]],
                            annual_diff=ann["diff"], annual_min_cash=ann["min_cash"])
    out["last4"] = L

    # ---- every 4-year window since Feb 2020 ----
    span = 365 * YEARS
    offs = np.round(np.arange(12 * YEARS) * 365 / 12).astype(int)
    win = {}
    for prem in PREMS:
        sig = out["sigma"][str(prem)]
        recs = []
        for s in range(0, len(px) - span):
            st = s + offs
            et = st + 365
            e_px = np.where(et <= s + span, px[np.minimum(et, len(px) - 1)], np.nan)
            r = ladder(px[st], offs, e_px, px[s + span], span, prem, sig)
            _, ann = run_path(px[s + 365 * np.arange(YEARS + 1)], prem)
            hodl = N_ETH * px[s + span]
            recs.append(dict(start=str(idx[s].date()), s0=px[s], s_end=px[s + span], lad=r["diff"] / hodl * 100,
                             ann=ann["diff"] / hodl * 100, lad_cash=r["min_cash"] / (N_ETH * px[s]) * 100,
                             ann_cash=ann["min_cash"] / (N_ETH * px[s]) * 100, itm_share=r["n_itm"] / r["n_exp"]))
        df = pd.DataFrame(recs)
        q = lambda c, x: float(df[c].quantile(x))
        win[str(prem)] = dict(
            n=len(df), first=df.start.iloc[0], last=df.start.iloc[-1],
            lad=dict(beat=float((df.lad > 0).mean()), median=q("lad", .5), p10=q("lad", .1), p90=q("lad", .9),
                     worst=float(df.lad.min()), best=float(df.lad.max()), cash_med=q("lad_cash", .5), cash_p10=q("lad_cash", .1),
                     by_year=df.assign(y=df.start.str[:4]).groupby("y").lad.median().round(1).to_dict()),
            ann=dict(beat=float((df.ann > 0).mean()), median=q("ann", .5), p10=q("ann", .1), p90=q("ann", .9),
                     worst=float(df.ann.min()), best=float(df.ann.max()), cash_med=q("ann_cash", .5), cash_p10=q("ann_cash", .1),
                     by_year=df.assign(y=df.start.str[:4]).groupby("y").ann.median().round(1).to_dict()),
            spread_sd=float((df.lad - df.ann).std()), lad_itm_share_med=q("itm_share", .5),
            series=df[["start", "s0", "s_end", "lad", "ann"]].round(2).to_dict("list"),
        )
    out["windows"] = win
    with open(os.path.join(ROOT, "results", "ladder.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)

    print(f"Last 4 years {L['dates'][0]} -> {str(end.date())}: ETH {L['start_px']:,.0f} -> {L['end_px']:,.0f}; holding ${L['hodl']:,.0f}")
    for prem in PREMS:
        r = L[str(prem)]
        print(f"\n premium {prem:.0%} (implied vol {out['sigma'][str(prem)]:.0%})")
        for y, b in enumerate(r["by_year"]):
            print(f"   year {y+1}: premiums +${b['premium']:,.0f}  payouts -${b['payout']:,.0f}  net {b['premium']-b['payout']:+,.0f}")
        print(f"   cash collected net {r['cash']:+,.0f}; buy back {48 - r['n_exp']} open calls -${r['open_mtm']:,.0f}; "
              f"result vs holding {r['diff']:+,.0f} ({r['diff']/L['hodl']*100:+.1f}%); worst running cash {r['min_cash']:+,.0f}; "
              f"{r['n_itm']}/{r['n_exp']} expired calls ended ITM | yearly-all-at-once: {r['annual_diff']:+,.0f}, worst cash {r['annual_min_cash']:+,.0f}")
        w = win[str(prem)]
        for k, lab in (("lad", "monthly ladder"), ("ann", "yearly, all at once")):
            x = w[k]
            print(f"   all {w['n']} 4y windows, {lab:20s}: beat holding {x['beat']:.0%}  median {x['median']:+.1f}%  10th {x['p10']:+.1f}%  90th {x['p90']:+.1f}%  "
                  f"worst {x['worst']:+.1f}%  best {x['best']:+.1f}%  worst cash median {x['cash_med']:+.1f}% (10th {x['cash_p10']:+.1f}%)  by start year {x['by_year']}")
        print(f"   spread between the two (sd of difference): {w['spread_sd']:.1f} pts; ladder ITM share median {w['lad_itm_share_med']:.0%}")


if __name__ == "__main__":
    main()
