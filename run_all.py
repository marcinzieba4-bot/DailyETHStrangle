"""
Run every simulation and write results/results.json (+ CSVs).

  python3 run_all.py            # use cached data in data/
  python3 run_all.py --refresh  # re-download history + live snapshot from Deribit first
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sim.engine import Carry, Costs, Strategy, bs_call_put, run  # noqa: E402
from sim import data as D  # noqa: E402
from sim import scenarios as SC  # noqa: E402

OUT = os.path.join(D.ROOT, "results")
os.makedirs(OUT, exist_ok=True)

N_WSTETH = 50.0
T_EXIT = 180      # exit mode: give up and mark to market after 180 days if never cleared
T_INC = 90        # income mode horizon
N_MC = 20000

STRATS = {
    "HODL 50 wstETH": Strategy("HODL", hold=True, mode="income"),
    "A. Call-only exit": Strategy("A", lot_frac=1.0, cap_mult=1.0, mode="exit"),
    "B. Your plan, adds up to 2x": Strategy("B", lot_frac=1.0, cap_mult=2.0, mode="exit"),
    "C. Your plan, unlimited adds": Strategy("C", lot_frac=1.0, cap_mult=None, mode="exit"),
    "D. Ladder 25% lots (cap 1x)": Strategy("D", lot_frac=0.25, cap_mult=1.0, mode="exit"),
    "A+stop. Call-only exit, stop -5%": Strategy("As", lot_frac=1.0, cap_mult=1.0, mode="exit", stop_frac=0.05),
}
INCOME = {
    "W1. Wheel 1x for 90d": Strategy("W1", lot_frac=1.0, cap_mult=1.0, mode="income"),
    "W2. Wheel 2x for 90d": Strategy("W2", lot_frac=1.0, cap_mult=2.0, mode="income"),
}


def pct(x, q):
    return float(np.nanpercentile(x, q))


def summarize(o, P0, Q0, exit_mode=True):
    rel = o["rel_pct"] * 100
    s = dict(
        mean_vs_sell_pct=float(np.mean(rel)), median_vs_sell_pct=float(np.median(rel)),
        p5_vs_sell_pct=pct(rel, 5), p1_vs_sell_pct=pct(rel, 1), p95_vs_sell_pct=pct(rel, 95),
        prob_beat_sell=float(np.mean(rel > 0)),
        mean_vs_sell_usd=float(np.mean(o["rel"])), p5_vs_sell_usd=pct(o["rel"], 5),
        worst_interim_vs_sell_usd_p5=pct(o["min_rel"], 5),
        maxE_p95=pct(o["maxE"], 95), prob_maxE_ge_2x=float(np.mean(o["maxE"] >= 2 * Q0 - 1e-6)),
        premium_mean=float(np.mean(o["prem"])),
    )
    if exit_mode:
        cl = o["cleared"]
        tau = np.where(cl, o["tau"], np.nan)
        s.update(
            prob_cleared_1d=float(np.mean(o["tau"] == 1)),
            prob_cleared_7d=float(np.mean(cl & (o["tau"] <= 7))),
            prob_cleared_30d=float(np.mean(cl & (o["tau"] <= 30))),
            prob_cleared=float(np.mean(cl)),
            prob_stopped=float(np.mean(o["stopped"])),
            days_median=float(np.nanmedian(tau)) if cl.any() else None,
            days_p90=pct(tau, 90) if cl.any() else None,
            exit_px_mean=float(np.mean(o["exit_px"])), exit_px_median=float(np.median(o["exit_px"])),
            exit_px_p5=pct(o["exit_px"], 5), exit_px_p25=pct(o["exit_px"], 25),
            exit_px_p75=pct(o["exit_px"], 75), exit_px_p95=pct(o["exit_px"], 95),
            mkt_px_at_clear_median=pct(o["S_tau"], 50) if cl.any() else None,
            mkt_px_at_clear_p5=pct(o["S_tau"], 5) if cl.any() else None,
            mkt_px_at_clear_p95=pct(o["S_tau"], 95) if cl.any() else None,
        )
    else:
        s.update(E_T_mean=float(np.mean(o["E_T"])), prob_flat_at_end=float(np.mean(o["E_T"] < 1e-6)),
                 prob_2x_at_end=float(np.mean(o["E_T"] > 1.5 * Q0)))
    return s


def main():
    if "--refresh" in sys.argv:
        D.refresh_history()
        D.fetch_live()
    live = D.load_live()
    hist = D.load_history()
    P0 = float(live["eth_index"])
    ratio = float(live["wsteth_ratio"])
    Q0 = N_WSTETH * ratio
    start_dow = pd.Timestamp(live["ts_utc"]).dayofweek
    day1_iv = float(live["daily_atm_iv"])
    dvol = float(live["dvol"])
    carry = Carry(stake_apr=float(live["steth_apr"]), fund_apr=0.015)
    costs = Costs()
    R = dict(live=live, P0=P0, Q0=Q0, position_usd=Q0 * P0, sell_today_usd=Q0 * P0 * (1 - costs.exit_cost),
             assumptions=dict(costs=costs.__dict__, carry=carry.__dict__, dow_iv=SC.DOW_IV.tolist(),
                              T_exit=T_EXIT, T_income=T_INC, n_mc=N_MC))

    # ---------- 1. Tomorrow's trade with today's real quotes ----------
    opts = {o["instrument_name"]: o for o in live["daily_options"]}
    k_lo = min(float(n.split("-")[2]) for n in opts)
    exp = live["daily_expiry"]
    c = opts[f"ETH-{exp}-{int(k_lo)}-C"]
    p = opts[f"ETH-{exp}-{int(k_lo)}-P"]
    und = c["underlying_price"]
    fee = 0.0003
    call_usd = (c["bid_price"] - min(fee, 0.125 * c["bid_price"])) * und
    put_usd = (p["bid_price"] - min(fee, 0.125 * p["bid_price"])) * und
    R["tomorrow"] = dict(
        strike=k_lo, expiry=exp, call=c, put=p, call_usd_per_eth=call_usd, put_usd_per_eth=put_usd,
        call_total_usd=call_usd * Q0, put_total_usd=put_usd * Q0,
        straddle_pct_of_spot=(call_usd + put_usd) / P0 * 100,
        exit_px_call_only=k_lo + call_usd, exit_px_straddle=k_lo + call_usd + put_usd,
        sell_now_px=P0 * (1 - costs.exit_cost),
        buy_px_if_down_straddle=k_lo - call_usd - put_usd,
        breakeven_down_call_only=k_lo - 0 + call_usd,  # still long; loss starts below K + premium
    )

    # ---------- 2. Illustrative day-by-day paths ----------
    paths = {
        "Up tomorrow, then keeps rising": [3, 2, 2, 1, 3, 2, 1, 2, 1, 2],
        "Grind lower 6 days, then bounce": [-2, -2.5, -1.5, -3, -2, -2, 3, 2.5, 3, 2],
        "Chop (+/-2% alternating)": [-2, 2, -2, 2, -2, 2, -2, 2, -2, 2],
        "Crash -30% then dead-cat": [-6, -8, -4, -9, -5, 2, -1, 3, -2, 2],
        "Selloff continues (-1.5%/day)": [-1.5] * 10,
    }
    R["illustrative"] = {}
    for name, mv in paths.items():
        S = SC.illustrative(P0, mv)[None, :]
        T = len(mv)
        iv = SC.iv_path(dvol, start_dow, T, day1_iv)[None, :]
        res = {}
        for sname in ["A. Call-only exit", "B. Your plan, adds up to 2x", "D. Ladder 25% lots (cap 1x)"]:
            st = STRATS[sname]
            st_inc = Strategy(st.name, st.lot_frac, st.cap_mult, mode="income")  # show full path, no early stop
            o_exit = run(S, iv, Q0, st, costs, carry)
            o_log = run(S, iv, Q0, st_inc, costs, carry, log=True)
            res[sname] = dict(log=o_log["log"],
                              cleared_day=int(o_exit["tau"][0]), exit_px=float(o_exit["exit_px"][0]),
                              mkt_px_at_clear=float(o_exit["S_tau"][0]) if o_exit["tau"][0] > 0 else None,
                              vs_sell_usd=float(o_exit["rel"][0]), vs_sell_pct=float(o_exit["rel_pct"][0] * 100))
        res["HODL_vs_sell_usd"] = float(Q0 * S[0, -1] - Q0 * P0 * (1 - costs.exit_cost))
        res["prices"] = S[0].tolist()
        res["moves"] = mv
        R["illustrative"][name] = res

    # ---------- 3. Monte Carlo regimes ----------
    pools = SC.z_pools(hist)
    R["mc"] = {}
    R["mc_hist"] = {}
    for i, (sname, sc) in enumerate(SC.mc_scenarios(T_EXIT).items()):
        S = SC.mc_paths(pools, P0, start_dow, sc["rv"], sc["mu"], N_MC, T_EXIT, seed=100 + i)
        iv = np.broadcast_to(SC.iv_path(sc["iv"], start_dow, T_EXIT, day1_iv * sc["iv"][0] / 56.0), S[:, :-1].shape)
        cr = Carry(stake_apr=carry.stake_apr, fund_apr=sc["fund"])
        row = {"desc": sc["desc"], "eth_median_90d": float(np.median(S[:, 90])), "eth_median_180d": float(np.median(S[:, -1]))}
        hists = {}
        for name, st in STRATS.items():
            Sx = S if st.mode == "exit" else S[:, :T_INC + 1]
            o = run(Sx, iv[:, :Sx.shape[1] - 1], Q0, st, costs, cr)
            row[name] = summarize(o, P0, Q0, exit_mode=st.mode == "exit")
            hists[name] = o["rel_pct"]
        for name, st in INCOME.items():
            o = run(S[:, :T_INC + 1], iv[:, :T_INC], Q0, st, costs, cr)
            row[name] = summarize(o, P0, Q0, exit_mode=False)
            hists[name] = o["rel_pct"]
        R["mc"][sname] = row
        # histogram data for charts (vs sell today, %)
        bins = np.linspace(-40, 40, 81)
        R["mc_hist"][sname] = {k: np.histogram(np.clip(v * 100, -40, 40), bins=bins)[0].tolist() for k, v in hists.items()}
        R["mc_hist"]["bins"] = bins.tolist()

    # Sensitivity of the no-view case to option pricing richness and execution
    R["sensitivity"] = {}
    sc = SC.mc_scenarios(T_EXIT)["No view (random walk)"]
    S = SC.mc_paths(pools, P0, start_dow, sc["rv"], sc["mu"], N_MC, T_EXIT, seed=7)
    for label, ivm, cst in [("IV 10% cheaper", 0.9, costs), ("Base", 1.0, costs), ("IV 10% richer", 1.1, costs),
                            ("Sell at mid (maker)", 1.0, Costs(spread_haircut=0.0)),
                            ("Wide spreads (7%)", 1.0, Costs(spread_haircut=0.07))]:
        iv = np.broadcast_to(SC.iv_path(sc["iv"] * ivm, start_dow, T_EXIT, day1_iv * ivm), S[:, :-1].shape)
        R["sensitivity"][label] = {}
        for name in ["A. Call-only exit", "B. Your plan, adds up to 2x", "D. Ladder 25% lots (cap 1x)"]:
            o = run(S, iv, Q0, STRATS[name], cst, carry)
            R["sensitivity"][label][name] = dict(mean_vs_sell_pct=float(np.mean(o["rel_pct"]) * 100),
                                                 p5_vs_sell_pct=pct(o["rel_pct"] * 100, 5))
        o = run(S[:, :T_INC + 1], iv[:, :T_INC], Q0, INCOME["W1. Wheel 1x for 90d"], cst, carry)
        R["sensitivity"][label]["W1. Wheel 1x for 90d"] = dict(mean_vs_sell_pct=float(np.mean(o["rel_pct"]) * 100),
                                                               p5_vs_sell_pct=pct(o["rel_pct"] * 100, 5))

    # ---------- 4. Historical backtest (real 08:00 settlements + DVOL-priced daily options) ----------
    R["hist"] = {}
    # premium vs payout check on a 1-lot daily straddle
    Sx, ivx, fx, sx, dates = SC.hist_windows(hist, 1)
    call, put = bs_call_put(Sx[:, 0], Sx[:, 0], ivx[:, 0])
    pay = np.abs(Sx[:, 1] - Sx[:, 0])
    gross = (call + put)
    net = gross * (1 - costs.spread_haircut) - 2 * np.minimum(costs.opt_fee * Sx[:, 0], costs.opt_fee_cap * call)
    R["hist"]["straddle_edge"] = dict(
        days=int(len(pay)), from_=str(dates[0].date()), to=str(dates[-1].date()),
        avg_premium_pct=float(np.mean(gross / Sx[:, 0]) * 100), avg_net_premium_pct=float(np.mean(net / Sx[:, 0]) * 100),
        avg_payout_pct=float(np.mean(pay / Sx[:, 0]) * 100),
        edge_gross_pct_of_premium=float((1 - pay.sum() / gross.sum()) * 100),
        edge_net_pct_of_premium=float((1 - pay.sum() / net.sum()) * 100),
        worst_day_pct=float(np.max(pay / Sx[:, 0]) * 100), win_rate=float(np.mean(pay < net)))
    by_year = pd.DataFrame({"y": dates.year, "g": gross / Sx[:, 0], "n": net / Sx[:, 0], "p": pay / Sx[:, 0]}).groupby("y").mean() * 100
    R["hist"]["straddle_by_year"] = {int(y): dict(premium=r.g, net=r.n, payout=r.p, edge_net=r.n - r.p) for y, r in by_year.iterrows()}

    Sx, ivx, fx, sx, dates = SC.hist_windows(hist, T_EXIT)
    R["hist"]["exit"] = {"n_starts": int(len(dates)), "from": str(dates[0].date()), "to": str(dates[-1].date())}
    ts = {"date": [str(d.date()) for d in dates], "price": Sx[:, 0].tolist()}
    for name, st in STRATS.items():
        if st.mode != "exit":
            continue
        o = run(Sx, ivx, Q0, st, costs, carry, fund=fx, stake=sx)
        R["hist"]["exit"][name] = summarize(o, P0, Q0, exit_mode=True)
        ts[name] = (o["rel_pct"] * 100).tolist()
        ts[name + "|days"] = o["tau"].tolist()
    R["hist"]["exit_ts"] = ts

    Sx, ivx, fx, sx, dates = SC.hist_windows(hist, T_INC)
    R["hist"]["income"] = {"n_starts": int(len(dates)), "from": str(dates[0].date()), "to": str(dates[-1].date())}
    ts2 = {"date": [str(d.date()) for d in dates]}
    for name, st in {**INCOME, "HODL 50 wstETH": STRATS["HODL 50 wstETH"]}.items():
        o = run(Sx, ivx, Q0, st, costs, carry, fund=fx, stake=sx)
        R["hist"]["income"][name] = summarize(o, P0, Q0, exit_mode=False)
        ts2[name] = (o["rel_pct"] * 100).tolist()
    R["hist"]["income_ts"] = ts2

    with open(os.path.join(OUT, "results.json"), "w") as fh:
        json.dump(R, fh, indent=1, default=float)
    print_summary(R)


def print_summary(R):
    P0, Q0 = R["P0"], R["Q0"]
    print(f"ETH {P0:.2f} | 50 wstETH = {Q0:.3f} ETH = ${Q0*P0:,.0f} | sell today -> ${R['sell_today_usd']:,.0f}")
    t = R["tomorrow"]
    print(f"\nTomorrow ({t['expiry']} 08:00 UTC), strike {t['strike']:.0f}: call ${t['call_usd_per_eth']:.2f}/ETH, put ${t['put_usd_per_eth']:.2f}/ETH "
          f"(straddle {t['straddle_pct_of_spot']:.2f}% of spot)")
    print(f"  up -> cleared at effective {t['exit_px_call_only']:.0f} (call only) / {t['exit_px_straddle']:.0f} (straddle) vs {t['sell_now_px']:.0f} now")
    print("\nIllustrative paths (exit mode): cleared day | effective exit $/ETH | vs sell-today $")
    for p, res in R["illustrative"].items():
        print(f"  {p}  [HODL vs sell: {res['HODL_vs_sell_usd']:+,.0f}]")
        for s in ["A. Call-only exit", "B. Your plan, adds up to 2x", "D. Ladder 25% lots (cap 1x)"]:
            r = res[s]
            print(f"     {s:32s} day {r['cleared_day']:>3} | {r['exit_px']:8.1f} | {r['vs_sell_usd']:+10,.0f} ({r['vs_sell_pct']:+.2f}%)")
    print("\nMonte Carlo (vs selling today): mean% | median% | 5th pct% | P(beat) | P(cleared 7d) | median exit px | p5 exit px | P(maxE>=2x)")
    for sc, row in R["mc"].items():
        print(f"  {sc}   (median ETH @90d {row['eth_median_90d']:.0f})")
        for k, s in row.items():
            if not isinstance(s, dict):
                continue
            print(f"     {k:32s} {s['mean_vs_sell_pct']:+6.2f} | {s['median_vs_sell_pct']:+6.2f} | {s['p5_vs_sell_pct']:+7.2f} | {s['prob_beat_sell']:.2f}"
                  + (f" | {s['prob_cleared_7d']:.2f} | {s['exit_px_median']:7.0f} | {s['exit_px_p5']:7.0f} | {s['prob_maxE_ge_2x']:.2f}" if 'prob_cleared_7d' in s else
                     f" | flat@end {s['prob_flat_at_end']:.2f}"))
    print("\nSensitivity (No view):")
    for k, v in R["sensitivity"].items():
        print("  ", k, {kk: round(vv["mean_vs_sell_pct"], 2) for kk, vv in v.items()})
    h = R["hist"]
    print("\nHistorical straddle edge:", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in h["straddle_edge"].items()})
    print("by year:", {y: {k: round(v, 3) for k, v in d.items()} for y, d in h["straddle_by_year"].items()})
    print(f"\nHistorical exit-mode ({h['exit']['n_starts']} start days {h['exit']['from']}..{h['exit']['to']}):")
    for k, s in h["exit"].items():
        if isinstance(s, dict):
            print(f"   {k:32s} mean {s['mean_vs_sell_pct']:+.2f}% med {s['median_vs_sell_pct']:+.2f}% p5 {s['p5_vs_sell_pct']:+.2f}% p1 {s['p1_vs_sell_pct']:+.2f}% "
                  f"beat {s['prob_beat_sell']:.2f} clr1d {s['prob_cleared_1d']:.2f} clr7d {s['prob_cleared_7d']:.2f} clr {s['prob_cleared']:.2f} med days {s['days_median']}")
    print(f"Historical income 90d ({h['income']['n_starts']} starts):")
    for k, s in h["income"].items():
        if isinstance(s, dict):
            print(f"   {k:32s} mean {s['mean_vs_sell_pct']:+.2f}% med {s['median_vs_sell_pct']:+.2f}% p5 {s['p5_vs_sell_pct']:+.2f}% beat {s['prob_beat_sell']:.2f} flat@end {s['prob_flat_at_end']:.2f}")


if __name__ == "__main__":
    main()
