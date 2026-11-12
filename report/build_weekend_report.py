"""
Standalone report for the chosen weekend strategy only.

  Saturday 08:00 UTC  sell ATM straddle (expires Sun 08:00), 1x account, gentle recovery (8 Saturdays, cap 1.5x),
                      one-time ETH-PERP delta hedge when ETH is >= 3% from the strike, closed Sun 07:00
  Sunday   08:00 UTC  buy ATM straddle (expires Mon 08:00), 1x account
Start: $125,411 on 27 Mar 2021. Real Deribit daily-option IVs, hourly ETH-PERP path.

  python3 report/build_weekend_report.py   -> results/weekend_strategy.html (+ results/weekend_strategy.json)
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from run_daily_vol import OPT_FEE, bs, load_days, simulate  # noqa: E402
from run_daily_vol_select import bootstrap  # noqa: E402

CAP = 125_411.0
CFG = dict(spread=0.015, side_by_dow={5: 1, 6: -1}, recovery=True, rec_k=8, rec_cap=1.5, hedge_trigger=0.03, trigger_mode="static")
SPREAD = 0.015


def main():
    days, paths = load_days()
    r = simulate(days, paths, **CFG, cap0=CAP, log=True)
    tr = pd.DataFrame(r["trades"])
    idx = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(days.index)}
    sat = tr[tr.side == "sell"].set_index("date")
    sun = tr[tr.side == "buy"].set_index("date")
    weeks = []
    for d0, s in sat.iterrows():
        d1 = str((pd.Timestamp(d0) + pd.Timedelta(days=1)).date())
        u = sun.loc[d1] if d1 in sun.index else None
        S0, iv = s.S0, s.iv
        c, _ = bs(S0, S0, iv, 1 / 365, "call")
        p, _ = bs(S0, S0, iv, 1 / 365, "put")
        hwm = s.eq_before / (1 - s.dd_before) if s.dd_before < 1 else s.eq_before
        gap = hwm - s.eq_before
        extra_raw = gap / (8 * s.prem_rate) if gap > 0 else 0.0
        w = dict(
            sat=d0, sun=d1, eq0=s.eq_before, hwm=hwm, gap=gap, rate=s.prem_rate, iv=iv * 100, S0=S0, ST=s.ST,
            call=float(c), put=float(p), haircut=float((c + p) * SPREAD), fee=float(min(OPT_FEE * S0, 0.125 * c) + min(OPT_FEE * S0, 0.125 * p)),
            extra_raw=extra_raw, capped=bool(s.eq_before + extra_raw > 1.5 * s.eq_before + 1e-6), notional=s.notional, size=s.size_x, eth=s.eth,
            prem=s.premium_usd, payout=s.payout_usd, opt=s.opt_pnl, move=s.signed_move * 100, maxmove=s.max_intraday * 100,
            trig=None if pd.isna(s.trig_hour) else int(s.trig_hour), dir=s.trig_dir if isinstance(s.trig_dir, str) else None,
            tprice=None if pd.isna(s.trig_price) else float(s.trig_price), heth=float(s.hedge_eth),
            hdelta=float(s.hedge_eth / s.eth) if s.eth else 0.0, hpnl=float(s.hedge_pnl), hcost=float(s.hedge_cost), satpnl=s.pnl_usd,
            eq1=s.eq_before + s.pnl_usd, path_sat=[round(x, 2) for x in paths[idx[d0]]],
        )
        if u is not None:
            c2, _ = bs(u.S0, u.S0, u.iv, 1 / 365, "call")
            p2, _ = bs(u.S0, u.S0, u.iv, 1 / 365, "put")
            w.update(sun_eq0=u.eq_before, sun_iv=u.iv * 100, sun_S0=u.S0, sun_ST=u.ST, sun_call=float(c2), sun_put=float(p2),
                     sun_notional=u.notional, sun_eth=u.eth, sun_cost=u.premium_usd, sun_payout=u.payout_usd, sun_move=u.signed_move * 100,
                     sunpnl=u.pnl_usd, eq2=u.eq_before + u.pnl_usd, path_sun=[round(x, 2) for x in paths[idx[d1]]])
        else:
            w.update(sunpnl=0.0, eq2=w["eq1"], path_sun=None)
        w["wk"] = w["satpnl"] + w["sunpnl"]
        w["wkpct"] = w["wk"] / w["eq0"] * 100
        weeks.append({k: (float(v) if isinstance(v, (np.floating, np.integer)) else v) for k, v in w.items()})
    W = pd.DataFrame(weeks)
    e = r["equity"]
    dd = e / e.cummax() - 1
    trough = dd.idxmin()
    peak = e[:trough].idxmax()
    rec = e[trough:][e[trough:] >= e[peak]]
    streak = longest = 0
    for x in W.wk:
        streak = streak + 1 if x < 0 else 0
        longest = max(longest, streak)
    trig = W[W.trig.notna()]
    wr = W.wkpct / 100
    nohedge = simulate(days, paths, **{**CFG, "hedge_trigger": None}, cap0=CAP)
    sat_only = simulate(days, paths, **{**CFG, "side_by_dow": {5: 1}}, cap0=CAP)
    norec = simulate(days, paths, **{**CFG, "recovery": False}, cap0=CAP)
    sunrec = simulate(days, paths, **CFG, recovery_buy=True, rec_k_buy=8, rec_cap_buy=1.5, cap0=CAP)
    boot = bootstrap(days, paths, CFG)
    fills = {f"{sp:.3f}": dict(cagr=(x := simulate(days, paths, **{**CFG, "spread": sp}, cap0=CAP))["cagr"], maxdd=x["maxdd"]) for sp in (0.0, 0.03, 0.05)}
    sp_sat = float(days.loc[days.dow == 5, "spread"].median())
    sp_sun = float(days.loc[days.dow == 6, "spread"].median())
    stats = dict(
        start=W.sat.iloc[0], end=W.sun.iloc[-1], weekends=int(len(W)), cap=CAP, final=float(e.iloc[-1]), total=float((e.iloc[-1] / CAP - 1) * 100),
        cagr=float(r["cagr"]), maxdd=float(r["maxdd"]), dd_peak=str(peak.date()), dd_trough=str(trough.date()),
        dd_rec=str(rec.index[0].date()) if len(rec) else None, sharpe=float(wr.mean() / wr.std() * np.sqrt(52)),
        win=float((W.wk > 0).mean()), sat_win=float((W.satpnl > 0).mean()), sun_win=float((W.sunpnl > 0).mean()),
        avg_win=float(W.wkpct[W.wkpct > 0].mean()), avg_loss=float(W.wkpct[W.wkpct < 0].mean()), longest=int(longest),
        sat_total=float(W.satpnl.sum()), sun_total=float(W.sunpnl.sum()), hedge_total=float(W.hpnl.sum()),
        prem_total=float(W.prem.sum()), payout_total=float(W.payout.sum()), sun_cost_total=float(W.sun_cost.sum()), sun_pay_total=float(W.sun_payout.sum()),
        triggers=int(len(trig)), trig_down=int((trig.dir == "down").sum()), trig_up=int((trig.dir == "up").sum()),
        trig_helped=float((trig.hpnl > 0).mean()), trig_hour=float(trig.trig.median()),
        trig_opt=float(trig.opt.sum()), trig_hedge=float(trig.hpnl.sum()),
        rec_share=float((W["size"] > 1.001).mean()), cap_share=float((W["size"] > 1.499).mean()),
        sat_rate=float(W.rate.mean() * 100), sat_move=float(W.move.abs().mean()), sun_rate=float((W.sun_cost / W.sun_notional).mean() * 100),
        sun_move=float(W.sun_move.abs().mean()),
        nohedge=dict(cagr=nohedge["cagr"], maxdd=nohedge["maxdd"], worst=nohedge["worst_day"]),
        sat_only=dict(cagr=sat_only["cagr"], maxdd=sat_only["maxdd"]), norec=dict(cagr=norec["cagr"], maxdd=norec["maxdd"]),
        sunrec=dict(cagr=sunrec["cagr"], maxdd=sunrec["maxdd"]),
        worst_day=float(r["worst_day"]), boot=boot, fills=fills, spread_sat=sp_sat, spread_sun=sp_sun,
    )
    yearly = []
    for y, g in W.groupby(W.sat.str[:4]):
        yearly.append(dict(year=int(y), ret=float(r["yearly"][int(y)]), n=int(len(g)), win=float((g.wk > 0).mean()), sat=float(g.satpnl.sum()),
                           sun=float(g.sunpnl.sum()), hedge=float(g.hpnl.sum()), trig=int(g.trig.notna().sum()), rec=float((g["size"] > 1.001).mean()),
                           end=float(g.eq2.iloc[-1]), sat_rate=float(g.rate.mean() * 100), sat_move=float(g.move.abs().mean()),
                           sun_rate=float((g.sun_cost / g.sun_notional).mean() * 100), sun_move=float(g.sun_move.abs().mean())))
    mo = e.resample("ME").last()
    mret = (mo / mo.shift(1).fillna(CAP) - 1) * 100
    monthly = {str(y): {str(m): float(v) for m, v in g.groupby(g.index.month).sum().items()} for y, g in mret.groupby(mret.index.year)}
    ew = e.resample("W").last()
    data = dict(cfg={k: (str(v) if isinstance(v, dict) else v) for k, v in CFG.items()}, stats=stats, yearly=yearly, monthly=monthly, weeks=weeks,
                series=dict(dates=[str(x.date()) for x in ew.index], eq=[round(v, 0) for v in ew], dd=[round(v * 100, 2) for v in (ew / ew.cummax() - 1)]))
    with open(os.path.join(ROOT, "results", "weekend_strategy.json"), "w") as fh:
        json.dump(data, fh, default=float)
    tpl = open(os.path.join(ROOT, "report", "weekend_template.html")).read()
    html = tpl.replace("__DATA__", json.dumps(data, separators=(",", ":"), default=float))
    out = os.path.join(ROOT, "results", "weekend_strategy.html")
    open(out, "w").write(html)
    print("wrote", out, f"{len(html)/1024:.0f} KB", "| final", round(stats["final"]), "cagr", round(stats["cagr"], 1), "dd", round(stats["maxdd"], 1),
          "| no-hedge", round(nohedge["cagr"], 1), "| sat-only", round(sat_only["cagr"], 1), "| no-rec", round(norec["cagr"], 1), round(norec["maxdd"], 1))


if __name__ == "__main__":
    main()
