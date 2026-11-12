"""Build results/report.html from results/results.json (run run_all.py first)."""
from __future__ import annotations

import html
import json
import os

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
R = json.load(open(os.path.join(ROOT, "results", "results.json")))
TPL = open(os.path.join(ROOT, "report", "template.html")).read()

P0, Q0 = R["P0"], R["Q0"]
POS = Q0 * P0
SELL = R["sell_today_usd"]
live = R["live"]
t = R["tomorrow"]
UNWIND = 0.0003 + 0.0003 + 0.0005  # perp open + perp close + wstETH sale once flat


def usd(x, sign=False):
    s = f"${abs(x):,.0f}"
    if sign:
        return ("+" if x >= 0 else "−") + s
    return ("−" if x < 0 else "") + s


def pct(x, d=1, sign=True):
    if round(x, d) == 0:
        x = 0.0
    if sign:
        return ("+" if x > 0 else ("−" if x < 0 else "")) + f"{abs(x):.{d}f}%"
    return f"{x:.{d}f}%"


def px(x):
    return f"${x:,.0f}"


V = {}
V["ASOF"] = pd.Timestamp(live["ts_utc"]).strftime("%-d %b %Y, %H:%M UTC")
V["ETH"] = f"${P0:,.2f}"
V["RATIO"] = f"{live['wsteth_ratio']:.4f}"
V["Q0"] = f"{Q0:.2f}"
V["Q0x2"] = f"{2*Q0:.1f}"
V["POS"] = usd(POS)
V["SELL"] = usd(SELL)
V["DVOL"] = f"{live['dvol']:.1f}"
V["DAILY_IV"] = f"{live['daily_atm_iv']:.1f}"
V["STAKE"] = f"{live['steth_apr']*100:.2f}"
V["LOT25"] = f"{Q0/4:.1f}"
V["STOP_PX"] = px(P0 * 0.95)

# ---- tomorrow ----
c, p = t["call"], t["put"]
V["EXP"] = t["expiry"]
V["K"] = f"{t['strike']:,.0f}"
V["C_BID"] = f"{c['bid_price']:.4f}"
V["P_BID"] = f"{p['bid_price']:.4f}"
V["C_ASK"] = f"{c['ask_price']:.4f}"
V["P_ASK"] = f"{p['ask_price']:.4f}"
V["C_IV"] = f"{c['mark_iv']:.1f}"
V["C_NET"] = f"${t['call_usd_per_eth']:.2f}"
V["P_NET"] = f"${t['put_usd_per_eth']:.2f}"
V["S_NET"] = f"${t['call_usd_per_eth']+t['put_usd_per_eth']:.2f}"
V["C_TOT"] = usd(t["call_total_usd"])
V["P_TOT"] = usd(t["put_total_usd"])
V["S_TOT"] = usd(t["call_total_usd"] + t["put_total_usd"])
V["S_PCT"] = f"{t['straddle_pct_of_spot']:.2f}%"
K = t["strike"]
exA = t["exit_px_call_only"] - UNWIND * K
exB = t["exit_px_straddle"] - UNWIND * K
V["EXIT_A"] = f"${exA:,.0f}"
V["EXIT_B"] = f"${exB:,.0f}"
V["SELL_PX"] = f"${t['sell_now_px']:,.0f}"
V["GAIN_A"] = usd((exA - t["sell_now_px"]) * Q0, True)
V["GAIN_B"] = usd((exB - t["sell_now_px"]) * Q0, True)
V["BUY_B"] = f"${t['buy_px_if_down_straddle']:,.0f}"
V["EXPO_B"] = usd(2 * Q0 * K)

# ---- historical strip (exit mode) ----
ts = pd.DataFrame(R["hist"]["exit_ts"])
H = R["hist"]["exit"]
ORDER = [("A+stop. Call-only exit, stop -5%", "A + stop", "Calls only, −5% stop"),
         ("A. Call-only exit", "A", "Calls only"),
         ("B. Your plan, adds up to 2x", "B", "Your plan, adds to 2×"),
         ("D. Ladder 25% lots (cap 1x)", "D", "25% ladder"),
         ("C. Your plan, unlimited adds", "C", "Your plan, no cap")]
strip = []
for key, short, label in ORDER:
    x = ts[key].to_numpy()
    s = H[key]
    strip.append(dict(key=short, label=label, p1=float(np.percentile(x, 1)), p5=float(np.percentile(x, 5)),
                      p25=float(np.percentile(x, 25)), p50=float(np.median(x)), p75=float(np.percentile(x, 75)),
                      p95=float(np.percentile(x, 95)), mean=float(x.mean()), beat=s["prob_beat_sell"],
                      d50=s["days_median"], d90=s["days_p90"], clr1=s["prob_cleared_1d"], maxE2=s["prob_maxE_ge_2x"],
                      worst=float(x.min())))

rows = []
for r in strip:
    rows.append(
        f"<tr><th scope='row'><span class='tag'>{r['key']}</span> {html.escape(r['label'])}</th>"
        f"<td>{pct(r['p50'])}</td><td>{pct(r['mean'])}</td><td>{r['beat']*100:.0f}%</td>"
        f"<td class='neg'>{pct(r['p5'])}</td><td class='neg'>{pct(r['p1'])}</td><td class='neg'>{pct(r['worst'])}</td>"
        f"<td>{r['d50']:.0f} / {r['d90']:.0f}</td><td>{r['maxE2']*100:.0f}%</td></tr>")
V["HIST_ROWS"] = "\n".join(rows)
V["HIST_N"] = f"{H['n_starts']:,}"
V["HIST_FROM"] = pd.Timestamp(H["from"]).strftime("%-d %b %Y")
V["HIST_TO"] = pd.Timestamp(H["to"]).strftime("%-d %b %Y")
A_ = H["A. Call-only exit"]
B_ = H["B. Your plan, adds up to 2x"]
AS_ = H["A+stop. Call-only exit, stop -5%"]
V["A_MED"] = pct(A_["median_vs_sell_pct"])
V["A_P5"] = pct(A_["p5_vs_sell_pct"])
V["A_P1"] = pct(A_["p1_vs_sell_pct"])
V["A_BEAT"] = f"{A_['prob_beat_sell']*100:.0f}%"
V["A_CLR1"] = f"{A_['prob_cleared_1d']*100:.0f}%"
V["B_MED"] = pct(B_["median_vs_sell_pct"])
V["B_P5"] = pct(B_["p5_vs_sell_pct"])
V["B_P1"] = pct(B_["p1_vs_sell_pct"])
V["B_WORST"] = pct(min(ts["B. Your plan, adds up to 2x"]))
V["B_2X"] = f"{B_['prob_maxE_ge_2x']*100:.0f}%"
V["AS_MED"] = pct(AS_["median_vs_sell_pct"])
V["AS_P5"] = pct(AS_["p5_vs_sell_pct"])
V["AS_P1"] = pct(AS_["p1_vs_sell_pct"])
V["AS_STOP"] = f"{AS_['prob_stopped']*100:.0f}%"
V["AS_D90"] = f"{AS_['days_p90']:.0f}"
V["AS_MED_USD"] = usd(AS_["median_vs_sell_pct"] / 100 * SELL, True)
V["AS_P5_USD"] = usd(AS_["p5_vs_sell_pct"] / 100 * SELL, True)
V["C_P1"] = pct(H["C. Your plan, unlimited adds"]["p1_vs_sell_pct"], 0)

# regime: starts right after a >5% four-day drop (like 8->12 Nov 2026)
pr = pd.Series(ts["price"].to_numpy(), index=pd.to_datetime(ts["date"]))
drop = ((pr / pr.shift(4) - 1) < -0.05).to_numpy()
V["DROP_BASE"] = f"{drop.mean()*100:.0f}%"
for tag, key in [("A", "A. Call-only exit"), ("B", "B. Your plan, adds up to 2x"), ("AS", "A+stop. Call-only exit, stop -5%")]:
    x = ts[key].to_numpy()
    V[f"{tag}_DROP_P5"] = pct(np.percentile(x[drop], 5))
    V[f"{tag}_DROP_MEAN"] = pct(x[drop].mean(), 2)
    V[f"{tag}_CALM_P5"] = pct(np.percentile(x[~drop], 5))
    V[f"{tag}_CALM_MEAN"] = pct(x[~drop].mean(), 2)
V["RET4_NOW"] = pct((P0 / 2235.74 - 1) * 100, 0)

e = R["hist"]["straddle_edge"]
V["EDGE_PREM"] = f"{e['avg_premium_pct']:.2f}%"
V["EDGE_NET"] = f"{e['avg_net_premium_pct']:.2f}%"
V["EDGE_PAY"] = f"{e['avg_payout_pct']:.2f}%"
V["EDGE_DAY"] = f"{e['avg_net_premium_pct']-e['avg_payout_pct']:+.2f}%"
V["EDGE_GROSS_PCT"] = f"{e['edge_gross_pct_of_premium']:.1f}%"
V["EDGE_NET_PCT"] = f"{e['edge_net_pct_of_premium']:.1f}%"
V["EDGE_WORST"] = f"{e['worst_day_pct']:.1f}%"
V["EDGE_WIN"] = f"{e['win_rate']*100:.0f}%"
yr = []
for y, d in R["hist"]["straddle_by_year"].items():
    cls = "pos" if d["edge_net"] > 0 else "neg"
    yr.append(f"<tr><th scope='row'>{y}</th><td>{d['premium']:.2f}%</td><td>{d['net']:.2f}%</td><td>{d['payout']:.2f}%</td>"
              f"<td class='{cls}'>{d['edge_net']:+.2f}%</td></tr>")
V["YEAR_ROWS"] = "\n".join(yr)

sens = []
for lab, d in R["sensitivity"].items():
    sens.append(f"<tr><th scope='row'>{html.escape(lab)}</th>" + "".join(
        f"<td class='{'pos' if d[k]['mean_vs_sell_pct'] > 0 else 'neg'}'>{pct(d[k]['mean_vs_sell_pct'], 2)}</td>"
        for k in ["A. Call-only exit", "B. Your plan, adds up to 2x", "D. Ladder 25% lots (cap 1x)", "W1. Wheel 1x for 90d"]) + "</tr>")
V["SENS_ROWS"] = "\n".join(sens)

inc = R["hist"]["income"]
V["W1_MEAN"] = pct(inc["W1. Wheel 1x for 90d"]["mean_vs_sell_pct"])
V["W1_P5"] = pct(inc["W1. Wheel 1x for 90d"]["p5_vs_sell_pct"])
V["W1_FLAT"] = f"{inc['W1. Wheel 1x for 90d']['prob_flat_at_end']*100:.0f}%"
V["HODL90_MEAN"] = pct(inc["HODL 50 wstETH"]["mean_vs_sell_pct"])
V["HODL90_P5"] = pct(inc["HODL 50 wstETH"]["p5_vs_sell_pct"])

# ---- exit price distribution (MC, no view) ----
nv = R["mc"]["No view (random walk)"]
ex = []
for key, short, label in [("A+stop. Call-only exit, stop -5%", "A + stop", "Calls only, −5% stop"),
                          ("A. Call-only exit", "A", "Calls only"),
                          ("B. Your plan, adds up to 2x", "B", "Your plan, adds to 2×"),
                          ("D. Ladder 25% lots (cap 1x)", "D", "25% ladder")]:
    s = nv[key]
    ex.append(f"<tr><th scope='row'><span class='tag'>{short}</span> {label}</th>"
              f"<td class='neg'>{px(s['exit_px_p5'])}</td><td>{px(s['exit_px_p25'])}</td><td><strong>{px(s['exit_px_median'])}</strong></td>"
              f"<td>{px(s['exit_px_p75'])}</td><td>{px(s['exit_px_p95'])}</td>"
              f"<td>{s['days_median']:.0f} / {s['days_p90']:.0f}</td>"
              f"<td>{px(s['mkt_px_at_clear_median'])}</td></tr>")
V["EXIT_ROWS"] = "\n".join(ex)
V["NV_A_MED"] = px(nv["A. Call-only exit"]["exit_px_median"])
V["NV_A_P5"] = px(nv["A. Call-only exit"]["exit_px_p5"])
V["NV_B_P5"] = px(nv["B. Your plan, adds up to 2x"]["exit_px_p5"])

# ---- MC scenario table ----
COLS = [("HODL 50 wstETH", "Keep all"), ("A. Call-only exit", "A"), ("A+stop. Call-only exit, stop -5%", "A + stop"),
        ("B. Your plan, adds up to 2x", "B"), ("D. Ladder 25% lots (cap 1x)", "D"), ("W1. Wheel 1x for 90d", "Wheel 90d")]
mc_rows = []
for sc, row in R["mc"].items():
    cells = []
    for k, _ in COLS:
        s = row[k]
        m = s["mean_vs_sell_pct"]
        a = min(abs(m), 25) / 25
        tone = "p" if m > 0 else "n"
        cells.append(f"<td class='hm' style='--a:{a:.2f}' data-tone='{tone}'><span class='big'>{pct(m)}</span>"
                     f"<span class='sm'>med {pct(s['median_vs_sell_pct'])} · 5%: {pct(s['p5_vs_sell_pct'], 0)}</span></td>")
    mc_rows.append(f"<tr><th scope='row'>{html.escape(sc)}<span class='sm'>{html.escape(row['desc'])}</span></th>{''.join(cells)}</tr>")
V["MC_HEAD"] = "".join(f"<th scope='col'>{h}</th>" for _, h in COLS)
V["MC_ROWS"] = "\n".join(mc_rows)
V["C_BEAR"] = pct(R["mc"]["Bear -40% / 90d"]["C. Your plan, unlimited adds"]["mean_vs_sell_pct"], 0)
V["C_CRASH"] = pct(R["mc"]["Crash -35% in 10d, then flat"]["C. Your plan, unlimited adds"]["mean_vs_sell_pct"], 0)

# ---- illustrative paths ----
il = []
details = []
for name, res in R["illustrative"].items():
    cells = []
    for k in ["A. Call-only exit", "B. Your plan, adds up to 2x", "D. Ladder 25% lots (cap 1x)"]:
        r = res[k]
        cls = "pos" if r["vs_sell_usd"] > 0 else "neg"
        if r["cleared_day"] > 0:
            st = f"flat on day {r['cleared_day']} · {px(r['exit_px'])}/ETH"
        else:
            st = f"still long {r['log'][-1]['exposure']:.0f} ETH"
        cells.append(f"<td><span class='{cls} big'>{usd(r['vs_sell_usd'], True)}</span><span class='sm'>{st}</span></td>")
    hodl = res["HODL_vs_sell_usd"]
    mv = " ".join(f"{m:+g}" for m in res["moves"])
    il.append(f"<tr><th scope='row'>{html.escape(name)}<span class='sm mono'>{mv}</span></th>{''.join(cells)}"
              f"<td><span class='{'pos' if hodl > 0 else 'neg'} big'>{usd(hodl, True)}</span><span class='sm'>ETH ends {px(res['prices'][-1])}</span></td></tr>")
    lg = res["B. Your plan, adds up to 2x"]["log"]
    trs = "".join(
        f"<tr><td>{d['day']}</td><td>{px(d['K'])}</td><td>{px(d['S1'])}</td><td>{d['move']}</td>"
        f"<td>{d['call_sz']:.1f} / {d['put_sz']:.1f}</td><td>{usd(d['premium'])}</td>"
        f"<td>{d['wst_eth']:.1f}</td><td>{d['perp_short']:.1f}</td><td><strong>{d['exposure']:.1f}</strong></td>"
        f"<td class='{'pos' if d['vs_sell_today'] > 0 else 'neg'}'>{usd(d['vs_sell_today'], True)}</td></tr>" for d in lg)
    details.append(f"<details><summary>{html.escape(name)}</summary><div class='scroll'><table class='log'>"
                   f"<thead><tr><th>Day</th><th>Strike</th><th>Settle</th><th>Move</th><th>Call / put sold (ETH)</th><th>Premium</th>"
                   f"<th>wstETH (ETH)</th><th>Perp short</th><th>Net ETH</th><th>vs sell today</th></tr></thead><tbody>{trs}</tbody></table></div></details>")
V["IL_ROWS"] = "\n".join(il)
V["IL_DETAILS"] = "\n".join(details)

# ---- clarified plan: accumulate on dips, distribute on rallies ----
A = json.load(open(os.path.join(ROOT, "results", "accumulate.json")))
PV = [("6.25 ETH/day", "Your plan, 6.25 ETH/day"), ("2.5 ETH/day", "Your plan, 2.5 ETH/day"),
      ("15.6 ETH/day", "Your plan, 15.6 ETH/day"), ("6.25 ETH/day, max 2x", "6.25/day, max 125 ETH"),
      ("6.25 ETH/day, sell only >= start, max 2x", "Fixed: sell only ≥ $2,000, max 125 ETH")]


def hm_cell(m, med, p5, scale=60):
    a = min(abs(m), scale) / scale
    tone = "p" if m > 0 else "n"
    return (f"<td class='hm' style='--a:{a:.2f}' data-tone='{tone}'><span class='big'>{pct(m, 0)}</span>"
            f"<span class='sm'>med {pct(med, 0)} · 5%: {pct(p5, 0)}</span></td>")


rows = []
blocks = [(k, v, v["desc"]) for k, v in A["mc"].items()]
blocks += [("History, 1-year windows", A["hist"]["365"], f"{A['hist']['365']['n']:,} starts, Mar 2021 – Nov 2025"),
           ("History, 2-year windows", A["hist"]["730"], f"{A['hist']['730']['n']:,} starts, Mar 2021 – Nov 2024")]
for name, blk, desc in blocks:
    h = blk["hodl"]
    cells = [hm_cell(h["mean"], h["median"], h["p5"])]
    for k, _ in PV:
        x = blk[k]
        cells.append(hm_cell(x["mean"], x["median"], x["p5"]))
    rows.append(f"<tr><th scope='row'>{html.escape(name)}<span class='sm'>{html.escape(desc)}</span></th>{''.join(cells)}</tr>")
V["PLAN_HEAD"] = "<th scope='col'>Keep all 50 wstETH</th>" + "".join(f"<th scope='col'>{html.escape(l)}</th>" for _, l in PV)
V["PLAN_ROWS"] = "\n".join(rows)

nvA = A["mc"]["No view (random walk)"]
rpA = A["mc"]["2022 replay: -60% in 6 months, then flat"]
h1 = A["hist"]["365"]
cash_rows = []
for k, l in PV:
    x, r22, hh = nvA[k], rpA[k], h1[k]
    cash_rows.append(
        f"<tr><th scope='row'>{html.escape(l)}</th><td>{x['cleared']*100:.0f}%<span class='sm'>history {hh['cleared']*100:.0f}%</span></td>"
        f"<td>{px(x['exit_px_cleared_med'])}<span class='sm'>history {pct(hh['exit_prem_cleared_med'], 0)} vs start</span></td>"
        f"<td>{x['eth_end_med']:.0f} / {x['eth_end_p95']:.0f}</td>"
        f"<td>{x['maxW_med']:.0f} / {x['maxW_p95']:.0f}</td>"
        f"<td>{usd(x['cash_need_med'])} / {usd(x['cash_need_p95'])}</td>"
        f"<td class='neg'>{r22['maxW_med']:.0f} ETH<span class='sm'>{usd(r22['cash_need_med'])} cash · {usd(r22['mean']/100*SELL, True)}</span></td></tr>")
V["CASH_ROWS"] = "\n".join(cash_rows)

pk = "6.25 ETH/day"
pnl_rows = []
for lab, key in [("Option premium minus payouts", "pnl_edge"), ("Staking on all wstETH held", "pnl_staking"),
                 ("Perp funding on the hedged part", "pnl_funding"), ("Fees, slippage, unwind", "pnl_costs"),
                 ("ETH price moves × your position", "pnl_inventory"), ("Total vs selling today", "mean")]:
    vals = [A["mc"]["No view (random walk)"][pk][key], A["mc"]["Mean-reverts to $2,000"][pk][key],
            A["mc"]["2022 replay: -60% in 6 months, then flat"][pk][key], A["hist"]["365"][pk][key]]
    tag = "th" if key == "mean" else "th"
    pnl_rows.append(f"<tr><{tag} scope='row'>{'<strong>' + lab + '</strong>' if key == 'mean' else lab}</{tag}>" + "".join(
        f"<td class='{'pos' if v > 0 else 'neg'}'>{pct(v, 1)}</td>" for v in vals) + "</tr>")
V["PNL_ROWS"] = "\n".join(pnl_rows)

# mean-reversion test on 08:00 settlements since daily data starts
pr_all = pd.read_csv(os.path.join(ROOT, "data", "eth_delivery_prices.csv"), parse_dates=["date"]).set_index("date")["price"]
pr_all = pr_all[pr_all.index >= "2020-02-18"]
lr = np.log(pr_all)
r1 = lr.diff().dropna()
V["MR_SINCE"] = pr_all.index[0].strftime("%b %Y")
V["MR_AC1"] = f"{r1.autocorr(1):+.2f}"
for k in (10, 60, 250):
    V[f"MR_VR{k}"] = f"{lr.diff(k).dropna().var() / (k * r1.var()):.2f}"
sg = np.sign(r1)
V["MR_PUP"] = f"{((sg > 0) & (sg.shift() < 0)).sum() / (sg.shift() < 0).sum() * 100:.0f}%"

x = nvA[pk]
V["PL_NV_MEAN"] = pct(x["mean"])
V["PL_NV_MED"] = pct(x["median"])
V["PL_NV_P5"] = pct(x["p5"], 0)
V["PL_NV_BEAT"] = f"{x['beat']*100:.0f}%"
V["PL_NV_CLR"] = f"{x['cleared']*100:.0f}%"
V["PL_NV_EXIT"] = px(x["exit_px_cleared_med"])
V["PL_NV_NOTCLR"] = pct(x["rel_notcleared_med"], 0)
V["PL_NV_CASH"] = usd(x["cash_need_med"])
V["PL_NV_CASH95"] = usd(x["cash_need_p95"])
V["PL_NV_EDGE"] = pct(x["pnl_edge"])
V["PL_NV_STAKE"] = pct(x["pnl_staking"])
V["PL_NV_USD4"] = pct(nvA["usd4"][pk]["mean"])
V["PL_MR_MEAN"] = pct(A["mc"]["Mean-reverts to $2,000"][pk]["mean"], 0)
V["PL_22_MEAN"] = pct(rpA[pk]["mean"], 0)
V["PL_22_USD"] = usd(rpA[pk]["mean"] / 100 * SELL, True)
V["PL_22_ETH"] = f"{rpA[pk]['maxW_med']:.0f}"
V["PL_22_CASH"] = usd(rpA[pk]["cash_need_med"])
V["PL_BULL"] = pct(A["mc"]["Bull +80% in a year"][pk]["mean"], 0)
V["PL_BULL_HODL"] = pct(A["mc"]["Bull +80% in a year"]["hodl"]["mean"], 0)
V["PL_H_MEAN"] = pct(h1[pk]["mean"], 0)
V["PL_H_MED"] = pct(h1[pk]["median"], 0)
V["PL_H_P5"] = pct(h1[pk]["p5"], 0)
V["PL_H_BEAT"] = f"{h1[pk]['beat']*100:.0f}%"
V["PL_H_HODL_MEAN"] = pct(h1["hodl"]["mean"], 0)
V["PL_H_HODL_MED"] = pct(h1["hodl"]["median"], 0)
V["PL_H_CLR"] = f"{h1[pk]['cleared']*100:.0f}%"
V["PL_H2_MEAN"] = pct(A["hist"]["730"][pk]["mean"], 0)
V["PL_H2_HODL"] = pct(A["hist"]["730"]["hodl"]["mean"], 0)
dr = A["mc"]["Dip -35%, then recovers to $2,400"]
V["DIP_CAP"] = pct(dr["6.25 ETH/day, max 2x"]["mean"], 0)
V["DIP_CAP_EXIT"] = px(dr["6.25 ETH/day, max 2x"]["exit_px_cleared_med"])
V["DIP_FIX"] = pct(dr["6.25 ETH/day, sell only >= start, max 2x"]["mean"], 0)
fx = "6.25 ETH/day, sell only >= start, max 2x"
V["FIX_NV_MEAN"] = pct(nvA[fx]["mean"], 0)
V["FIX_MR"] = pct(A["mc"]["Mean-reverts to $2,000"][fx]["mean"], 0)
V["FIX_H_MEAN"] = pct(h1[fx]["mean"], 0)
V["FIX_H_MED"] = pct(h1[fx]["median"], 0)
V["FIX_H_P5"] = pct(h1[fx]["p5"], 0)
V["FIX_22"] = pct(rpA[fx]["mean"], 0)
V["FIX_CASH95"] = usd(nvA[fx]["cash_need_p95"])
V["FIX_EXIT"] = px(nvA[fx]["exit_px_cleared_med"])
V["FIX_CLR"] = f"{nvA[fx]['cleared']*100:.0f}%"

# ---- version 3: 1 ETH a day, never below 50 wstETH under the start price ----
RG = json.load(open(os.path.join(ROOT, "results", "range.json")))
k1, k3 = "1 ETH/day", "3 ETH/day"


def kusd(x, sign=True):
    v = x / 1000
    s_ = f"${abs(v):,.0f}k" if abs(v) >= 9.5 else f"${abs(v):,.1f}k"
    return (("+" if x >= 0 else "−") if sign else ("−" if x < 0 else "")) + s_


def usd_cell(m, med, p5, beat=None, scale=30000):
    a = min(abs(m), scale) / scale
    tone = "p" if m > 0 else "n"
    extra = f" · beats {beat*100:.0f}%" if beat is not None else ""
    return (f"<td class='hm' style='--a:{a:.2f}' data-tone='{tone}'><span class='big'>{kusd(m)}</span>"
            f"<span class='sm'>med {kusd(med)} · 5%: {kusd(p5)}{extra}</span></td>")


rg_blocks = [(k, v, v["desc"]) for k, v in RG["mc"].items()]
rg_blocks += [("History, 1-year windows", RG["hist"]["365"], f"{RG['hist']['365']['n']:,} starts, Mar 2021 – Nov 2025"),
              ("History, 2-year windows", RG["hist"]["730"], f"{RG['hist']['730']['n']:,} starts, Mar 2021 – Nov 2024")]
rows = []
for name, blk, desc in rg_blocks:
    a1, a3 = blk[k1], blk[k3]
    rows.append(
        f"<tr><th scope='row'>{html.escape(name)}<span class='sm'>{html.escape(desc)}</span></th>"
        + hm_cell(a1["hodl_vs_sell_mean"], a1["hodl_vs_sell_med"], a1["hodl_vs_sell_p5"], 80)
        + hm_cell(a1["vs_sell_mean"], a1["vs_sell_med"], a1["vs_sell_p5"], 80)
        + usd_cell(a1["vs_hodl_mean_usd"], a1["vs_hodl_med_usd"], a1["vs_hodl_p5_usd"], a1["beat_hodl"])
        + usd_cell(a3["vs_hodl_mean_usd"], a3["vs_hodl_med_usd"], a3["vs_hodl_p5_usd"], a3["beat_hodl"], 90000) + "</tr>")
V["RG_ROWS"] = "\n".join(rows)

money_cols = [("Range around $2,000", RG["mc"]["Range around $2,000"]),
              ("Wanders, back at $2,000", RG["mc"]["Wanders, back at $2,000 after 2 years"]),
              ("Dip to ~$1,200, back at $2,000", RG["mc"]["Dips to ~$1,200, back at $2,000"]),
              ("No view", RG["mc"]["No view (random walk)"]),
              ("2022 replay", RG["mc"]["2022 replay: −60%, no recovery"]),
              ("History, 2 years", RG["hist"]["730"])]
V["MONEY_HEAD"] = "".join(f"<th scope='col'>{html.escape(n)}</th>" for n, _ in money_cols)
mrows = []
for lab, key, strong in [("Premium collected", "premium_usd", False), ("Paid out on assigned options", "payout_usd", False),
                         ("Option edge (premium − payouts)", "edge_usd", True), ("Staking on the extra wstETH", "extra_stake_usd", False),
                         ("Perp funding received", "funding_usd", False), ("Fees and slippage", "costs_usd", False),
                         ("Range effect: more ETH after drops, less after rises", "grid_usd", True),
                         ("Total vs keeping 50 wstETH", "vs_hodl_mean_usd", True)]:
    cells = []
    for _, blk in money_cols:
        v = blk[k1][key]
        if key == "payout_usd":
            v = -v
        cls = "" if key == "premium_usd" or abs(v) < 0.5 else ("pos" if v > 0 else "neg")
        cells.append(f"<td class='{cls}'>{'<strong>' if strong else ''}{usd(v, True)}{'</strong>' if strong else ''}</td>")
    mrows.append(f"<tr><th scope='row'>{'<strong>' + lab + '</strong>' if strong else lab}</th>{''.join(cells)}</tr>")
V["MONEY_ROWS"] = "\n".join(mrows)

prow = []
for name, blk in [("Range around $2,000", RG["mc"]["Range around $2,000"]), ("No view", RG["mc"]["No view (random walk)"]),
                  ("Dip to ~$1,200, back at $2,000", RG["mc"]["Dips to ~$1,200, back at $2,000"]),
                  ("2022 replay", RG["mc"]["2022 replay: −60%, no recovery"]), ("Bull: doubles", RG["mc"]["Bull: doubles in 2 years"]),
                  ("History, 2 years", RG["hist"]["730"])]:
    cells = []
    for k in (k1, k3):
        x = blk[k]
        cells.append(f"<td>{x['E_T_med']:.0f} / {x['E_T_p95']:.0f}</td><td>{x['maxW_p95']:.0f}</td>"
                     f"<td>{kusd(x['cash_med'], False)} / {kusd(x['cash_p95'], False)}</td>")
    prow.append(f"<tr><th scope='row'>{html.escape(name)}</th>{''.join(cells)}</tr>")
V["POS_ROWS"] = "\n".join(prow)

rr = RG["mc"]["Range around $2,000"]
wb = RG["mc"]["Wanders, back at $2,000 after 2 years"]
nv3 = RG["mc"]["No view (random walk)"]
r22 = RG["mc"]["2022 replay: −60%, no recovery"]
bl = RG["mc"]["Bull: doubles in 2 years"]
h2 = RG["hist"]["730"]
V["R_RANGE"] = kusd(rr[k1]["vs_hodl_mean_usd"])
V["R_RANGE_BEAT"] = f"{rr[k1]['beat_hodl']*100:.0f}%"
V["R_CALM"] = kusd(RG["mc"]["Calm range around $2,000"][k1]["vs_hodl_mean_usd"])
V["R_WANDER"] = kusd(wb[k1]["vs_hodl_mean_usd"])
V["R_DIP"] = kusd(RG["mc"]["Dips to ~$1,200, back at $2,000"][k1]["vs_hodl_mean_usd"])
V["R_H2"] = kusd(h2[k1]["vs_hodl_mean_usd"])
V["R_H2_MED"] = kusd(h2[k1]["vs_hodl_med_usd"])
V["R_H2_BEAT"] = f"{h2[k1]['beat_hodl']*100:.0f}%"
V["R_H2_P5"] = kusd(h2[k1]["vs_hodl_p5_usd"])
V["R_PREM"] = kusd(rr[k1]["premium_usd"], False)
V["R_PAY"] = kusd(rr[k1]["payout_usd"], False)
V["R_EDGE"] = usd(rr[k1]["edge_usd"])
V["R_GRID"] = kusd(rr[k1]["grid_usd"])
V["R_ADDED"] = f"{rr[k1]['added_eth_med']:.0f}"
V["R_HEDGED"] = f"{rr[k1]['hedged_eth_med']:.0f}"
V["R_CASH"] = kusd(rr[k1]["cash_med"], False)
V["R_CASH95"] = kusd(rr[k1]["cash_p95"], False)
V["R_NV"] = kusd(nv3[k1]["vs_hodl_mean_usd"])
V["R_NV_MED"] = kusd(nv3[k1]["vs_hodl_med_usd"])
V["R_NV_P5"] = kusd(nv3[k1]["vs_hodl_p5_usd"])
V["R_NV_SELL_MED"] = pct(nv3[k1]["vs_sell_med"], 0)
V["R_22_SELL"] = pct(r22[k1]["vs_sell_mean"], 0)
V["R_22_HODL"] = kusd(r22[k1]["vs_hodl_mean_usd"])
V["R_22_ETH"] = f"{r22[k1]['E_T_med']:.0f}"
V["R_22_CASH"] = kusd(r22[k1]["cash_med"], False)
V["R_22_CASH95"] = kusd(r22[k1]["cash_p95"], False)
V["R_BULL_MED"] = kusd(bl[k1]["vs_hodl_med_usd"])
V["R_BULL_MEAN"] = kusd(bl[k1]["vs_hodl_mean_usd"])
V["R_BULL_P5"] = kusd(bl[k1]["vs_hodl_p5_usd"])
V["R_BULL_CASH95"] = kusd(bl[k1]["cash_p95"], False)
V["R3_RANGE"] = kusd(rr[k3]["vs_hodl_mean_usd"])
V["R3_NV_P5"] = kusd(nv3[k3]["vs_hodl_p5_usd"])
V["R3_CASH95"] = kusd(rr[k3]["cash_p95"], False)
V["R_CLEARED"] = f"{max(rr[k1]['cleared'], nv3[k1]['cleared'], h2[k1]['cleared'])*100:.0f}%"
V["R_ET_MED"] = f"{rr[k1]['E_T_med']:.0f}"
V["R_IV_RICH"] = kusd(rr["iv_rich"]["vs_hodl_mean_usd"])
V["R_IV_CHEAP"] = kusd(rr["iv_cheap"]["vs_hodl_mean_usd"])
V["R_USD4"] = kusd(rr["usd4"]["vs_hodl_mean_usd"])

# ---- version 4: weekly covered calls, 2 ETH a week ----
WK = json.load(open(os.path.join(ROOT, "results", "weekly.json")))
chain = json.load(open(os.path.join(ROOT, "data", "weekly_chain_snapshot.json")))
wq = []
for kstr, lab in [("2000", "ATM, $2,000"), ("2100", "+5%, $2,100"), ("2200", "+10%, $2,200")]:
    o = next(x for x in chain if x["instrument_name"] == f"ETH-20NOV26-{kstr}-C")
    u = o["underlying_price"]
    net = (o["bid_price"] - min(0.0003, 0.125 * o["bid_price"])) * u
    wq.append(f"<tr><th scope='row'>{lab}</th><td>{o['bid_price']:.4f} / {o['ask_price']:.4f}</td><td>{o['mark_iv']:.1f}%</td>"
              f"<td>${net:,.1f}</td><td>{net/u*100:.2f}%</td><td>${2*net:,.0f}</td></tr>")
V["WQ_ROWS"] = "\n".join(wq)
WCOLS = [("2 ETH/wk, ATM", "2 ETH/wk, ATM"), ("2 ETH/wk, +5%", "2 ETH/wk, +5%"), ("2 ETH/wk, +10%", "2 ETH/wk, +10%"),
         ("2 ETH/wk, ATM, strike ≥ $2,000", "2 ETH/wk, ATM, never below $2,000"),
         ("5 ETH/wk, +5%, strike ≥ $2,000", "5 ETH/wk, +5%, never below $2,000"),
         ("Sell 2 ETH/wk, no options", "Sell 2 ETH/wk, no options")]
V["WK_HEAD"] = "<th scope='col'>Keep 50 wstETH</th>" + "".join(f"<th scope='col'>{html.escape(l)}</th>" for _, l in WCOLS)
wrows = []
wblocks = [(k, v, v["desc"]) for k, v in WK["mc"].items()]
wblocks += [("History, 1-year windows", WK["hist"]["52"], f"{WK['hist']['52']['n']:,} daily starts, Mar 2021 – Nov 2025"),
            ("History, 2-year windows", WK["hist"]["104"], f"{WK['hist']['104']['n']:,} daily starts, Mar 2021 – Nov 2024")]
for name, blk, desc in wblocks:
    h = blk["hodl"]
    cells = [hm_cell(h["mean"], h["median"], h["p5"], 80)]
    for k, _ in WCOLS:
        x = blk[k]
        cells.append(hm_cell(x["mean"], x["median"], x["p5"], 80))
    wrows.append(f"<tr><th scope='row'>{html.escape(name)}<span class='sm'>{html.escape(desc)}</span></th>{''.join(cells)}</tr>")
V["WK_ROWS"] = "\n".join(wrows)

spd = []
for k, l in WCOLS:
    nv_, rg_ = WK["mc"]["No view (random walk)"][k], WK["mc"]["Range around $2,000"][k]
    def wk_(x):
        return "—" if x["weeks_med"] is None or x["out_2y"] < 0.5 else f"{x['weeks_med']:.0f}"
    spd.append(f"<tr><th scope='row'>{html.escape(l)}</th>"
               f"<td>{wk_(nv_)} / {wk_(rg_)}</td>"
               f"<td>{nv_['out_1y']*100:.0f}% / {rg_['out_1y']*100:.0f}%</td><td>{nv_['out_2y']*100:.0f}% / {rg_['out_2y']*100:.0f}%</td>"
               f"<td>{nv_['eth_left_med']:.0f} / {rg_['eth_left_med']:.0f}</td>"
               f"<td>{px(P0*(1+nv_['avg_strike_rel_med']/100))} / {px(P0*(1+rg_['avg_strike_rel_med']/100))}</td>"
               f"<td>{usd(nv_['premium_usd'])}</td><td class='{'' if abs(nv_['edge_usd']) < 0.5 else ('pos' if nv_['edge_usd'] > 0 else 'neg')}'>{'—' if abs(nv_['edge_usd']) < 0.5 else usd(nv_['edge_usd'], True)}</td></tr>")
V["WK_SPEED"] = "\n".join(spd)

ce = []
for m, lab in [("0.0", "ATM"), ("0.05", "+5%"), ("0.1", "+10%")]:
    e_ = WK["call_edge"][m]
    ce.append(f"<tr><th scope='row'>{lab}</th><td>{e_['premium_pct']:.2f}%</td><td>{e_['net_pct']:.2f}%</td><td>{e_['payout_pct']:.2f}%</td>"
              f"<td>{e_['itm']*100:.0f}%</td><td class='{'pos' if e_['edge_pct_of_premium'] > 0 else 'neg'}'>{e_['edge_pct_of_premium']:+.1f}%</td></tr>")
V["WK_EDGE"] = "\n".join(ce)

mcw = WK["mc"]
g = lambda sc, k, f: mcw[sc][k][f]
V["WK_ATM_WEEKS"] = f"{g('No view (random walk)', '2 ETH/wk, ATM', 'weeks_med'):.0f}"
V["WK_10_LEFT"] = f"{g('No view (random walk)', '2 ETH/wk, +10%', 'eth_left_med'):.0f}"
V["WK_5_OUT2"] = f"{g('No view (random walk)', '2 ETH/wk, +5%', 'out_2y')*100:.0f}%"
V["WK_DIP_ATM"] = pct(g("Dips to ~$1,200, back at $2,000", "2 ETH/wk, ATM", "mean"), 0)
V["WK_DIP_FLOOR"] = pct(g("Dips to ~$1,200, back at $2,000", "2 ETH/wk, ATM, strike ≥ $2,000", "mean"), 0)
V["WK_DIP_FLOOR5"] = pct(g("Dips to ~$1,200, back at $2,000", "5 ETH/wk, +5%, strike ≥ $2,000", "mean"), 0)
V["WK_RG_FLOOR"] = pct(g("Range around $2,000", "2 ETH/wk, ATM, strike ≥ $2,000", "mean"), 0)
V["WK_RG_FLOOR5"] = pct(g("Range around $2,000", "5 ETH/wk, +5%, strike ≥ $2,000", "mean"), 0)
V["WK_RG_FLOOR5_P5"] = pct(g("Range around $2,000", "5 ETH/wk, +5%, strike ≥ $2,000", "p5"), 1)
V["WK_22"] = pct(g("2022 replay: −60%, no recovery", "2 ETH/wk, ATM, strike ≥ $2,000", "mean"), 0)
V["WK_22_HODL"] = pct(mcw["2022 replay: −60%, no recovery"]["hodl"]["mean"], 0)
V["WK_22_LEFT"] = f"{g('2022 replay: −60%, no recovery', '2 ETH/wk, ATM, strike ≥ $2,000', 'eth_left_med'):.0f}"
V["WK_22_ATM"] = pct(g("2022 replay: −60%, no recovery", "2 ETH/wk, ATM", "mean"), 0)
V["WK_BULL_FLOOR"] = pct(g("Bull: doubles in 2 years", "2 ETH/wk, ATM, strike ≥ $2,000", "mean"), 0)
V["WK_BULL_HODL"] = pct(mcw["Bull: doubles in 2 years"]["hodl"]["mean"], 0)
V["WK_NV_FLOOR"] = pct(g("No view (random walk)", "2 ETH/wk, ATM, strike ≥ $2,000", "mean"), 0)
V["WK_NV_FLOOR_MED"] = pct(g("No view (random walk)", "2 ETH/wk, ATM, strike ≥ $2,000", "median"), 0)
V["WK_NV_FLOOR_P5"] = pct(g("No view (random walk)", "2 ETH/wk, ATM, strike ≥ $2,000", "p5"), 0)
V["WK_NV_FLOOR_OUT2"] = f"{g('No view (random walk)', '2 ETH/wk, ATM, strike ≥ $2,000', 'out_2y')*100:.0f}%"
V["WK_NV_FLOOR5_OUT1"] = f"{g('No view (random walk)', '5 ETH/wk, +5%, strike ≥ $2,000', 'out_1y')*100:.0f}%"
V["WK_RG_FLOOR5_OUT1"] = f"{g('Range around $2,000', '5 ETH/wk, +5%, strike ≥ $2,000', 'out_1y')*100:.0f}%"
V["WK_RG_FLOOR5_OUT2"] = f"{g('Range around $2,000', '5 ETH/wk, +5%, strike ≥ $2,000', 'out_2y')*100:.0f}%"
V["WK_NV_FLOOR_PX"] = px(P0 * (1 + g("No view (random walk)", "2 ETH/wk, ATM, strike ≥ $2,000", "avg_strike_rel_med") / 100))
V["WK_NV_ATM_PX"] = px(P0 * (1 + g("No view (random walk)", "2 ETH/wk, ATM", "avg_strike_rel_med") / 100))
V["WK_ATM_PREM"] = usd(g("No view (random walk)", "2 ETH/wk, ATM", "premium_usd"))
V["WK_ATM_EDGE"] = usd(g("No view (random walk)", "2 ETH/wk, ATM", "edge_usd"), True)
ce0 = WK["call_edge"]
V["WK_E0"] = f"{ce0['0.0']['edge_pct_of_premium']:+.1f}%"
V["WK_E5"] = f"{ce0['0.05']['edge_pct_of_premium']:+.1f}%"
V["WK_E10"] = f"{ce0['0.1']['edge_pct_of_premium']:+.1f}%"
V["WK_H2_FLOOR"] = pct(WK["hist"]["104"]["2 ETH/wk, ATM, strike ≥ $2,000"]["median"], 0)
V["WK_H2_HODL"] = pct(WK["hist"]["104"]["hodl"]["median"], 0)

# ---- version 5: yearly ATM calls on 50 ETH that are never sold ----
AN = json.load(open(os.path.join(ROOT, "results", "annual.json")))
L4 = AN["last4"]
yr_rows = []
for d0, d1, r in zip(L4["dates"][:-1], L4["dates"][1:], L4["0.26"]["rows"]):
    yr_rows.append(f"<tr><th scope='row'>{pd.Timestamp(d0).strftime('%b %Y')} → {pd.Timestamp(d1).strftime('%b %Y')}</th>"
                   f"<td>{px(r['strike'])}</td><td>{px(r['expiry_price'])}</td><td class='pos'>{usd(r['premium'], True)}</td>"
                   f"<td class='{'neg' if r['payout'] > 0 else ''}'>{usd(-r['payout'], True) if r['payout'] > 0 else '$0'}</td>"
                   f"<td class='{'pos' if r['net'] > 0 else 'neg'}'><strong>{usd(r['net'], True)}</strong></td>"
                   f"<td class='{'pos' if r['cash'] > 0 else 'neg'}'>{usd(r['cash'], True)}</td><td>{r['eth_balance']:.1f}</td></tr>")
V["AN_ROWS"] = "\n".join(yr_rows)
t26, t20 = L4["0.26"]["total"], L4["0.2"]["total"]
V["AN_HODL"] = usd(t26["hodl"])
V["AN_FINAL"] = usd(t26["final"])
V["AN_DIFF"] = usd(t26["diff"], True)
V["AN_DIFF_PCT"] = pct(t26["diff"] / t26["hodl"] * 100, 0)
V["AN_MINCASH"] = usd(t26["min_cash"])
V["AN_ETH_FINAL"] = f"{t26['eth_final']:.1f}"
V["AN_ETH_VAL"] = usd(t26["eth_final_value"])
V["AN_ETH_MIN"] = f"{min(r['eth_balance'] for r in L4['0.26']['rows']):.1f}"
V["AN20_DIFF"] = usd(t20["diff"], True)
V["AN20_DIFF_PCT"] = pct(t20["diff"] / t20["hodl"] * 100, 0)
V["AN_START_PX"] = px(L4["prices"][0])
V["AN_END_PX"] = px(L4["prices"][-1])
V["AN_SCALE_DIFF"] = usd(t26["diff"] * Q0 / 50, True)
wr = []
for key, lab in [("0.26", "26% premium"), ("0.2", "20% premium")]:
    w = AN["windows"][key]
    by = w["by_start_year"]
    wr.append(f"<tr><th scope='row'>{lab}</th><td>{w['beat_hodl']*100:.0f}%</td><td>{pct(w['diff_pct_hodl_median'], 0)}</td>"
              f"<td class='neg'>{pct(w['diff_pct_hodl_p10'], 0)}</td><td>{pct(w['diff_pct_hodl_p90'], 0)}</td>"
              f"<td>{pct(by.get('2020', 0), 0)} / {pct(by.get('2021', 0), 0)} / {pct(by.get('2022', 0), 0)}</td>"
              f"<td>{w['eth_beat_hodl']*100:.0f}%</td></tr>")
V["AN_WIN_ROWS"] = "\n".join(wr)
w26 = AN["windows"]["0.26"]
V["AN_N"] = f"{w26['n']:,}"
V["AN_BEAT"] = f"{w26['beat_hodl']*100:.0f}%"
V["AN_MED"] = pct(w26["diff_pct_hodl_median"], 0)
V["AN_P10"] = pct(w26["diff_pct_hodl_p10"], 0)
V["AN_2020"] = pct(w26["by_start_year"]["2020"], 0)
V["AN_2022"] = pct(w26["by_start_year"]["2022"], 0)
V["AN_CASH_MED"] = f"{-w26['min_cash_pct_median']:.0f}%"
V["AN20_BEAT"] = f"{AN['windows']['0.2']['beat_hodl']*100:.0f}%"
V["AN_ITM3"] = f"{w26['itm_years_dist'].get('3', w26['itm_years_dist'].get(3, 0))*100:.0f}%"

# ---- chart data ----
data = dict(
    strip=strip,
    ts=dict(date=ts["date"].tolist(), price=[round(x, 2) for x in ts["price"]],
            A=[round(x, 2) for x in ts["A. Call-only exit"]], B=[round(x, 2) for x in ts["B. Your plan, adds up to 2x"]],
            dA=ts["A. Call-only exit|days"].tolist(), dB=ts["B. Your plan, adds up to 2x|days"].tolist()),
    ann=dict(date=AN["windows"]["0.26"]["series"]["start"], price=AN["windows"]["0.26"]["series"]["s0"],
             d26=AN["windows"]["0.26"]["series"]["diff_pct_hodl"], d20=AN["windows"]["0.2"]["series"]["diff_pct_hodl"],
             itm=AN["windows"]["0.26"]["series"]["itm_years"], send=AN["windows"]["0.26"]["series"]["s_end"]),
    rng=dict(date=RG["hist_ts"]["date"], price=RG["hist_ts"]["price"],
             r1=[round(v * POS / 100 / 1000, 2) for v in RG["hist_ts"][k1 + "|vs_hodl"]],
             r3=[round(v * POS / 100 / 1000, 2) for v in RG["hist_ts"][k3 + "|vs_hodl"]],
             hodl=RG["hist_ts"]["hodl"], s1=RG["hist_ts"][k1]),
    plan=dict(date=h1["ts"]["date"], price=h1["ts"]["price"], hodl=h1["ts"]["hodl"], plan=h1["ts"][pk],
              fix=h1["ts"][fx], planW=h1["ts"][pk + "|maxW"], fixW=h1["ts"][fx + "|maxW"]),
)
out = TPL.replace("__DATA__", json.dumps(data, separators=(",", ":")))
for k, v in V.items():
    out = out.replace("%%" + k + "%%", v)
missing = [m for m in set(__import__("re").findall(r"%%([A-Z0-9_]+)%%", out))]
if missing:
    raise SystemExit(f"unfilled placeholders: {missing}")
path = os.path.join(ROOT, "results", "report.html")
open(path, "w").write(out)
print("wrote", path, f"{len(out)/1024:.0f} KB")
