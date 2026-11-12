"""
Selling daily ETH volatility: a systematic design with delta hedging and loss recovery.

One trade per day at the 08:00 UTC settlement: sell a next-day ATM straddle (put + call), optionally with
bought wings (iron fly). Path simulated hourly on Deribit ETH-PERPETUAL bars; settles at 08:00 next day.

Inputs
  data/eth_perp_hourly.csv   hourly bars (price path, hedge fills, realised vol)
  data/daily_atm_iv.csv      real traded IV of the next-day expiry around 08:00 (Deribit history API)
  data/eth_dvol.csv          fallback: DVOL x weekday factor when no trades were found

Components (all optional, combined in `simulate`):
  maker      sell at mark instead of mark minus the measured half-spread
  iv_filter  sell only if today's ATM IV >= k x realised vol of the last 7 days (hourly, annualised)
  skip_dow   weekdays not traded (0=Mon ... 6=Sun, by the 08:00 start day)
  wings      buy put at K(1-w) and call at K(1+w): caps the daily loss at w - net premium
  hedge      hourly delta hedge with a perp, rebalanced when off by more than `band` x size; stops `stop_h`
             hours before expiry (gamma near expiry makes hourly hedging pure noise)
  recovery   after losses, extra notional = drawdown / (K x premium rate): recover the drawdown from premium over
             ~K days; capped at `rec_cap` x base; back to base at a new equity high
  lev        notional = lev x equity (compounding)
Costs: options 0.03% of underlying per leg (capped at 12.5% of price); perp 0.03% per trade.

  python3 run_daily_vol.py   -> results/daily_vol.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd
from scipy.special import ndtr

from sim.scenarios import DOW_IV

ROOT = os.path.dirname(os.path.abspath(__file__))
YEAR_H = 365 * 24
OPT_FEE = 0.0003
PERP_FEE = 0.0003


def load_days():
    h = pd.read_csv(os.path.join(ROOT, "data", "eth_perp_hourly.csv"), parse_dates=["timestamp"]).set_index("timestamp")
    close = h["close"]
    # price at hh:00 = close of the bar that started an hour earlier
    px = close.copy()
    px.index = px.index + pd.Timedelta(hours=1)
    px = px[~px.index.duplicated()].asfreq("h").ffill()
    starts = px.index[(px.index.hour == 8)]
    rows = []
    for t0 in starts:
        t1 = t0 + pd.Timedelta(hours=24)
        if t1 not in px.index:
            continue
        path = px.loc[t0:t1].to_numpy()
        if len(path) != 25:
            continue
        rows.append((t0.normalize(), path))
    days = pd.DataFrame({"date": [r[0] for r in rows]}).set_index("date")
    paths = np.stack([r[1] for r in rows])
    # realised vol over the previous 7 days from hourly returns
    lr = np.log(px).diff()
    rv7 = lr.rolling(24 * 7).std() * np.sqrt(YEAR_H)
    days["rv7"] = rv7.reindex(days.index + pd.Timedelta(hours=8)).to_numpy()
    days["dow"] = days.index.dayofweek
    iv = pd.read_csv(os.path.join(ROOT, "data", "daily_atm_iv.csv"), parse_dates=["date"]).set_index("date")
    dv = pd.read_csv(os.path.join(ROOT, "data", "eth_dvol.csv"), parse_dates=["date"]).set_index("date")["dvol"]
    days["iv_trades"] = iv["atm_iv"].reindex(days.index) / 100
    days["n_trades"] = iv["n_trades"].reindex(days.index)
    days["iv_model"] = dv.reindex(days.index).ffill().to_numpy() / 100 * DOW_IV[days["dow"].to_numpy()]
    ok = days["iv_trades"].notna() & (days["n_trades"] >= 3)
    days["iv"] = np.where(ok, days["iv_trades"], days["iv_model"])
    days["iv_src"] = np.where(ok, "trades", "model")
    days["wing_put_iv"] = iv["wing_put_iv"].reindex(days.index) / 100
    days["wing_call_iv"] = iv["wing_call_iv"].reindex(days.index) / 100
    days["spread"] = iv["spread_frac"].reindex(days.index)
    keep = days["iv"].notna() & days["rv7"].notna()
    return days[keep.to_numpy()], paths[keep.to_numpy()]


def bs(S, K, sig, T, kind):
    T = np.maximum(T, 1e-9)
    sd = sig * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sd * sd) / sd
    if kind == "call":
        return S * ndtr(d1) - K * ndtr(d1 - sd), ndtr(d1)
    return K * ndtr(-(d1 - sd)) - S * ndtr(-d1), ndtr(d1) - 1


def simulate(days, paths, lev=1.0, maker=False, iv_filter=None, skip_dow=(), wings=None, wing_skew=0.10,
             hedge=False, band=0.25, stop_h=2, recovery=False, rec_k=10, rec_cap=2.0, iv_mult=1.0, spread=None,
             start=None, end=None, cap0=100_000.0, side_by_dow=None, lev_buy=1.0, hedge_buy=None, buy_filter=None):
    sel = np.ones(len(days), bool)
    if start:
        sel &= days.index >= start
    if end:
        sel &= days.index <= end
    d, P = days[sel], paths[sel]
    eq = cap0
    hwm = cap0
    out_eq, out_ret, traded, sizes = [], [], 0, []
    worst = 0.0
    for i in range(len(d)):
        row = d.iloc[i]
        path = P[i]
        S0 = path[0]
        iv = row["iv"] * iv_mult
        if side_by_dow is not None:
            side = side_by_dow.get(int(row["dow"]), 0)
        else:
            side = 0 if row["dow"] in skip_dow else 1
        if side == 1 and iv_filter is not None:
            side = 1 if iv >= iv_filter * row["rv7"] else 0
        if side == -1 and buy_filter is not None:
            side = -1 if iv <= buy_filter * row["rv7"] else 0
        trade = side != 0
        pnl = 0.0
        if side == -1 and eq > 0:
            T = 1 / 365
            c, _ = bs(S0, S0, iv, T, "call")
            p, _ = bs(S0, S0, iv, T, "put")
            sp = (row["spread"] if np.isfinite(row["spread"]) else 0.03) if spread is None else spread
            hair = 0.0 if maker else sp
            cost = c * (1 + hair) + min(OPT_FEE * S0, 0.125 * c) + p * (1 + hair) + min(OPT_FEE * S0, 0.125 * p)
            n = lev_buy * eq / S0
            pnl = n * (abs(path[-1] - S0) - cost)
            hb = hedge if hedge_buy is None else hedge_buy
            if hb:
                hs = path[:24]
                Th = (24 - np.arange(24)) / YEAR_H
                _, dc = bs(hs, S0, iv, Th, "call")
                _, dp = bs(hs, S0, iv, Th, "put")
                targets = -(dc + dp) * n               # long straddle: hedge against its delta
                H = 0.0
                for h in range(24):
                    s_ = hs[h]
                    if h < 24 - stop_h:
                        if abs(targets[h] - H) > band * n:
                            pnl -= abs(targets[h] - H) * s_ * PERP_FEE
                            H = targets[h]
                    elif H != 0.0:
                        pnl -= abs(H) * s_ * PERP_FEE
                        H = 0.0
                    pnl += H * (path[h + 1] - s_)
            traded += 1
            sizes.append(lev_buy)
        if side == 1 and eq > 0:
            T = 1 / 365
            c, _ = bs(S0, S0, iv, T, "call")
            p, _ = bs(S0, S0, iv, T, "put")
            sp = (row["spread"] if np.isfinite(row["spread"]) else 0.03) if spread is None else spread
            hair = 0.0 if maker else sp
            fee = lambda px_: min(OPT_FEE * S0, 0.125 * px_)
            prem = c * (1 - hair) - fee(c) + p * (1 - hair) - fee(p)
            wing_cost = 0.0
            if wings:
                kp, kc = S0 * (1 - wings), S0 * (1 + wings)
                ivp = row["wing_put_iv"] * iv_mult if np.isfinite(row["wing_put_iv"]) else iv + wing_skew
                ivc = row["wing_call_iv"] * iv_mult if np.isfinite(row["wing_call_iv"]) else iv + wing_skew
                wp, _ = bs(S0, kp, ivp, T, "put")
                wc, _ = bs(S0, kc, ivc, T, "call")
                wing_cost = wp * (1 + hair) + fee(wp) + wc * (1 + hair) + fee(wc)
            net = prem - wing_cost
            rate = net / S0
            base = lev * eq
            notional = base
            if recovery and eq < hwm and rate > 0:
                notional = min(base + (hwm - eq) / (rec_k * rate), rec_cap * base)
            n = notional / S0
            ST = path[-1]
            pay = abs(ST - S0)
            if wings:
                pay -= max(kp - ST, 0) + max(ST - kc, 0)
            pnl = n * (net - pay)
            if hedge:
                hs = path[:24]
                Th = (24 - np.arange(24)) / YEAR_H
                _, dc = bs(hs, S0, iv, Th, "call")
                _, dp = bs(hs, S0, iv, Th, "put")
                book = -(dc + dp)                             # short straddle delta per ETH
                if wings:
                    _, dwp = bs(hs, kp, ivp, Th, "put")
                    _, dwc = bs(hs, kc, ivc, Th, "call")
                    book = book + dwp + dwc
                targets = -book * n
                H = 0.0
                for h in range(24):
                    s = hs[h]
                    if h < 24 - stop_h:
                        if abs(targets[h] - H) > band * n:
                            pnl -= abs(targets[h] - H) * s * PERP_FEE
                            H = targets[h]
                    elif H != 0.0:
                        pnl -= abs(H) * s * PERP_FEE
                        H = 0.0
                    pnl += H * (path[h + 1] - s)
            traded += 1
            sizes.append(notional / base)
        ret = pnl / eq if eq > 0 else 0.0
        worst = min(worst, ret)
        eq += pnl
        hwm = max(hwm, eq)
        out_eq.append(eq)
        out_ret.append(ret)
    e = pd.Series(out_eq, index=d.index)
    r = pd.Series(out_ret, index=d.index)
    years = max((e.index[-1] - e.index[0]).days / 365.25, 1e-9)
    cagr = (e.iloc[-1] / cap0) ** (1 / years) - 1 if e.iloc[-1] > 0 else -1.0
    dd = e / e.cummax() - 1
    yr = e.resample("YE").last()
    yearly = (yr / yr.shift(1).fillna(cap0) - 1) * 100
    sharpe = float(r.mean() / r.std() * np.sqrt(365)) if r.std() > 0 else 0.0
    return dict(cagr=float(cagr * 100), maxdd=float(dd.min() * 100), sharpe=sharpe, worst_day=float(worst * 100),
                final=float(e.iloc[-1] / cap0), traded=traded, days=len(d), max_size=float(max(sizes) if sizes else 0),
                yearly={int(k.year): float(v) for k, v in yearly.items()}, equity=e)


def edge_table(days, paths):
    """Average premium (at mark) vs average |move| by weekday and by IV/RV bucket."""
    S0, ST = paths[:, 0], paths[:, -1]
    iv = days["iv"].to_numpy()
    prem = 0.7979 * iv * np.sqrt(1 / 365)          # ATM straddle / S, Black-Scholes approximation
    move = np.abs(ST / S0 - 1)
    df = pd.DataFrame({"dow": days["dow"].to_numpy(), "prem": prem, "move": move,
                       "ratio": iv / days["rv7"].to_numpy(), "src": days["iv_src"].to_numpy()}, index=days.index)
    by_dow = df.groupby("dow").apply(lambda g: pd.Series({"n": len(g), "prem": g.prem.mean() * 100, "move": g.move.mean() * 100,
                                                          "edge_pct_of_prem": (1 - g.move.sum() / g.prem.sum()) * 100}))
    df["bucket"] = pd.cut(df.ratio, [0, 0.8, 1.0, 1.2, 1.5, 10], labels=["<0.8", "0.8–1.0", "1.0–1.2", "1.2–1.5", ">1.5"])
    by_ratio = df.groupby("bucket", observed=True).apply(lambda g: pd.Series({"n": len(g), "edge_pct_of_prem": (1 - g.move.sum() / g.prem.sum()) * 100}))
    by_year = df.groupby(df.index.year).apply(lambda g: pd.Series({"n": len(g), "prem": g.prem.mean() * 100, "move": g.move.mean() * 100,
                                                                   "edge_pct_of_prem": (1 - g.move.sum() / g.prem.sum()) * 100}))
    return by_dow, by_ratio, by_year


if __name__ == "__main__":
    days, paths = load_days()
    print(len(days), "days", days.index[0].date(), days.index[-1].date(), "| IV from trades:", (days.iv_src == "trades").mean())
    a, b, c = edge_table(days, paths)
    print(a.round(2), b.round(2), c.round(2), sep="\n")
