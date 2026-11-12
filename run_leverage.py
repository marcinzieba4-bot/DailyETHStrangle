"""
Continuous run from 1 Jan 2020 of the monthly ladder of 1-year ATM ETH puts, compounding, with leverage.

Every 30.4 days a new 1-year put is sold on notional = leverage x current equity / 12 (USD-sized).
Optional: rule B size-up after a losing tranche (extra notional whose premium equals the loss, total capped).
Optional: put spread = also buy a 1-year put at 50% of the strike (caps the loss per tranche).

Pricing ("market"): 1-year IV = 0.5 x 30-day IV + 26 vol points (calibrated so DVOL 56 -> 54, today's quote);
30-day IV = Deribit DVOL, or 1.11 x 30-day realised vol before DVOL starts (Mar 2021). Forward 5%/yr above spot.
Sold at mark - 1.5%, bought at mark + 1.5% (wing priced 10 vol points higher for skew), 0.03% fee per leg.

Margin (Deribit standard-margin style, USD collateral):
  initial     per short put: mark + max(0.15 - OTM, 0.10) x S      (new tranches are cut to fit)
  maintenance per short put: mark + 0.075 x S;  per spread: min(that, strike width)
Daily: if collateral < maintenance, everything is bought back (+1% slippage) and the strategy stops.

  python3 run_leverage.py   -> results/leverage.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
from scipy.special import ndtr

from run_puts import load_long

ROOT = os.path.dirname(os.path.abspath(__file__))
START = "2020-01-01"
CAP0 = 100_000.0
BASIS = 0.05
HAIR = 0.015
FEE = 0.0003
WING = 0.5
WING_SKEW = 0.10
MONTH = 365 / 12


def put_fwd(S, K, sig, T):
    """Undiscounted Black-76 put on the forward F = S (1+BASIS)^T."""
    T = np.maximum(T, 1e-6)
    F = S * (1 + BASIS) ** T
    sd = sig * np.sqrt(T)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    return K * ndtr(-(d1 - sd)) - F * ndtr(-d1)


def load_iv(p):
    r = np.log(p).diff()
    rv = r.rolling(30, min_periods=10).std() * np.sqrt(365) * 100
    dv = pd.read_csv(os.path.join(ROOT, "data", "eth_dvol.csv"), parse_dates=["date"]).set_index("date")["dvol"]
    iv30 = dv.reindex(p.index).combine_first(rv * 1.11).ffill().bfill()
    return (0.5 * iv30 + 26) / 100


def simulate(p, iv1, lev, spread=False, sizeup=False, cap_mult=3.0, fixed_prem=None, wing=WING, trend=False, bear_lev=0.0,
             cash_yield=0.0, take_profit=0.05):
    S = p.to_numpy()
    IV = iv1.to_numpy()
    MA = p.rolling(200, min_periods=50).mean().to_numpy()
    n = len(S)
    sale_days = set(np.round(np.arange(0, n, MONTH)).astype(int))
    cash = CAP0
    pos = []   # dicts: size, K, Kw, t0, prem_rate
    eq = np.empty(n)
    liquidated = None
    peak_notional = 0.0
    loss_due = 0.0
    for d in range(n):
        s, sig = S[d], IV[d]
        if cash > 0 and cash_yield:
            cash *= 1 + cash_yield / 365
        # expiries
        keep = []
        for q in pos:
            if d - q["t0"] >= 365:
                pay = q["size"] * max(q["K"] - s, 0.0)
                if spread:
                    pay -= q["size"] * max(q["Kw"] - s, 0.0)
                cash -= pay
                res = q["prem"] - pay
                if res < 0:
                    loss_due += -res
            else:
                keep.append(q)
        pos = keep
        if liquidated is None and d in sale_days:
            # mark existing book
            liab = sum(q["size"] * (put_fwd(s, q["K"], sig, 1 - (d - q["t0"]) / 365)
                                    - (put_fwd(s, q["Kw"], sig + WING_SKEW, 1 - (d - q["t0"]) / 365) if spread else 0.0)) for q in pos)
            equity = cash - liab
            L = lev if (not trend or s >= MA[d]) else bear_lev
            base = max(L * equity / 12, 0.0)
            short = put_fwd(s, s, sig, 1.0) if fixed_prem is None else fixed_prem * s
            wing_px = put_fwd(s, wing * s, sig + WING_SKEW, 1.0) if spread else 0.0
            prem_rate = (short * (1 - HAIR) - FEE * s - (wing_px * (1 + HAIR) + FEE * s if spread else 0.0)) / s
            notional = base
            if sizeup and loss_due > 0 and base > 0:
                notional = min(base + loss_due / prem_rate, cap_mult * base)
            loss_due = 0.0
            size = notional / s
            # initial margin check
            def im(q_size, K, T):
                m = q_size * (put_fwd(s, K, sig, T) + max(0.15 * s - max(s - K, 0), 0.10 * s))
                return m
            used = sum(im(q["size"], q["K"], 1 - (d - q["t0"]) / 365) for q in pos)
            if spread:
                used = sum(min(im(q["size"], q["K"], 1 - (d - q["t0"]) / 365), q["size"] * (q["K"] - q["Kw"])) for q in pos)
                need_per_eth = min(put_fwd(s, s, sig, 1.0) + 0.15 * s, s - wing * s)
            else:
                need_per_eth = put_fwd(s, s, sig, 1.0) + 0.15 * s
            room = max(cash + size * prem_rate * s - used, 0.0)
            size = min(size, room / need_per_eth) if need_per_eth > 0 else size
            if size > 0:
                pos.append(dict(size=size, K=s, Kw=wing * s, t0=d, prem=size * prem_rate * s, prem_eth=prem_rate * s))
                cash += size * prem_rate * s
            peak_notional = max(peak_notional, sum(q["size"] * q["K"] for q in pos) / max(equity, 1e-9))
        # take profit: buy back puts that have earned 95% of their premium (frees margin)
        if take_profit and pos:
            keep = []
            for q in pos:
                T = 1 - (d - q["t0"]) / 365
                net = put_fwd(s, q["K"], sig, T) - (put_fwd(s, q["Kw"], sig + WING_SKEW, T) if spread else 0.0)
                if net < take_profit * q["prem_eth"] and T > 0.05:
                    cash -= q["size"] * (net * (1 + HAIR) + FEE * s * (2 if spread else 1))
                else:
                    keep.append(q)
            pos = keep
        # daily mark and maintenance
        liab, mm = 0.0, 0.0
        for q in pos:
            T = 1 - (d - q["t0"]) / 365
            m = put_fwd(s, q["K"], sig, T)
            net = m - (put_fwd(s, q["Kw"], sig + WING_SKEW, T) if spread else 0.0)
            liab += q["size"] * net
            req = q["size"] * (m + 0.075 * s)
            if spread:
                req = min(req, q["size"] * (q["K"] - q["Kw"]))
            mm += req
        if liquidated is None and pos and cash < mm:
            cash -= liab * 1.01
            pos = []
            liquidated = str(p.index[d].date())
            liab = 0.0
        eq[d] = cash - liab
    e = pd.Series(eq, index=p.index)
    return e, liquidated, peak_notional


def stats(e):
    yr = e.resample("YE").last()
    first = e.iloc[0]
    yearly = (yr / yr.shift(1).fillna(first) - 1) * 100
    yearly.index = yearly.index.year
    dd = e / e.cummax() - 1
    years = (e.index[-1] - e.index[0]).days / 365.25
    cagr = (e.iloc[-1] / e.iloc[0]) ** (1 / years) - 1 if e.iloc[-1] > 0 else -1.0
    trough = dd.idxmin()
    peak = e[:trough].idxmax()
    rec = e[trough:][e[trough:] >= e[peak]]
    mo = e.resample("ME").last()
    monthly = mo / mo.shift(1).fillna(first) - 1
    return dict(yearly={int(k): float(v) for k, v in yearly.items()}, cagr=float(cagr * 100), maxdd=float(dd.min() * 100),
                dd_peak=str(peak.date()), dd_trough=str(trough.date()), dd_recovered=str(rec.index[0].date()) if len(rec) else None,
                final=float(e.iloc[-1]), worst_month=float(monthly.min() * 100), worst_month_at=str(monthly.idxmin().date()),
                mar=float(cagr * 100 / abs(dd.min() * 100)) if dd.min() < 0 else None)


def main():
    p = load_long()
    p = p[p.index >= START]
    iv1 = load_iv(load_long()).reindex(p.index)
    variants = {
        "Puts 1× (cash-secured)": dict(lev=1.0),
        "Puts 1× + size-up (cap 3×)": dict(lev=1.0, sizeup=True),
        "Puts 1× + 4% yield on collateral": dict(lev=1.0, cash_yield=0.04),
        "Puts 2×": dict(lev=2.0),
        "Puts 3×": dict(lev=3.0),
        "Puts 1×, only above 200-day MA": dict(lev=1.0, trend=True),
        "Puts 2×, only above 200-day MA": dict(lev=2.0, trend=True),
        "Puts 3×, only above 200-day MA": dict(lev=3.0, trend=True),
        "Put spread (−50% wing) 2×": dict(lev=2.0, spread=True, wing=0.5),
        "Put spread (−30% wing) 2×": dict(lev=2.0, spread=True, wing=0.7),
        "Put spread (−30% wing) 3×": dict(lev=3.0, spread=True, wing=0.7),
        "Put spread (−30% wing) 3×, above 200-day MA": dict(lev=3.0, spread=True, wing=0.7, trend=True),
        "Puts 2×, above MA, + size-up (cap 3×)": dict(lev=2.0, trend=True, sizeup=True),
        "Puts 1.5×": dict(lev=1.5),
        "Puts 1× + size-up + 4% yield": dict(lev=1.0, sizeup=True, cash_yield=0.04),
        "Puts 1.5× + size-up + 4% yield": dict(lev=1.5, sizeup=True, cash_yield=0.04),
        "Puts 2× + size-up + 4% yield": dict(lev=2.0, sizeup=True, cash_yield=0.04),
    }
    out = dict(start=str(p.index[0].date()), end=str(p.index[-1].date()), cap0=CAP0, variants={}, series={})
    for name, kw in variants.items():
        e, liq, pk = simulate(p, iv1, **kw)
        st = stats(e)
        st.update(liquidated=liq, peak_notional_x=pk)
        out["variants"][name] = st
        out["series"][name] = (e.resample("W").last() / CAP0 * 100).round(2).tolist()
        print(f"{name:34s} CAGR {st['cagr']:+6.1f}%  maxDD {st['maxdd']:6.1f}% ({st['dd_peak']}→{st['dd_trough']}, recovered {st['dd_recovered']})  "
              f"final x{st['final']/CAP0:.2f}  worst month {st['worst_month']:+.1f}% ({st['worst_month_at']})  peak notional {pk:.1f}×  liquidated {liq}")
        print("      yearly:", {k: round(v, 1) for k, v in st["yearly"].items()})
    hodl = p / p.iloc[0] * CAP0
    cash = pd.Series(CAP0 * 1.04 ** (np.arange(len(p)) / 365), index=p.index)
    for name, e in [("Hold ETH", hodl), ("Cash at 4%", cash)]:
        st = stats(e)
        out["variants"][name] = st
        out["series"][name] = (e.resample("W").last() / CAP0 * 100).round(2).tolist()
        print(f"{name:34s} CAGR {st['cagr']:+6.1f}%  maxDD {st['maxdd']:6.1f}%  final x{st['final']/CAP0:.2f}  yearly {({k: round(v, 1) for k, v in st['yearly'].items()})}")
    out["series_dates"] = [str(d.date()) for d in p.resample("W").last().index]
    out["series_price"] = p.resample("W").last().round(2).tolist()
    # premium the model paid each year (1-year ATM put, % of spot, at the bid)
    prem = pd.Series([(put_fwd(s, s, v, 1.0) * (1 - HAIR) / s - FEE) * 100 for s, v in zip(p.to_numpy(), iv1.to_numpy())], index=p.index)
    out["premium_by_year"] = prem.resample("YE").mean().round(1).rename(lambda d: d.year).to_dict()
    out["premium_now"] = float(prem.iloc[-1])
    print("model 1y ATM put premium by year:", out["premium_by_year"], "now", round(out["premium_now"], 1))
    with open(os.path.join(ROOT, "results", "leverage.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
