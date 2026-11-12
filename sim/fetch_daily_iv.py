"""
Download, for every day, the real implied vol of next-day-expiry ETH options traded around the 08:00 UTC
settlement (Deribit history API), plus hourly ETH-PERPETUAL bars. Writes:
  data/daily_atm_iv.csv     date, n_trades, atm_iv (median trade IV, strikes within 2% of index), call_iv, put_iv,
                            wing_put_iv / wing_call_iv (strikes 3-8% OTM), spread_frac (median |price-mark|/mark)
  data/eth_perp_hourly.csv  timestamp (UTC), open, high, low, close
Resumable: skips dates already in the CSV.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import os
import sys
import time
import urllib.request

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = "https://history.deribit.com/api/v2/public/"
LIVE = "https://www.deribit.com/api/v2/public/"


def get(base, path, tries=4):
    for k in range(tries):
        try:
            with urllib.request.urlopen(base + path, timeout=30) as r:
                return json.load(r)["result"]
        except Exception:
            time.sleep(1.5 * (k + 1))
    raise RuntimeError(path)


def tag(d):
    return f"{d.day}{d.strftime('%b').upper()}{d.strftime('%y')}"


def fetch_iv(start="2021-03-24", end=None, path=None):
    path = path or os.path.join(ROOT, "data", "daily_atm_iv.csv")
    done = set()
    if os.path.exists(path):
        with open(path) as fh:
            done = {r["date"] for r in csv.DictReader(fh)}
    end = end or dt.date.today().isoformat()
    d = dt.date.fromisoformat(start)
    new = not os.path.exists(path)
    with open(path, "a", newline="") as fh:
        w = csv.writer(fh)
        if new:
            w.writerow(["date", "n_trades", "index", "atm_iv", "call_iv", "put_iv", "wing_put_iv", "wing_call_iv", "spread_frac"])
        while d.isoformat() <= end:
            if d.isoformat() in done:
                d += dt.timedelta(days=1)
                continue
            t0 = int(dt.datetime(d.year, d.month, d.day, 8, 0, tzinfo=dt.UTC).timestamp() * 1000)
            t1 = t0 + 90 * 60 * 1000
            try:
                r = get(HIST, f"get_last_trades_by_currency_and_time?currency=ETH&kind=option&start_timestamp={t0}"
                              f"&end_timestamp={t1}&count=1000&sorting=asc")
            except RuntimeError:
                d += dt.timedelta(days=1)
                continue
            nt = tag(d + dt.timedelta(days=1))
            tr = [x for x in r["trades"] if x["instrument_name"].split("-")[1] == nt and x.get("iv")]
            row = [d.isoformat(), len(tr), "", "", "", "", "", "", ""]
            if tr:
                idx = float(np.median([x["index_price"] for x in tr]))
                m = lambda x: float(x["instrument_name"].split("-")[2]) / x["index_price"] - 1
                atm = [x["iv"] for x in tr if abs(m(x)) <= 0.02]
                c = [x["iv"] for x in tr if abs(m(x)) <= 0.02 and x["instrument_name"].endswith("-C")]
                p = [x["iv"] for x in tr if abs(m(x)) <= 0.02 and x["instrument_name"].endswith("-P")]
                wp = [x["iv"] for x in tr if -0.08 <= m(x) <= -0.03 and x["instrument_name"].endswith("-P")]
                wc = [x["iv"] for x in tr if 0.03 <= m(x) <= 0.08 and x["instrument_name"].endswith("-C")]
                sp = [abs(x["price"] - x["mark_price"]) / x["mark_price"] for x in tr if x.get("mark_price")]
                med = lambda a: f"{np.median(a):.2f}" if a else ""
                row = [d.isoformat(), len(tr), f"{idx:.2f}", med(atm), med(c), med(p), med(wp), med(wc), med(sp)]
            w.writerow(row)
            fh.flush()
            d += dt.timedelta(days=1)
            time.sleep(0.15)


def fetch_hourly(start="2021-03-20"):
    path = os.path.join(ROOT, "data", "eth_perp_hourly.csv")
    t0 = int(dt.datetime.fromisoformat(start).replace(tzinfo=dt.UTC).timestamp() * 1000)
    now = int(time.time() * 1000)
    rows = {}
    while t0 < now:
        t1 = min(t0 + 40 * 86400 * 1000, now)
        r = get(LIVE, f"get_tradingview_chart_data?instrument_name=ETH-PERPETUAL&start_timestamp={t0}&end_timestamp={t1}&resolution=60")
        for t, o, h, l, c in zip(r["ticks"], r["open"], r["high"], r["low"], r["close"]):
            rows[t] = (o, h, l, c)
        t0 = t1
        time.sleep(0.2)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "open", "high", "low", "close"])
        for t in sorted(rows):
            w.writerow([dt.datetime.fromtimestamp(t / 1000, dt.UTC).strftime("%Y-%m-%d %H:%M"), *rows[t]])
    print("hourly bars", len(rows))


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("hourly", "all"):
        fetch_hourly()
    if what in ("iv", "all"):
        fetch_iv()
    if what == "iv_range":                      # python3 sim/fetch_daily_iv.py iv_range START END OUTFILE
        fetch_iv(sys.argv[2], sys.argv[3], sys.argv[4])
