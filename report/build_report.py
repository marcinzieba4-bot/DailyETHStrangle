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

# ---- chart data ----
data = dict(
    strip=strip,
    ts=dict(date=ts["date"].tolist(), price=[round(x, 2) for x in ts["price"]],
            A=[round(x, 2) for x in ts["A. Call-only exit"]], B=[round(x, 2) for x in ts["B. Your plan, adds up to 2x"]],
            dA=ts["A. Call-only exit|days"].tolist(), dB=ts["B. Your plan, adds up to 2x|days"].tolist()),
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
