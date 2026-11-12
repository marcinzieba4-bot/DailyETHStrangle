"""
No ETH held. Every month sell a 1-year ATM put on 1/12 of the capital (50 ETH x start price, in USD) for `prem` x spot. At expiry pay
size x max(K - S, 0) in cash and sell a new 1-year ATM put at the current price.
After a losing tranche (loss = payout - premium > 0) the replacement is sized up just enough that
premium covers the loss:

  plain : new size = base
  A     : new size = max(base, loss / (prem x S_now))       -> the whole new premium equals the last loss
  B     : new size = base + loss / (prem x S_now)           -> base tranche plus an extra one whose premium equals the loss

Open puts are marked at Black-Scholes value with the volatility implied by the premium; the lowest point of
cash minus that value is the money needed to stay in. "Collateral" is what fully cash-secured puts would lock
(sum of open size x strike).

  python3 run_puts.py   -> results/puts.json
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
FEE = 0.0003
PREMS = [0.22, 0.18, 0.26]
RULES = ["plain", "A", "B"]


def put_val(S, K, sig, T):
    return bs_call_put(S, K, sig, max(T, 1e-6))[1]


def implied_vol(prem):
    return brentq(lambda s: put_val(1.0, 1.0, s, 1.0) - prem, 0.01, 3.0)


def ladder(S_m, prem, sig, rule):
    """Tranches sized in USD: the base tranche is 1/12 of the starting capital (50 ETH x start price)."""
    n = len(S_m) - 1
    cap = N_ETH * S_m[0]
    base_k = np.zeros(n)
    size, K, prem_in = np.zeros(n), np.zeros(n), np.zeros(n)
    cash = 0.0
    equity, collat, rows = [], [], []
    for k in range(n + 1):
        S = S_m[k]
        loss = 0.0
        if k >= 12:
            j = k - 12
            pay = size[j] * max(K[j] - S, 0.0)
            cash -= pay
            res = prem_in[j] - pay
            rows.append(dict(month=k, strike=K[j], price=S, size=size[j], premium=prem_in[j], payout=pay, result=res))
            loss = max(-res, 0.0)
        if k < n:
            base = cap / 12 / S
            base_k[k] = base
            s = base
            if loss > 0 and rule == "A":
                s = max(base, loss / (prem * S))
            elif loss > 0 and rule == "B":
                s = base + loss / (prem * S)
            size[k], K[k] = s, S
            prem_in[k] = s * (prem - FEE) * S
            cash += prem_in[k]
        open_j = [j for j in range(max(0, k - 11), min(k + 1, n)) if j + 12 > k]
        liab = sum(size[j] * put_val(S, K[j], sig, (j + 12 - k) / 12) for j in open_j)
        equity.append(cash - liab)
        collat.append(sum(size[j] * K[j] for j in open_j))
    rel = size / base_k
    return dict(final=equity[-1], min_equity=min(0.0, min(equity)), max_size=float(rel.max()),
                max_collat=max(collat), rows=rows, equity=equity, sizes=rel.tolist(), base_k=base_k,
                n_loss=sum(r["result"] < 0 for r in rows), n_exp=len(rows))


def load_long():
    """Bitstamp daily closes Aug 2017 - Feb 2020 spliced onto Deribit 08:00 settlements."""
    b = pd.read_csv(os.path.join(ROOT, "data", "eth_bitstamp_2017_2020.csv"), parse_dates=["date"]).set_index("date")["price"]
    p = load_prices()
    return pd.concat([b[b.index < p.index[0]], p])


def windows(p, prems, sigmas, keep_series):
    px, idx = p.to_numpy(), p.index
    span = 365 * YEARS
    offs = np.round(np.arange(12 * YEARS + 1) * 365 / 12).astype(int)
    win = {}
    for prem in prems:
        sig = sigmas[str(prem)]
        for rule in RULES:
            recs = []
            for s in range(0, len(px) - span):
                S_w = px[s + offs]
                r = ladder(S_w, prem, sig, rule)
                bv = N_ETH * S_w[0]
                recs.append(dict(start=str(idx[s].date()), s0=S_w[0], s_end=S_w[-1], pnl=r["final"] / bv * 100,
                                 dd=r["min_equity"] / bv * 100, max_size=r["max_size"], collat=r["max_collat"] / bv,
                                 hodl=(S_w[-1] / S_w[0] - 1) * 100))
            df = pd.DataFrame(recs)
            q = lambda c, x: float(df[c].quantile(x))
            yr = df.assign(y=df.start.str[:4]).groupby("y")
            win[f"{prem}|{rule}"] = dict(
                n=len(df), first=df.start.iloc[0], last=df.start.iloc[-1],
                profit=float((df.pnl > 0).mean()), median=q("pnl", .5), mean=float(df.pnl.mean()),
                p10=q("pnl", .1), p90=q("pnl", .9), worst=float(df.pnl.min()), best=float(df.pnl.max()),
                worst_start=df.loc[df.pnl.idxmin(), "start"],
                beat_hodl=float((df.pnl > df.hodl).mean()), hodl_median=q("hodl", .5), hodl_profit=float((df.hodl > 0).mean()),
                beat_cash=float((df.pnl > (1.04 ** YEARS - 1) * 100).mean()),
                dd_median=q("dd", .5), dd_p10=q("dd", .1), dd_worst=float(df.dd.min()),
                size_median=q("max_size", .5), size_p90=q("max_size", .9), size_max=float(df.max_size.max()),
                collat_median=q("collat", .5), collat_p90=q("collat", .9), collat_max=float(df.collat.max()),
                by_year=yr.pnl.median().round(1).to_dict(),
                profit_by_year=yr.pnl.apply(lambda x: float((x > 0).mean())).round(2).to_dict(),
                size_by_year=yr.max_size.median().round(1).to_dict(),
                dd_by_year=yr.dd.median().round(0).to_dict(),
                series=df[["start", "s0", "s_end", "pnl", "dd", "max_size", "hodl"]].round(2).to_dict("list") if (keep_series and prem == 0.22) else None,
            )
    return win


def main():
    p = load_prices()
    out = dict(n_eth=N_ETH, years=YEARS, prems=PREMS, sigma={str(x): implied_vol(x) for x in PREMS})
    end = p.index[-1]
    start = end - pd.DateOffset(years=YEARS)
    dates = [start + pd.DateOffset(months=k) for k in range(12 * YEARS + 1)]
    S_m = np.array([p.asof(d) for d in dates])
    L = dict(dates=[str(d.date()) for d in dates], prices=S_m.tolist(), start_value=N_ETH * S_m[0],
             hodl_change=N_ETH * (S_m[-1] - S_m[0]))
    for prem in PREMS:
        for rule in RULES:
            r = ladder(S_m, prem, out["sigma"][str(prem)], rule)
            L[f"{prem}|{rule}"] = dict(final=r["final"], min_equity=r["min_equity"], max_size=r["max_size"], max_collat=r["max_collat"],
                                      n_loss=r["n_loss"], n_exp=r["n_exp"], equity=[round(e) for e in r["equity"]],
                                      tranches=[dict(month=x["month"], strike=round(x["strike"]), price=round(x["price"]),
                                                     move=round((x["price"] / x["strike"] - 1) * 100, 1),
                                                     size=round(r["sizes"][x["month"] - 12], 2), eth=round(x["size"], 2),
                                                     result=round(x["result"])) for x in r["rows"]])
    out["last4"] = L
    win = windows(p, PREMS, out["sigma"], False)
    out["windows_long"] = windows(load_long(), PREMS, out["sigma"], True)
    out["windows"] = win
    with open(os.path.join(ROOT, "results", "puts.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)

    print(f"Last 4 years {L['dates'][0]} -> {L['dates'][-1]}: ETH {S_m[0]:,.0f} -> {S_m[-1]:,.0f}; notional ${L['start_value']:,.0f}; holding 50 ETH {L['hodl_change']:+,.0f}")
    for prem in PREMS:
        print(f"\n== put premium {prem:.0%} (implied vol {out['sigma'][str(prem)]:.0%})")
        for rule in RULES:
            r = L[f"{prem}|{rule}"]
            w = out["windows_long"][f"{prem}|{rule}"]
            print(f"  {rule:5s} last4: P&L {r['final']:+,.0f}  worst equity {r['min_equity']:+,.0f}  max size {r['max_size']:.1f}x  "
                  f"max collateral ${r['max_collat']:,.0f}  losing {r['n_loss']}/{r['n_exp']}")
            print(f"        windows: profit {w['profit']:.0%}  beat holding {w['beat_hodl']:.0%} (holding profitable {w['hodl_profit']:.0%}, median {w['hodl_median']:+.0f}%)  "
                  f"median {w['median']:+.1f}%  10th {w['p10']:+.1f}%  worst {w['worst']:+.1f}% ({w['worst_start']})  best {w['best']:+.1f}%  | "
                  f"cash needed median {-w['dd_median']:.0f}% 10th {-w['dd_p10']:.0f}% worst {-w['dd_worst']:.0f}%  | size median {w['size_median']:.1f}x p90 {w['size_p90']:.1f}x max {w['size_max']:.1f}x  | "
                  f"collateral median {w['collat_median']:.1f}x p90 {w['collat_p90']:.1f}x | by start year {w['by_year']} | profit by year {w['profit_by_year']} | size by year {w['size_by_year']}")
            w2 = win[f"{prem}|{rule}"]
            print(f"        2020+ only: profit {w2['profit']:.0%} median {w2['median']:+.1f}% worst {w2['worst']:+.1f}%   | beats 4% cash {w['beat_cash']:.0%}")
    print("\nRule B, 22%: expired tranches")
    for x in L["0.22|B"]["tranches"]:
        print(f"   m{x['month']:2d} K {x['strike']:5d} -> {x['price']:5d} ({x['move']:+6.1f}%) size {x['size']:.2f}x result {x['result']:+,}")


if __name__ == "__main__":
    main()
