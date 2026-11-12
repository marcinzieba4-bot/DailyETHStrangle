"""
No ETH held. Every month sell a 1-year ATM straddle (put + call) on 1/12 of 50 ETH; premium = `prem` x spot
(both legs together). When a tranche expires it is settled in cash (pay size x |S - K|) and replaced
by a new 1-year ATM straddle. Recovery sizing after a losing tranche (loss = payout - premium > 0):

  plain : new size = base
  A     : new size = max(base, loss / (0.5 x prem x S_now))   -> half of the new premium equals the last loss
  B     : new size = base + loss / (0.5 x prem x S_now)        -> base tranche plus an extra one whose half-premium covers the loss

Open straddles are marked at Black-Scholes value with the volatility implied by the premium; the
lowest point of cash minus that liability is the money you would need to stay in the trade.

  python3 run_straddle.py   -> results/straddle.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
from scipy.optimize import brentq

from sim.engine import bs_call_put
from run_annual import load_prices

ROOT = os.path.dirname(os.path.abspath(__file__))
N_ETH = 50.0
BASE = N_ETH / 12
YEARS = 4
FEE = 0.0006           # 0.03% per leg
PREMS = [0.44, 0.38, 0.50]
RULES = ["plain", "A", "B"]


def straddle(S, K, sig, T):
    c, p = bs_call_put(S, K, sig, max(T, 1e-6))
    return c + p


def implied_vol(prem):
    return brentq(lambda s: straddle(1.0, 1.0, s, 1.0) - prem, 0.01, 3.0)


def ladder(S_m, prem, sig, rule):
    """S_m: prices at the 12*YEARS+1 monthly points (last = end). Returns totals and monthly equity."""
    n = len(S_m) - 1
    size = np.zeros(n)
    K = np.zeros(n)
    prem_in = np.zeros(n)
    cash = 0.0
    equity, rows = [], []
    for k in range(n + 1):
        S = S_m[k]
        loss = 0.0
        if k >= 12:                       # tranche k-12 expires now
            j = k - 12
            pay = size[j] * abs(S - K[j])
            cash -= pay
            res = prem_in[j] - pay
            rows.append(dict(month=k, strike=K[j], price=S, size=size[j], premium=prem_in[j], payout=pay, result=res))
            loss = max(-res, 0.0)
        if k < n:                         # sell the next tranche
            s = BASE
            if loss > 0 and rule == "A":
                s = max(BASE, loss / (0.5 * prem * S))
            elif loss > 0 and rule == "B":
                s = BASE + loss / (0.5 * prem * S)
            size[k], K[k] = s, S
            prem_in[k] = s * (prem - FEE) * S
            cash += prem_in[k]
        # mark open tranches
        liab = sum(size[j] * straddle(S, K[j], sig, (j + 12 - k) / 12) for j in range(max(0, k - 11), min(k + 1, n)) if j + 12 > k)
        equity.append(cash - liab)
    final = equity[-1]
    return dict(final=final, min_equity=min(0.0, min(equity)), max_size=float(size.max() / BASE), rows=rows,
                equity=equity, sizes=(size / BASE).tolist(), n_loss=sum(r["result"] < 0 for r in rows), n_exp=len(rows))


def main():
    p = load_prices()
    px, idx = p.to_numpy(), p.index
    out = dict(n_eth=N_ETH, years=YEARS, prems=PREMS, sigma={str(x): implied_vol(x) for x in PREMS})

    end = idx[-1]
    start = end - pd.DateOffset(years=YEARS)
    dates = [start + pd.DateOffset(months=k) for k in range(12 * YEARS + 1)]
    S_m = np.array([p.asof(d) for d in dates])
    L = dict(dates=[str(d.date()) for d in dates], prices=S_m.tolist(), start_value=N_ETH * S_m[0],
             hodl_change=N_ETH * (S_m[-1] - S_m[0]))
    for prem in PREMS:
        for rule in RULES:
            r = ladder(S_m, prem, out["sigma"][str(prem)], rule)
            by_year = []
            for y in range(YEARS):
                rr = [x for x in r["rows"] if 12 * (y + 1) <= x["month"] < 12 * (y + 2)]
                by_year.append(dict(n=len(rr), premium=sum(x["premium"] for x in rr), payout=sum(x["payout"] for x in rr),
                                    result=sum(x["result"] for x in rr), max_size=max([x["size"] / BASE for x in rr], default=0)))
            L[f"{prem}|{rule}"] = dict(final=r["final"], min_equity=r["min_equity"], max_size=r["max_size"], by_year=by_year,
                                      equity=[round(e, 0) for e in r["equity"]], sizes=[round(s, 2) for s in r["sizes"]],
                                      n_loss=r["n_loss"], n_exp=r["n_exp"],
                                      tranches=[dict(month=x["month"], strike=round(x["strike"]), price=round(x["price"]),
                                                     move=round((x["price"] / x["strike"] - 1) * 100, 1), size=round(x["size"] / BASE, 2),
                                                     result=round(x["result"])) for x in r["rows"]])
    out["last4"] = L

    span = 365 * YEARS
    offs = np.round(np.arange(12 * YEARS + 1) * 365 / 12).astype(int)
    win = {}
    for prem in PREMS:
        sig = out["sigma"][str(prem)]
        for rule in RULES:
            recs = []
            for s in range(0, len(px) - span):
                S_w = px[s + offs]
                r = ladder(S_w, prem, sig, rule)
                base_val = N_ETH * S_w[0]
                recs.append(dict(start=str(idx[s].date()), s0=S_w[0], s_end=S_w[-1], pnl=r["final"] / base_val * 100,
                                 dd=r["min_equity"] / base_val * 100, max_size=r["max_size"],
                                 hodl=(S_w[-1] / S_w[0] - 1) * 100))
            df = pd.DataFrame(recs)
            q = lambda c, x: float(df[c].quantile(x))
            win[f"{prem}|{rule}"] = dict(
                n=len(df), profit=float((df.pnl > 0).mean()), median=q("pnl", .5), mean=float(df.pnl.mean()),
                p10=q("pnl", .1), p90=q("pnl", .9), worst=float(df.pnl.min()), best=float(df.pnl.max()),
                dd_median=q("dd", .5), dd_p10=q("dd", .1), dd_worst=float(df.dd.min()),
                size_median=q("max_size", .5), size_p90=q("max_size", .9), size_max=float(df.max_size.max()),
                by_year=df.assign(y=df.start.str[:4]).groupby("y").pnl.median().round(1).to_dict(),
                worst_start=df.loc[df.pnl.idxmin(), "start"],
                series=df[["start", "s0", "s_end", "pnl", "dd", "max_size"]].round(2).to_dict("list") if prem == 0.44 else None,
            )
    out["windows"] = win
    with open(os.path.join(ROOT, "results", "straddle.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)

    print(f"Last 4 years {L['dates'][0]} -> {L['dates'][-1]}: ETH {S_m[0]:,.0f} -> {S_m[-1]:,.0f}; base notional ${L['start_value']:,.0f}; "
          f"holding 50 ETH would have made {L['hodl_change']:+,.0f}")
    for prem in PREMS:
        print(f"\n== combined premium {prem:.0%} (implied vol {out['sigma'][str(prem)]:.0%})")
        for rule in RULES:
            r = L[f"{prem}|{rule}"]
            yrs = "  ".join(f"Y{y+2}: {b['result']:+,.0f} (max size {b['max_size']:.1f}x)" for y, b in enumerate(r["by_year"][:3]))
            print(f"  {rule:5s} final P&L {r['final']:+,.0f}  worst equity {r['min_equity']:+,.0f}  max size {r['max_size']:.1f}x  "
                  f"losing tranches {r['n_loss']}/{r['n_exp']}  | {yrs}")
            w = win[f"{prem}|{rule}"]
            print(f"        all {w['n']} windows: profit {w['profit']:.0%}  median {w['median']:+.1f}%  mean {w['mean']:+.1f}%  10th {w['p10']:+.1f}%  "
                  f"worst {w['worst']:+.1f}% ({w['worst_start']})  best {w['best']:+.1f}%  | worst equity median {w['dd_median']:+.1f}% 10th {w['dd_p10']:+.1f}% "
                  f"worst {w['dd_worst']:+.1f}%  | max size median {w['size_median']:.1f}x p90 {w['size_p90']:.1f}x max {w['size_max']:.1f}x | by start year {w['by_year']}")
    t = L["0.44|A"]["tranches"]
    print("\nRule A, 44%: tranches that expired (move, size, result):")
    for x in t:
        print(f"   m{x['month']:2d} K {x['strike']:5d} -> {x['price']:5d} ({x['move']:+6.1f}%) size {x['size']:.2f}x result {x['result']:+,}")


if __name__ == "__main__":
    main()
