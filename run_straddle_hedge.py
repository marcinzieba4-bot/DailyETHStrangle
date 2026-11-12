"""
Monthly ladder of 1-year ATM straddles (put + call) from 1 Jan 2020, compounding, with optional delta hedge.

Same market model as run_leverage.py: 1-year IV = 0.5 x 30-day IV + 26 vol points, futures 5%/yr above spot,
sold 1.5% below value, 0.03% fee per leg, Deribit standard margin per leg (no netting between legs):
  initial     per short option: mark + max(0.15 - OTM, 0.10) x S
  maintenance per short option: mark + 0.075 x S;  perp: 1% of notional
Each leg is bought back once it has earned 95% of its premium. Hedge = short/long ETH-PERP against the book's
delta, rebalanced daily when off by more than `band` x equity; real Deribit funding; 0.05% per perp trade.

  python3 run_straddle_hedge.py   -> results/straddle_hedge.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from run_leverage import (BASIS, CAP0, FEE, HAIR, MONTH, START, load_funding, load_iv, put_delta, put_fwd, stats)
from run_puts import load_long

ROOT = os.path.dirname(os.path.abspath(__file__))


def call_fwd(S, K, sig, T):
    T = max(T, 1e-6)
    return put_fwd(S, K, sig, T) + S * (1 + BASIS) ** T - K


def call_delta(S, K, sig, T):
    T = max(T, 1e-6)
    return put_delta(S, K, sig, T) + (1 + BASIS) ** T


def simulate(p, iv1, funding, lev=1.0, hedge=0.0, band=0.02, sizeup=False, cap_mult=3.0, cash_yield=0.0,
             legs=("put", "call"), call_hedge_only_trend=False, ma_days=200, perp_cost=0.0005, take_profit=0.05):
    S, IV, FU = p.to_numpy(), iv1.to_numpy(), funding.to_numpy()
    MA = p.rolling(ma_days, min_periods=50).mean().to_numpy()
    n = len(S)
    sale_days = set(np.round(np.arange(0, n, MONTH)).astype(int))
    cash, H = CAP0, 0.0
    pos = []
    eq = np.empty(n)
    liquidated, loss_due = None, 0.0
    fund, hcost = 0.0, 0.0

    def leg_val(q, kind, s, sig, T):
        return put_fwd(s, q["K"], sig, T) if kind == "put" else call_fwd(s, q["K"], sig, T)

    for d in range(n):
        s, sig = S[d], IV[d]
        if d > 0 and H != 0.0:
            cash += H * (s - S[d - 1])
            f = -H * S[d - 1] * FU[d - 1] / 365
            cash += f
            fund += f
        if cash > 0 and cash_yield:
            cash *= 1 + cash_yield / 365
        # expiries
        keep = []
        for q in pos:
            if d - q["t0"] >= 365:
                pay = q["n_put"] * max(q["K"] - s, 0.0) + q["n_call"] * max(s - q["K"], 0.0)
                cash -= pay
                res = q["prem"] - q["bought_back"] - pay
                if res < 0:
                    loss_due += -res
            else:
                keep.append(q)
        pos = keep
        # new tranche
        if liquidated is None and d in sale_days:
            liab = sum(q["n_put"] * put_fwd(s, q["K"], sig, 1 - (d - q["t0"]) / 365) +
                       q["n_call"] * call_fwd(s, q["K"], sig, 1 - (d - q["t0"]) / 365) for q in pos)
            equity = cash - liab
            base = max(lev * equity / 12, 0.0)
            pp = put_fwd(s, s, sig, 1.0) if "put" in legs else 0.0
            cp = call_fwd(s, s, sig, 1.0) if "call" in legs else 0.0
            prem_rate = ((pp + cp) * (1 - HAIR) - FEE * s * len(legs)) / s
            notional = base
            if sizeup and loss_due > 0 and base > 0:
                notional = min(base + loss_due / prem_rate, cap_mult * base)
            loss_due = 0.0
            size = notional / s
            used = 0.0
            for q in pos:
                T = 1 - (d - q["t0"]) / 365
                used += q["n_put"] * (put_fwd(s, q["K"], sig, T) + max(0.15 * s - max(s - q["K"], 0), 0.10 * s))
                used += q["n_call"] * (call_fwd(s, q["K"], sig, T) + max(0.15 * s - max(q["K"] - s, 0), 0.10 * s))
            used += abs(H) * s * 0.02
            need = pp + cp + 0.15 * s * len(legs)
            room = max(cash + size * prem_rate * s - used, 0.0)
            size = min(size, room / need)
            if size > 0:
                q = dict(K=s, t0=d, n_put=size if "put" in legs else 0.0, n_call=size if "call" in legs else 0.0,
                         prem=size * prem_rate * s, bought_back=0.0,
                         p_put=pp * (1 - HAIR), p_call=cp * (1 - HAIR))
                pos.append(q)
                cash += q["prem"]
        # take profit per leg
        if take_profit:
            for q in pos:
                T = 1 - (d - q["t0"]) / 365
                if T <= 0.05:
                    continue
                if q["n_put"] > 0 and put_fwd(s, q["K"], sig, T) < take_profit * q["p_put"]:
                    c = q["n_put"] * (put_fwd(s, q["K"], sig, T) * (1 + HAIR) + FEE * s)
                    cash -= c
                    q["bought_back"] += c
                    q["n_put"] = 0.0
                if q["n_call"] > 0 and call_fwd(s, q["K"], sig, T) < take_profit * q["p_call"]:
                    c = q["n_call"] * (call_fwd(s, q["K"], sig, T) * (1 + HAIR) + FEE * s)
                    cash -= c
                    q["bought_back"] += c
                    q["n_call"] = 0.0
            pos = [q for q in pos if q["n_put"] > 0 or q["n_call"] > 0]
        # hedge
        if liquidated is None and (hedge > 0 or call_hedge_only_trend):
            book_put = sum(q["n_put"] * -put_delta(s, q["K"], sig, 1 - (d - q["t0"]) / 365) for q in pos)
            book_call = sum(q["n_call"] * -call_delta(s, q["K"], sig, 1 - (d - q["t0"]) / 365) for q in pos)
            if call_hedge_only_trend:
                # calls always hedged; puts hedged only below the moving average
                target = -book_call - (book_put if s < MA[d] else 0.0)
            else:
                target = -hedge * (book_put + book_call)
            liab = sum(q["n_put"] * put_fwd(s, q["K"], sig, 1 - (d - q["t0"]) / 365) +
                       q["n_call"] * call_fwd(s, q["K"], sig, 1 - (d - q["t0"]) / 365) for q in pos)
            if abs(target - H) * s > band * max(cash - liab, 1.0):
                c = abs(target - H) * s * perp_cost
                cash -= c
                hcost += c
                H = target
        # mark & maintenance
        liab, mm = 0.0, abs(H) * s * 0.01
        for q in pos:
            T = 1 - (d - q["t0"]) / 365
            vp, vc = put_fwd(s, q["K"], sig, T), call_fwd(s, q["K"], sig, T)
            liab += q["n_put"] * vp + q["n_call"] * vc
            mm += q["n_put"] * (vp + 0.075 * s) + q["n_call"] * (vc + 0.075 * s)
        if liquidated is None and pos and cash < mm:
            cash -= liab * 1.01 + abs(H) * s * perp_cost
            pos, H, liab = [], 0.0, 0.0
            liquidated = str(p.index[d].date())
        eq[d] = cash - liab
    e = pd.Series(eq, index=p.index)
    return e, liquidated, dict(funding=fund / CAP0 * 100, hedge_cost=hcost / CAP0 * 100)


def main():
    full = load_long()
    ivf = load_iv(full)
    fuf = load_funding(full.index)
    variants = {
        "Straddles 1×, no hedge": dict(lev=1.0),
        "Straddles 1×, delta-hedged daily": dict(lev=1.0, hedge=1.0),
        "Straddles 1×, delta-hedged, 10% band": dict(lev=1.0, hedge=1.0, band=0.10),
        "Straddles 1× + size-up + 4% yield, delta-hedged": dict(lev=1.0, hedge=1.0, sizeup=True, cash_yield=0.04),
        "Straddles 0.5×, delta-hedged": dict(lev=0.5, hedge=1.0),
        "Straddles 1.5×, delta-hedged": dict(lev=1.5, hedge=1.0),
        "Straddles 1× + size-up + 4% yield, calls hedged, puts hedged below MA": dict(lev=1.0, call_hedge_only_trend=True, sizeup=True, cash_yield=0.04),
        "Calls only 1×, delta-hedged": dict(lev=1.0, hedge=1.0, legs=("call",)),
    }
    out = dict(variants={}, starts={})
    p = full[full.index >= START]
    for name, kw in variants.items():
        e, liq, x = simulate(p, ivf.reindex(p.index), fuf.reindex(p.index), **kw)
        st = stats(e)
        st.update(liquidated=liq, **x)
        out["variants"][name] = st
        print(f"{name:66s} CAGR {st['cagr']:+6.1f}%  maxDD {st['maxdd']:6.1f}%  x{st['final']/CAP0:.2f}  funding {x['funding']:+.0f}%  hedge cost {x['hedge_cost']:.0f}%  liq {liq}")
        print("      ", {k: round(v, 1) for k, v in st["yearly"].items()})
    for st_ in ["2021-01-01", "2021-11-09", "2022-06-18", "2024-03-12"]:
        q = full[full.index >= st_]
        out["starts"][st_] = {}
        for name in ["Straddles 1×, no hedge", "Straddles 1×, delta-hedged daily", "Straddles 1× + size-up + 4% yield, delta-hedged"]:
            e, liq, _ = simulate(q, ivf.reindex(q.index), fuf.reindex(q.index), **variants[name])
            s_ = stats(e)
            out["starts"][st_][name] = dict(cagr=s_["cagr"], maxdd=s_["maxdd"], liq=liq)
        print(st_, {k: (round(v["cagr"], 1), round(v["maxdd"], 0), v["liq"]) for k, v in out["starts"][st_].items()})
    # realised vs implied (1-year) by year, to explain the result
    r = np.log(p).diff()
    rv = (r.groupby(p.index.year).std() * np.sqrt(365) * 100).round(0)
    iv = (ivf.reindex(p.index).groupby(p.index.year).mean() * 100).round(0)
    out["rv_iv"] = {int(y): dict(realised=float(rv[y]), implied=float(iv[y])) for y in rv.index}
    print("realised vs model 1y implied vol by year:", out["rv_iv"])
    with open(os.path.join(ROOT, "results", "straddle_hedge.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
