"""
Detailed write-up of the best weekend strategy and the Saturday-only vs Saturday+Sunday question.

Best version (highest Sharpe, smallest drawdown among the tested set):
  Saturday 08:00 UTC  SELL ATM straddle expiring Sunday 08:00, 1x account; recovery: while below the high add
                      drawdown / (8 x premium rate) notional, cap 1.5x; delta-hedge once if ETH moves 3% from the
                      strike (hourly check), hold to 1 hour before expiry.
  Sunday   08:00 UTC  BUY ATM straddle expiring Monday 08:00, 1x account, no hedge, no recovery.
Account starts at $125,411 (50 wstETH today) on 27 Mar 2021, real Deribit daily-option IVs, hourly ETH-PERP path.

  python3 run_best_weekend.py   -> results/best_weekend.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from run_daily_vol import load_days, simulate
from run_daily_vol_select import bootstrap

ROOT = os.path.dirname(os.path.abspath(__file__))
CAP = 125_411.0
IS = dict(start="2021-03-24", end="2023-12-31")
OOS = dict(start="2024-01-01", end="2026-12-31")
GENTLE = dict(recovery=True, rec_k=8, rec_cap=1.5)
AGGR = dict(recovery=True, rec_k=2, rec_cap=3.0)
H3 = dict(hedge_trigger=0.03, trigger_mode="static")
SAT = dict(spread=0.015, side_by_dow={5: 1})
CAL = dict(spread=0.015, side_by_dow={5: 1, 6: -1})
BEST = {**CAL, **GENTLE, **H3}


def clean(r):
    return {k: v for k, v in r.items() if k not in ("equity", "trades")}


def three(days, paths, cfg):
    return {lab: clean(simulate(days, paths, **cfg, **per, cap0=CAP)) for lab, per in (("IS", IS), ("OOS", OOS), ("full", {}))}


def main():
    days, paths = load_days()
    out = {}
    # ---------- 1. Saturday only vs Saturday + Sunday ----------
    comp = {}
    for setting, kw in (("Plain", {}), ("Gentle recovery + 3% hedge", {**GENTLE, **H3}), ("Aggressive recovery (2 Sat, cap 3×) + 3% hedge", {**AGGR, **H3})):
        for legs, base in (("Saturday only", SAT), ("Saturday + Sunday", CAL)):
            cfg = {**base, **kw}
            r = three(days, paths, cfg)
            b = bootstrap(days, paths, cfg)
            comp[f"{legs} | {setting}"] = dict(legs=legs, setting=setting, **r, boot=b)
            print(f"{legs:18s} {setting:46s} full {r['full']['cagr']:+6.1f}%/{r['full']['maxdd']:6.1f}% Sh {r['full']['sharpe']:.2f} | "
                  f"OOS {r['OOS']['cagr']:+6.1f}%/{r['OOS']['maxdd']:6.1f}% | boot median {b['cagr_median']:+.1f}% lost {b['loss_2y']:.0%} DD5 {b['dd_p5']:.1f}%  "
                  f"yrs {({k: round(v) for k, v in r['full']['yearly'].items()})}")
    out["compare"] = comp

    # ---------- 2. best version, every weekend ----------
    r = simulate(days, paths, **BEST, cap0=CAP, log=True)
    tr = pd.DataFrame(r["trades"])
    tr["date"] = pd.to_datetime(tr["date"])
    sat = tr[tr.side == "sell"].set_index("date")
    sun = tr[tr.side == "buy"].set_index("date")
    weeks = []
    for d0, s_ in sat.iterrows():
        d1 = d0 + pd.Timedelta(days=1)
        u = sun.loc[d1] if d1 in sun.index else None
        sat_pnl = s_.pnl_usd
        sun_pnl = float(u.pnl_usd) if u is not None else 0.0
        eq_after = (u.eq_before + u.pnl_usd) if u is not None else (s_.eq_before + s_.pnl_usd)
        weeks.append(dict(
            sat=str(d0.date()), eth=float(s_.S0), sat_iv=float(s_.iv * 100), size=float(s_.size_x), notional=float(s_.notional),
            sat_prem=float(s_.premium_usd), sat_move=float(s_.signed_move * 100), sat_max=float(s_.max_intraday * 100),
            trig=None if pd.isna(s_.trig_hour) else int(s_.trig_hour), trig_dir=s_.trig_dir if isinstance(s_.trig_dir, str) else None,
            hedge_eth=float(s_.hedge_eth), hedge_pnl=float(s_.hedge_pnl), opt_pnl=float(s_.opt_pnl), sat_pnl=float(sat_pnl),
            sun_iv=float(u.iv * 100) if u is not None else None, sun_cost=float(u.premium_usd) if u is not None else None,
            sun_move=float(u.signed_move * 100) if u is not None else None, sun_pnl=sun_pnl,
            week_pnl=float(sat_pnl + sun_pnl), week_pct=float((sat_pnl + sun_pnl) / s_.eq_before * 100), equity=float(eq_after),
            in_rec=bool(s_.size_x > 1.001)))
    W = pd.DataFrame(weeks)
    W["sat_dt"] = pd.to_datetime(W.sat)
    e = r["equity"]
    dd = e / e.cummax() - 1
    trough = dd.idxmin()
    peak = e[:trough].idxmax()
    rec = e[trough:][e[trough:] >= e[peak]]
    wk_ret = W.week_pct / 100
    streak = longest = 0
    for x in W.week_pnl:
        streak = streak + 1 if x < 0 else 0
        longest = max(longest, streak)
    trig = W[W.trig.notna()]
    stats = dict(
        start=str(e.index[0].date()), end=str(e.index[-1].date()), weekends=int(len(W)), cap=CAP, final=float(e.iloc[-1]),
        total_pct=float((e.iloc[-1] / CAP - 1) * 100), cagr=float(r["cagr"]), maxdd=float(r["maxdd"]),
        dd_peak=str(peak.date()), dd_trough=str(trough.date()), dd_recovered=str(rec.index[0].date()) if len(rec) else None,
        sharpe_weekly=float(wk_ret.mean() / wk_ret.std() * np.sqrt(52)), win_weekends=float((W.week_pnl > 0).mean()),
        avg_win=float(W.week_pct[W.week_pct > 0].mean()), avg_loss=float(W.week_pct[W.week_pct < 0].mean()),
        best=W.loc[W.week_pct.idxmax(), ["sat", "week_pct", "week_pnl"]].to_dict(), worst=W.loc[W.week_pct.idxmin(), ["sat", "week_pct", "week_pnl"]].to_dict(),
        longest_losing=int(longest), sat_total=float(W.sat_pnl.sum()), sun_total=float(W.sun_pnl.sum()),
        sat_prem_total=float(W.sat_prem.sum()), sun_cost_total=float(W.sun_cost.sum()),
        hedge_total=float(W.hedge_pnl.sum()), sat_win=float((W.sat_pnl > 0).mean()), sun_win=float((W.sun_pnl > 0).mean()),
        triggered=int(len(trig)), triggered_share=float(len(trig) / len(W)), trig_down=int((trig.trig_dir == "down").sum()),
        trig_up=int((trig.trig_dir == "up").sum()), trig_opt=float(trig.opt_pnl.sum()), trig_hedge=float(trig.hedge_pnl.sum()),
        trig_helped=float((trig.hedge_pnl > 0).mean()) if len(trig) else 0.0, trig_median_hour=float(trig.trig.median()) if len(trig) else None,
        rec_share=float(W.in_rec.mean()), max_size=float(W["size"].max()),
        weekend_pct=dict(p5=float(W.week_pct.quantile(.05)), p25=float(W.week_pct.quantile(.25)), p50=float(W.week_pct.median()),
                         p75=float(W.week_pct.quantile(.75)), p95=float(W.week_pct.quantile(.95))),
    )
    yr = []
    for y, g in W.groupby(W.sat_dt.dt.year):
        yr.append(dict(year=int(y), ret=float(r["yearly"][int(y)]), weekends=int(len(g)), win=float((g.week_pnl > 0).mean()),
                       sat=float(g.sat_pnl.sum()), sun=float(g.sun_pnl.sum()), hedge=float(g.hedge_pnl.sum()), trig=int(g.trig.notna().sum()),
                       rec=float(g.in_rec.mean()), end=float(g.equity.iloc[-1])))
    mo = e.resample("ME").last()
    mret = (mo / mo.shift(1).fillna(CAP) - 1) * 100
    monthly = {int(y): {int(m): float(v) for m, v in g.items()} for y, g in mret.groupby(mret.index.year)
               for g in [g.groupby(g.index.month).sum()]}
    # without the hedge, same weeks (to show the hedge's effect)
    r_nohedge = simulate(days, paths, **{**CAL, **GENTLE}, cap0=CAP, log=True)
    tn = pd.DataFrame(r_nohedge["trades"])
    tn = tn[tn.side == "sell"].set_index("date")
    W["sat_pnl_nohedge_pct"] = [float(tn.loc[d_, "pnl_pct"] * 100) if d_ in tn.index else None for d_ in W.sat]
    W["sat_pnl_pct"] = W.sat_pnl / (W.equity - W.week_pnl) * 100
    stats["nohedge"] = clean(r_nohedge)
    # worked example: the triggered Saturday with the biggest move
    ex_date = trig.loc[trig.sat_max.idxmax(), "sat"]
    i = list(days.index.strftime("%Y-%m-%d")).index(ex_date)
    ex_path = paths[i].tolist()
    exw = W[W.sat == ex_date].iloc[0].to_dict()
    exn = tn.loc[ex_date].to_dict()
    example = dict(date=ex_date, path=ex_path, strike=ex_path[0], iv=exw["sat_iv"], size=exw["size"], notional=exw["notional"],
                   eth=float(sat.loc[pd.Timestamp(ex_date), "eth"]), premium=exw["sat_prem"], trig_hour=exw["trig"], trig_dir=exw["trig_dir"],
                   trig_price=float(sat.loc[pd.Timestamp(ex_date), "trig_price"]), hedge_eth=exw["hedge_eth"],
                   payout=float(sat.loc[pd.Timestamp(ex_date), "payout_usd"]), opt_pnl=exw["opt_pnl"], hedge_pnl=exw["hedge_pnl"],
                   hedge_cost=float(sat.loc[pd.Timestamp(ex_date), "hedge_cost"]), sat_pnl=exw["sat_pnl"],
                   eq_before=float(sat.loc[pd.Timestamp(ex_date), "eq_before"]), nohedge_pct=float(exn["pnl_pct"] * 100))
    # a typical non-triggered Saturday for contrast
    calm = W[W.trig.isna()].iloc[len(W[W.trig.isna()]) // 2]
    out.update(stats=stats, yearly=yr, monthly=monthly, example=example, calm_example=calm.drop("sat_dt").to_dict(),
               weeks=W.drop(columns=["sat_dt"]).to_dict("records"),
               series=(e.resample("W").last()).round(0).tolist(), dd_series=(dd.resample("W").min() * 100).round(2).tolist(),
               dates=[str(x.date()) for x in e.resample("W").last().index],
               hist=np.histogram(np.clip(W.week_pct, -12, 12), bins=np.arange(-12, 12.5, 1))[0].tolist(),
               aggr=dict(full=clean(simulate(days, paths, **{**CAL, **AGGR, **H3}, cap0=CAP))))
    print("\nBEST:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in stats.items() if not isinstance(v, dict)})
    print("weekend pct quantiles", stats["weekend_pct"], "best", stats["best"], "worst", stats["worst"])
    for y in yr:
        print(f"  {y['year']}: {y['ret']:+.1f}%  weekends {y['weekends']} won {y['win']:.0%}  Sat ${y['sat']:+,.0f}  Sun ${y['sun']:+,.0f}  hedge ${y['hedge']:+,.0f} ({y['trig']} triggers)  end ${y['end']:,.0f}")
    print("example:", {k: (round(v, 2) if isinstance(v, float) else v) for k, v in example.items() if k != "path"})
    with open(os.path.join(ROOT, "results", "best_weekend.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
