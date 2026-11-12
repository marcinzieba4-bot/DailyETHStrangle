"""
Build a daily-vol selling strategy step by step on 2021-03 .. 2023-12 (in-sample), then test every stage on
2024-01 .. 2026-11 (out-of-sample) and with a block bootstrap. Execution assumed: sell 1.5% below mark
(between a resting maker order and hitting the bid); taker and pure-maker shown as sensitivities.

  python3 run_daily_vol_select.py   -> results/daily_vol.json
"""
from __future__ import annotations

import json
import os

import numpy as np
import pandas as pd

from run_daily_vol import edge_table, load_days, simulate

ROOT = os.path.dirname(os.path.abspath(__file__))
IS = dict(start="2021-03-24", end="2023-12-31")
OOS = dict(start="2024-01-01", end="2026-12-31")
EXEC = dict(spread=0.015)


def score(r):
    return r["sharpe"]


def pick(days, paths, base, grid, label):
    best, best_kw, rows = None, None, []
    for kw in grid:
        cfg = {**base, **kw}
        r = simulate(days, paths, **cfg, **IS)
        rows.append((kw, r))
        if best is None or score(r) > score(best):
            best, best_kw = r, kw
    print(f"\n== {label}")
    for kw, r in rows:
        mark = " <" if kw == best_kw else ""
        print(f"   {str(kw):55s} IS CAGR {r['cagr']:+7.1f}%  maxDD {r['maxdd']:6.1f}%  Sharpe {r['sharpe']:5.2f}  worst day {r['worst_day']:+6.1f}%  traded {r['traded']}{mark}")
    return {**base, **best_kw}, [(str(kw), {k: v for k, v in r.items() if k != "equity"}) for kw, r in rows]


def bootstrap(days, paths, cfg, n=300, horizon=730, block=10, seed=3):
    rng = np.random.default_rng(seed)
    N = len(days)
    res = []
    for _ in range(n):
        idx = []
        while len(idx) < horizon:
            s = rng.integers(0, N - block)
            idx.extend(range(s, s + block))
        idx = np.array(idx[:horizon])
        d = days.iloc[idx].copy()
        d.index = pd.date_range("2027-01-01", periods=horizon, freq="D")
        d["dow"] = days["dow"].to_numpy()[idx]
        r = simulate(d, paths[idx], **{k: v for k, v in cfg.items() if k not in ("start", "end")})
        res.append((r["cagr"], r["maxdd"], r["final"]))
    a = np.array(res)
    return dict(cagr_median=float(np.median(a[:, 0])), cagr_p5=float(np.percentile(a[:, 0], 5)), cagr_p95=float(np.percentile(a[:, 0], 95)),
                loss_2y=float(np.mean(a[:, 2] < 1)), dd_median=float(np.median(a[:, 1])), dd_p5=float(np.percentile(a[:, 1], 5)),
                dd_gt50=float(np.mean(a[:, 1] < -50)), ruin=float(np.mean(a[:, 2] < 0.2)))


def main():
    days, paths = load_days()
    print(len(days), "days", days.index[0].date(), "→", days.index[-1].date(), "| IV from real trades:", f"{(days.iv_src == 'trades').mean():.0%}")
    by_dow, by_ratio, by_year = edge_table(days, paths)
    print(by_dow.round(2), by_ratio.round(2), by_year.round(2), sep="\n")
    out = dict(n_days=len(days), iv_from_trades=float((days.iv_src == "trades").mean()),
               edge_dow=by_dow.round(3).reset_index().to_dict("list"), edge_ratio=by_ratio.round(3).reset_index().astype({"bucket": str}).to_dict("list"),
               edge_year=by_year.round(3).reset_index().to_dict("list"),
               iv_vs_model=float((days.iv_trades / days.iv_model).median()), spread_median=float(days.spread.median()))
    steps = {}
    cfg = dict(lev=1.0, **EXEC)
    base_cfg = dict(cfg)
    cfg, steps["hedge"] = pick(days, paths, cfg, [dict(hedge=False)] + [dict(hedge=True, band=b, stop_h=s) for b in (0.1, 0.25, 0.5) for s in (1, 2, 4)], "1. delta hedge")
    cfg_hedge = dict(cfg)
    cfg, steps["iv_filter"] = pick(days, paths, cfg, [dict(iv_filter=None)] + [dict(iv_filter=k) for k in (0.8, 0.9, 1.0, 1.1, 1.2)], "2. sell only when IV >= k x 7-day realised vol")
    cfg_filter = dict(cfg)
    cfg, steps["dow"] = pick(days, paths, cfg, [dict(skip_dow=()), dict(skip_dow=(6,)), dict(skip_dow=(2, 6)), dict(skip_dow=(2, 3, 6))], "3. skip weekdays (by start day, 6 = Sunday)")
    cfg_dow = dict(cfg)
    cfg, steps["wings"] = pick(days, paths, cfg, [dict(wings=None)] + [dict(wings=w) for w in (0.06, 0.08, 0.10, 0.15)], "4. buy wings (iron fly)")
    cfg_wings = dict(cfg)
    cfg, steps["recovery"] = pick(days, paths, cfg, [dict(recovery=False)] + [dict(recovery=True, rec_k=k, rec_cap=c) for k, c in ((5, 1.5), (10, 1.5), (10, 2.0), (20, 2.0), (10, 3.0))],
                                  "5. recovery size-up (recover drawdown from premium over K days, capped)")
    cfg_rec = dict(cfg)
    lev_rows = []
    print("\n== 6. leverage (notional / equity)")
    for L in (0.5, 1.0, 1.5, 2.0, 3.0):
        r = simulate(days, paths, **{**cfg, "lev": L}, **IS)
        r2 = simulate(days, paths, **{**cfg, "lev": L}, **OOS)
        lev_rows.append(dict(lev=L, IS={k: v for k, v in r.items() if k != "equity"}, OOS={k: v for k, v in r2.items() if k != "equity"}))
        print(f"   lev {L}: IS CAGR {r['cagr']:+.1f}% DD {r['maxdd']:.1f}% Sharpe {r['sharpe']:.2f} | OOS CAGR {r2['cagr']:+.1f}% DD {r2['maxdd']:.1f}% Sharpe {r2['sharpe']:.2f}")
    out["steps"] = steps
    out["leverage"] = lev_rows
    stages = {"1. Plain: sell daily ATM straddle": base_cfg, "2. + hourly delta hedge": cfg_hedge, "3. + IV filter": cfg_filter,
              "4. + weekday filter": cfg_dow, "5. + wings": cfg_wings, "6. + recovery size-up": cfg_rec}
    out["stages"] = {}
    print("\n== Stages: in-sample vs out-of-sample vs full")
    for name, c in stages.items():
        rr = {}
        for lab, per in (("IS", IS), ("OOS", OOS), ("full", dict(start=None, end=None))):
            r = simulate(days, paths, **c, **per)
            rr[lab] = {k: v for k, v in r.items() if k != "equity"}
            if lab == "full":
                rr["series"] = (r["equity"].resample("W").last() / 1000).round(2).tolist()
                rr["dates"] = [str(d_.date()) for d_ in r["equity"].resample("W").last().index]
        out["stages"][name] = dict(cfg={k: (list(v) if isinstance(v, tuple) else v) for k, v in c.items()}, **rr)
        print(f"   {name:38s} IS {rr['IS']['cagr']:+6.1f}%/{rr['IS']['maxdd']:6.1f}%  OOS {rr['OOS']['cagr']:+6.1f}%/{rr['OOS']['maxdd']:6.1f}% (Sharpe {rr['OOS']['sharpe']:.2f})  "
              f"full {rr['full']['cagr']:+6.1f}%/{rr['full']['maxdd']:6.1f}%  worst day {rr['full']['worst_day']:+.1f}%  yearly {({k: round(v) for k, v in rr['full']['yearly'].items()})}")
    final = cfg_rec
    out["final_cfg"] = {k: (list(v) if isinstance(v, tuple) else v) for k, v in final.items()}
    out["exec_sens"] = {}
    for lab, kw in (("taker (sell at the bid, ~3% below mark)", dict(spread=0.03)), ("1.5% below mark (assumed)", dict(spread=0.015)),
                    ("maker at mark", dict(spread=0.0)), ("1.5% below mark, options 5% cheaper", dict(spread=0.015, iv_mult=0.95)),
                    ("1.5% below mark, options 5% richer", dict(spread=0.015, iv_mult=1.05))):
        r = simulate(days, paths, **{**final, **kw})
        out["exec_sens"][lab] = {k: v for k, v in r.items() if k != "equity"}
        print(f"   exec: {lab:42s} full CAGR {r['cagr']:+.1f}%  DD {r['maxdd']:.1f}%  Sharpe {r['sharpe']:.2f}")
    print("\n== Block bootstrap, 2 years, 300 paths")
    out["bootstrap"] = {}
    for name in ("1. Plain: sell daily ATM straddle", "2. + hourly delta hedge", "6. + recovery size-up"):
        b = bootstrap(days, paths, stages[name])
        out["bootstrap"][name] = b
        print(f"   {name:38s} CAGR median {b['cagr_median']:+.1f}% (5% {b['cagr_p5']:+.1f}%, 95% {b['cagr_p95']:+.1f}%)  loss after 2y {b['loss_2y']:.0%}  "
              f"maxDD median {b['dd_median']:.1f}% (5% {b['dd_p5']:.1f}%)  DD>50% {b['dd_gt50']:.0%}")
    with open(os.path.join(ROOT, "results", "daily_vol.json"), "w") as fh:
        json.dump(out, fh, indent=1, default=float)


if __name__ == "__main__":
    main()
