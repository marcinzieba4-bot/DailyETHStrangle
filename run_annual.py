"""
Yearly covered calls on 50 ETH that are never sold.

Every year on the same date: sell a 1-year ATM call on 50 ETH for `premium` x spot (USD).
At expiry the call is cash-settled: pay 50 x max(S_expiry - strike, 0). Then sell the next one.
The 50 ETH are held throughout (staking would be identical to plain holding, so it is left out).

Also: an ETH-settled version (Deribit options are inverse: premium received in ETH and kept as ETH,
payout paid in ETH), and every 4-year window since daily settlement data starts (Feb 2020).

  python3 run_annual.py   -> results/annual.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.abspath(__file__))
N_ETH = 50.0
YEARS = 4
FEE = 0.0003  # Deribit option fee, % of underlying per contract


def load_prices():
    p = pd.read_csv(os.path.join(ROOT, "data", "eth_delivery_prices.csv"), parse_dates=["date"]).set_index("date")["price"]
    return p[p.index >= "2020-02-18"]


def run_path(S, prem):
    """S: settlement prices at each roll date (YEARS+1). Returns per-year rows and totals (USD- and ETH-settled)."""
    cash, rows = 0.0, []
    eth = N_ETH                      # ETH-settled variant: ETH balance
    for i in range(len(S) - 1):
        k, s1 = S[i], S[i + 1]
        p_usd = N_ETH * (prem - FEE) * k
        pay = N_ETH * max(s1 - k, 0.0)
        cash += p_usd - pay
        eth += N_ETH * (prem - FEE) - N_ETH * max(s1 - k, 0.0) / s1
        rows.append(dict(strike=k, expiry_price=s1, premium=p_usd, payout=pay, net=p_usd - pay, cash=cash,
                         itm=bool(s1 > k), eth_balance=eth))
    hodl = N_ETH * S[-1]
    return rows, dict(final=hodl + cash, hodl=hodl, diff=cash, eth_final_value=eth * S[-1], eth_final=eth,
                      min_cash=min(0.0, min(r["cash"] for r in rows)), start_value=N_ETH * S[0])


def main():
    p = load_prices()
    out = {"n_eth": N_ETH, "years": YEARS, "premiums": [0.26, 0.20]}
    # exact last 4 years: same calendar date each year, ending at today's settlement
    end = p.index[-1]
    dates = [end - pd.DateOffset(years=YEARS - i) for i in range(YEARS + 1)]
    S = np.array([p.loc[d] for d in dates])
    out["last4"] = {"dates": [str(d.date()) for d in dates], "prices": S.tolist()}
    for prem in out["premiums"]:
        rows, tot = run_path(S, prem)
        out["last4"][str(prem)] = dict(rows=rows, total=tot)

    # every 4-year window with daily starts
    px = p.to_numpy()
    idx = p.index
    span = 365 * YEARS
    win = {}
    for prem in out["premiums"]:
        recs = []
        for s in range(0, len(px) - span):
            Sw = px[s + 365 * np.arange(YEARS + 1)]
            rows, tot = run_path(Sw, prem)
            recs.append(dict(start=str(idx[s].date()), s0=Sw[0], s_end=Sw[-1], diff_pct_start=tot["diff"] / tot["start_value"] * 100,
                             diff_pct_hodl=tot["diff"] / tot["hodl"] * 100, itm_years=sum(r["itm"] for r in rows),
                             min_cash_pct=tot["min_cash"] / tot["start_value"] * 100,
                             eth_diff_pct_hodl=(tot["eth_final_value"] - tot["hodl"]) / tot["hodl"] * 100))
        df = pd.DataFrame(recs)
        win[str(prem)] = dict(
            n=len(df), first=df.start.iloc[0], last=df.start.iloc[-1],
            beat_hodl=float((df.diff_pct_hodl > 0).mean()),
            diff_pct_hodl_median=float(df.diff_pct_hodl.median()), diff_pct_hodl_p10=float(df.diff_pct_hodl.quantile(0.1)),
            diff_pct_hodl_p90=float(df.diff_pct_hodl.quantile(0.9)), diff_pct_hodl_min=float(df.diff_pct_hodl.min()),
            diff_pct_hodl_max=float(df.diff_pct_hodl.max()),
            diff_pct_start_median=float(df.diff_pct_start.median()),
            itm_years_dist={int(k): float(v) for k, v in df.itm_years.value_counts(normalize=True).sort_index().items()},
            min_cash_pct_median=float(df.min_cash_pct.median()), min_cash_pct_p10=float(df.min_cash_pct.quantile(0.1)),
            eth_beat_hodl=float((df.eth_diff_pct_hodl > 0).mean()), eth_diff_median=float(df.eth_diff_pct_hodl.median()),
            by_start_year=df.assign(y=df.start.str[:4]).groupby("y").diff_pct_hodl.median().round(1).to_dict(),
        )
        win[str(prem)]["series"] = df[["start", "s0", "s_end", "diff_pct_hodl", "itm_years"]].round(2).to_dict("list")
    out["windows"] = win
    with open(os.path.join(ROOT, "results", "annual.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)

    print("Last 4 years:", out["last4"]["dates"], [round(x) for x in S])
    for prem in out["premiums"]:
        L = out["last4"][str(prem)]
        print(f"\n premium {prem:.0%}")
        for d, r in zip(out["last4"]["dates"][1:], L["rows"]):
            print(f"  to {d}: strike ${r['strike']:,.0f} -> ${r['expiry_price']:,.0f}  premium +${r['premium']:,.0f}  payout -${r['payout']:,.0f}"
                  f"  net {r['net']:+,.0f}  cumulative cash {r['cash']:+,.0f}  ETH-settled balance {r['eth_balance']:.2f} ETH")
        t = L["total"]
        print(f"  HODL ${t['hodl']:,.0f}  covered ${t['final']:,.0f}  diff {t['diff']:+,.0f} ({t['diff']/t['hodl']*100:+.1f}% of HODL)"
              f"  | ETH-settled: {t['eth_final']:.2f} ETH = ${t['eth_final_value']:,.0f} ({(t['eth_final_value']/t['hodl']-1)*100:+.1f}%)  worst cash {t['min_cash']:+,.0f}")
        w = win[str(prem)]
        print(f"  all 4y windows ({w['n']} starts {w['first']}..{w['last']}): beat HODL {w['beat_hodl']:.0%}, median {w['diff_pct_hodl_median']:+.1f}% of HODL "
              f"(10th {w['diff_pct_hodl_p10']:+.1f}, 90th {w['diff_pct_hodl_p90']:+.1f}, min {w['diff_pct_hodl_min']:+.1f}, max {w['diff_pct_hodl_max']:+.1f}); "
              f"ITM years {w['itm_years_dist']}; worst cash median {w['min_cash_pct_median']:+.1f}% of start value; ETH-settled beat {w['eth_beat_hodl']:.0%}; by start year {w['by_start_year']}")


if __name__ == "__main__":
    main()
