"""
Detailed study of (A) selling the Saturday daily straddle with loss-recovery sizing and (B) buying the Monday
daily straddle (Mon 08:00 -> Tue 08:00) with loss-recovery sizing. Real Deribit daily-option IVs, 2021-03..2026-11.

Recovery sizing (applied while the account is below its high, reset at a new high):
  sell: extra notional = drawdown / (K x premium received, % of notional)   -> premium of ~K trades wins it back
  buy : extra notional = drawdown / (K x premium paid, % of notional)       -> ~K wins of one premium win it back
  total size capped at `cap` x base.

  python3 run_sat_mon.py   -> results/sat_mon.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from run_daily_vol import bs, load_days, simulate
from run_daily_vol_select import bootstrap

ROOT = os.path.dirname(os.path.abspath(__file__))
IS = dict(start="2021-03-24", end="2023-12-31")
OOS = dict(start="2024-01-01", end="2026-12-31")
EXEC = dict(spread=0.015)
CAP_USER = 125_411.0
DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def clean(r):
    return {k: v for k, v in r.items() if k not in ("equity", "trades")}


def three(days, paths, cfg):
    return {lab: clean(simulate(days, paths, **cfg, **per)) for lab, per in (("IS", IS), ("OOS", OOS), ("full", {}))}


def drawdowns(e, top=3):
    """Largest peak-to-trough episodes: start, trough, recovery, depth, weeks."""
    out, peak_v, peak_t, in_dd, trough_v, trough_t = [], e.iloc[0], e.index[0], False, None, None
    for t, v in e.items():
        if v >= peak_v:
            if in_dd:
                out.append(dict(start=str(peak_t.date()), trough=str(trough_t.date()), recovered=str(t.date()),
                                depth=float((trough_v / peak_v - 1) * 100), weeks=float((t - peak_t).days / 7)))
                in_dd = False
            peak_v, peak_t = v, t
        else:
            if not in_dd or v < trough_v:
                trough_v, trough_t = v, t
            in_dd = True
    if in_dd:
        out.append(dict(start=str(peak_t.date()), trough=str(trough_t.date()), recovered=None,
                        depth=float((trough_v / peak_v - 1) * 100), weeks=float((e.index[-1] - peak_t).days / 7)))
    return sorted(out, key=lambda x: x["depth"])[:top]


def detail(days, paths, cfg, label):
    r = simulate(days, paths, **cfg, log=True)
    tr = pd.DataFrame(r["trades"])
    tr["date"] = pd.to_datetime(tr["date"])
    tr["year"] = tr.date.dt.year
    e = r["equity"]
    yearly = []
    for y, g in tr.groupby("year"):
        yearly.append(dict(year=int(y), ret=float(r["yearly"][int(y)]), trades=int(len(g)), win=float((g.pnl_pct > 0).mean()),
                           prem=float(g.prem_rate.mean() * 100), move=float(g.move.mean() * 100), in_rec=float((g.size_x > 1.001).mean()),
                           size_avg=float(g.size_x.mean()), size_max=float(g.size_x.max()), worst=float(g.pnl_pct.min() * 100),
                           usd=float(r["yearly"][int(y)] / 100 * CAP_USER)))
    active = tr.size_x > 1.001
    worst = tr.nsmallest(8, "pnl_pct")[["date", "iv", "prem_rate", "move", "size_x", "pnl_pct"]]
    best = tr.nlargest(5, "pnl_pct")[["date", "iv", "prem_rate", "move", "size_x", "pnl_pct"]]
    to_rows = lambda df: [dict(date=str(x.date.date()), iv=float(x.iv * 100), prem=float(x.prem_rate * 100), move=float(x.move * 100),
                               size=float(x.size_x), pnl=float(x.pnl_pct * 100)) for x in df.itertuples()]
    q = tr.pnl_pct * 100
    d = dict(label=label, cfg={k: (dict(v) if isinstance(v, dict) else v) for k, v in cfg.items()}, summary=clean(r),
             yearly=yearly, drawdowns=drawdowns(e), worst=to_rows(worst), best=to_rows(best),
             trades=int(len(tr)), win=float((tr.pnl_pct > 0).mean()), avg_win=float(tr.pnl_pct[tr.pnl_pct > 0].mean() * 100),
             avg_loss=float(tr.pnl_pct[tr.pnl_pct < 0].mean() * 100),
             pnl_pct=dict(p1=float(q.quantile(.01)), p5=float(q.quantile(.05)), p25=float(q.quantile(.25)), p50=float(q.median()),
                          p75=float(q.quantile(.75)), p95=float(q.quantile(.95)), p99=float(q.quantile(.99))),
             rec_share=float(active.mean()), rec_size_avg=float(tr.size_x[active].mean()) if active.any() else 1.0,
             rec_size_max=float(tr.size_x.max()), prem_avg=float(tr.prem_rate.mean() * 100), move_avg=float(tr.move.mean() * 100),
             final_usd=float(r["final"] * CAP_USER),
             series=(e.resample("W").last() / 1000).round(2).tolist(), dates=[str(x.date()) for x in e.resample("W").last().index],
             hist=np.histogram(np.clip(q, -15, 15), bins=np.arange(-15, 15.5, 1))[0].tolist())
    print(f"\n### {label}: CAGR {r['cagr']:+.1f}%  maxDD {r['maxdd']:.1f}%  Sharpe {r['sharpe']:.2f}  trades {len(tr)}  win {d['win']:.0%}  "
          f"avg win {d['avg_win']:+.2f}%  avg loss {d['avg_loss']:+.2f}%  in recovery {d['rec_share']:.0%} (avg size {d['rec_size_avg']:.2f}x, max {d['rec_size_max']:.2f}x)  "
          f"$125k -> ${d['final_usd']:,.0f}")
    for y in yearly:
        print(f"    {y['year']}: {y['ret']:+6.1f}% (${y['usd']:+,.0f})  trades {y['trades']}  win {y['win']:.0%}  prem {y['prem']:.2f}% vs move {y['move']:.2f}%  "
              f"in recovery {y['in_rec']:.0%}  max size {y['size_max']:.2f}x  worst trade {y['worst']:+.1f}%")
    print("    drawdowns:", [(x['start'], x['trough'], x['recovered'], round(x['depth'], 1), round(x['weeks'])) for x in d["drawdowns"]])
    print("    worst trades:", [(x['date'], round(x['move'], 1), round(x['prem'], 2), round(x['size'], 2), round(x['pnl'], 1)) for x in d["worst"]])
    print("    per-trade P&L percentiles:", {k: round(v, 2) for k, v in d["pnl_pct"].items()})
    return d


def leg_stats(days, paths, side, dow):
    S0, ST = paths[:, 0], paths[:, -1]
    iv = days.iv.to_numpy()
    c, _ = bs(S0, S0, iv, 1 / 365, "call")
    p, _ = bs(S0, S0, iv, 1 / 365, "put")
    prem = (c + p) / S0
    move = np.abs(ST / S0 - 1)
    m = (days.dow == dow).to_numpy()
    if side == "buy":
        x = move[m] - prem[m] * 1.015 - 0.0006
    else:
        x = prem[m] * 0.985 - 0.0006 - move[m]
    yrs = days.index.year[m]
    return dict(n=int(m.sum()), mean=float(x.mean() * 100), t=float(x.mean() / (x.std() / np.sqrt(len(x)))), win=float((x > 0).mean()),
                top10=float(np.sort(x)[-10:].sum() / x.sum()) if x.sum() > 0 else None,
                by_year={int(y): float(x[yrs == y].mean() * 100) for y in sorted(set(yrs))},
                edge_before_costs=float((1 - move[m].sum() / prem[m].sum()) * 100) * (-1 if side == "buy" else 1))


def main():
    days, paths = load_days()
    out = {}
    # ---------------- A. Saturday sell ----------------
    sat = {**EXEC, "side_by_dow": {5: 1}}
    grid = [("No recovery", {})] + [(f"Recover over {k} Saturdays, cap {c}x", dict(recovery=True, rec_k=k, rec_cap=c))
                                     for k in (2, 4, 8, 12) for c in (1.25, 1.5, 2.0, 3.0)]
    print("== A. Saturday sell, recovery grid")
    rows = []
    for name, kw in grid:
        r = three(days, paths, {**sat, **kw})
        rows.append(dict(name=name, **r))
        print(f"   {name:36s} IS {r['IS']['cagr']:+6.1f}%/{r['IS']['maxdd']:6.1f}%  OOS {r['OOS']['cagr']:+6.1f}%/{r['OOS']['maxdd']:6.1f}%  "
              f"full {r['full']['cagr']:+6.1f}%/{r['full']['maxdd']:6.1f}%  Sharpe {r['full']['sharpe']:.2f}  worst {r['full']['worst_day']:+.1f}%  max size {r['full']['max_size']:.2f}")
    out["sat_grid"] = rows
    best_is = max(rows, key=lambda x: x["IS"]["cagr"] / max(abs(x["IS"]["maxdd"]), 1))
    print("   best in-sample by CAGR/|DD|:", best_is["name"])
    out["sat_best_is"] = best_is["name"]
    rec_cfg = {**sat, "recovery": True, "rec_k": 8, "rec_cap": 1.5}
    out["sat_plain"] = detail(days, paths, sat, "Sell Saturday, no recovery")
    out["sat_rec"] = detail(days, paths, rec_cfg, "Sell Saturday + recovery (8 Saturdays, cap 1.5x)")
    out["sat_stats"] = leg_stats(days, paths, "sell", 5)
    out["sat_boot"] = {"No recovery": bootstrap(days, paths, sat), "Recovery 8 / 1.5x": bootstrap(days, paths, rec_cfg)}
    print("   bootstrap:", {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in out["sat_boot"].items()})
    out["sat_sens"] = {}
    for lab, kw in (("At mark", dict(spread=0.0)), ("1.5% from mark (base)", {}), ("3% from mark", dict(spread=0.03)),
                    ("5% from mark", dict(spread=0.05)), ("Options 5% cheaper", dict(iv_mult=0.95)), ("Options 5% richer", dict(iv_mult=1.05)),
                    ("0.5x notional", dict(lev=0.5)), ("1.5x notional", dict(lev=1.5)), ("2x notional", dict(lev=2.0))):
        out["sat_sens"][lab] = clean(simulate(days, paths, **{**rec_cfg, **kw}))
    print("   sensitivity:", {k: (round(v['cagr'], 1), round(v['maxdd'], 1)) for k, v in out["sat_sens"].items()})

    # ---------------- B. Monday buy ----------------
    print("\n== B. Buying each weekday's straddle (after 1.5% + fees), per trade")
    out["buy_by_dow"] = {}
    for d_ in range(7):
        st = leg_stats(days, paths, "buy", d_)
        out["buy_by_dow"][DOW[d_]] = st
        print(f"   buy {DOW[d_]}: mean {st['mean']:+.3f}%  t {st['t']:+.2f}  win {st['win']:.0%}  by year {({k: round(v, 2) for k, v in st['by_year'].items()})}")
    mon = {**EXEC, "side_by_dow": {0: -1}}
    mgrid = [("No recovery", {})] + [(f"Recover over {k} trades, cap {c}x", dict(recovery_buy=True, rec_k_buy=k, rec_cap_buy=c))
                                      for k in (1, 2, 4, 8) for c in (1.5, 2.0, 3.0)] + \
            [("Hedged hourly (gamma scalping)", dict(hedge_buy=True, band=0.25, stop_h=1)), ("No recovery, 0.5x", dict(lev_buy=0.5))]
    print("\n== B. Monday buy grid")
    mrows = []
    for name, kw in mgrid:
        r = three(days, paths, {**mon, **kw})
        mrows.append(dict(name=name, **r))
        print(f"   {name:36s} IS {r['IS']['cagr']:+6.1f}%/{r['IS']['maxdd']:6.1f}%  OOS {r['OOS']['cagr']:+6.1f}%/{r['OOS']['maxdd']:6.1f}%  "
              f"full {r['full']['cagr']:+6.1f}%/{r['full']['maxdd']:6.1f}%  max size {r['full']['max_size']:.2f}  yrs {({k: round(v) for k, v in r['full']['yearly'].items()})}")
    out["mon_grid"] = mrows
    out["mon_plain"] = detail(days, paths, mon, "Buy Monday, no recovery")
    out["mon_rec"] = detail(days, paths, {**mon, "recovery_buy": True, "rec_k_buy": 4, "rec_cap_buy": 2.0}, "Buy Monday + recovery (4 trades, cap 2x)")
    out["mon_boot"] = {"No recovery": bootstrap(days, paths, mon),
                       "Recovery 4 / 2x": bootstrap(days, paths, {**mon, "recovery_buy": True, "rec_k_buy": 4, "rec_cap_buy": 2.0})}
    print("   bootstrap:", {k: {kk: round(vv, 2) for kk, vv in v.items()} for k, v in out["mon_boot"].items()})

    # ---------------- C. combinations ----------------
    print("\n== C. Combinations")
    combos = {
        "Sell Sat + buy Sun": {**EXEC, "side_by_dow": {5: 1, 6: -1}},
        "Sell Sat + buy Sun, recovery on Sat": {**EXEC, "side_by_dow": {5: 1, 6: -1}, "recovery": True, "rec_k": 8, "rec_cap": 1.5},
        "Sell Sat + buy Sun + buy Mon": {**EXEC, "side_by_dow": {5: 1, 6: -1, 0: -1}},
        "Sell Sat (rec) + buy Sun + buy Mon": {**EXEC, "side_by_dow": {5: 1, 6: -1, 0: -1}, "recovery": True, "rec_k": 8, "rec_cap": 1.5},
        "Sell Sat (rec) + buy Sun + buy Mon (rec)": {**EXEC, "side_by_dow": {5: 1, 6: -1, 0: -1}, "recovery": True, "rec_k": 8, "rec_cap": 1.5,
                                                     "recovery_buy": True, "rec_k_buy": 4, "rec_cap_buy": 2.0},
        "Sell Sat (rec) + buy Mon": {**EXEC, "side_by_dow": {5: 1, 0: -1}, "recovery": True, "rec_k": 8, "rec_cap": 1.5},
    }
    out["combos"] = {}
    for name, c in combos.items():
        r = three(days, paths, c)
        out["combos"][name] = r
        print(f"   {name:42s} IS {r['IS']['cagr']:+6.1f}%/{r['IS']['maxdd']:6.1f}%  OOS {r['OOS']['cagr']:+6.1f}%/{r['OOS']['maxdd']:6.1f}%  "
              f"full {r['full']['cagr']:+6.1f}%/{r['full']['maxdd']:6.1f}%  Sharpe {r['full']['sharpe']:.2f}  yrs {({k: round(v) for k, v in r['full']['yearly'].items()})}")
    with open(os.path.join(ROOT, "results", "sat_mon.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
