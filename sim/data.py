"""Market data: Deribit settlement prices (08:00 UTC), DVOL, funding; live snapshot."""
from __future__ import annotations

import datetime as dt
import json
import os
import urllib.request

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
DERIBIT = "https://www.deribit.com/api/v2/public/"
WSTETH = "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"

# Approximate historical Lido stETH APR by year (used only in the historical backtest).
STAKE_APR_BY_YEAR = {2021: 0.045, 2022: 0.045, 2023: 0.040, 2024: 0.032, 2025: 0.028, 2026: 0.025}


def _get(path):
    with urllib.request.urlopen(DERIBIT + path, timeout=30) as r:
        return json.load(r)["result"]


def refresh_history():
    """Re-download settlement prices, DVOL and monthly funding into data/."""
    rows, off = {}, 0
    while True:
        r = _get(f"get_delivery_prices?index_name=eth_usd&offset={off}&count=100")
        for x in r["data"]:
            rows[x["date"]] = x["delivery_price"]
        off += len(r["data"])
        if off >= r["records_total"] or not r["data"]:
            break
    pd.Series(rows, name="price").rename_axis("date").sort_index().to_csv(os.path.join(DATA, "eth_delivery_prices.csv"))

    now = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    start = int(dt.datetime(2021, 3, 1, tzinfo=dt.UTC).timestamp() * 1000)
    out, end = {}, now
    while True:
        r = _get(f"get_volatility_index_data?currency=ETH&start_timestamp={start}&end_timestamp={end}&resolution=1D")
        for t, o, h, l, c in r["data"]:
            out[dt.datetime.fromtimestamp(t / 1000, dt.UTC).date()] = c
        if not r.get("continuation") or not r["data"]:
            break
        end = r["continuation"]
    pd.Series(out, name="dvol").rename_axis("date").sort_index().to_csv(os.path.join(DATA, "eth_dvol.csv"))

    recs = []
    d = dt.datetime(2021, 3, 1, tzinfo=dt.UTC)
    endd = dt.datetime.now(dt.UTC)
    while d < endd:
        nd = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        e = min(nd, endd)
        v = _get(f"get_funding_rate_value?instrument_name=ETH-PERPETUAL"
                 f"&start_timestamp={int(d.timestamp()*1000)}&end_timestamp={int(e.timestamp()*1000)}")
        recs.append((d.date(), v, v / ((e - d).total_seconds() / 86400) * 365))
        d = nd
    pd.DataFrame(recs, columns=["month", "funding_total", "funding_apr"]).to_csv(
        os.path.join(DATA, "eth_funding_monthly.csv"), index=False)


def load_history():
    p = pd.read_csv(os.path.join(DATA, "eth_delivery_prices.csv"), parse_dates=["date"]).set_index("date")["price"]
    v = pd.read_csv(os.path.join(DATA, "eth_dvol.csv"), parse_dates=["date"]).set_index("date")["dvol"]
    f = pd.read_csv(os.path.join(DATA, "eth_funding_monthly.csv"), parse_dates=["month"])
    df = pd.concat([p, v], axis=1).dropna()
    df = df[df.index >= "2021-03-24"].copy()
    df["r"] = np.log(df.price).diff().shift(-1)          # return from this 08:00 to next 08:00
    df["dow"] = df.index.dayofweek
    df["z"] = df.r / (df.dvol / 100 / np.sqrt(365))      # move in units of DVOL-implied daily sigma
    f["key"] = f.month.dt.to_period("M")
    fmap = dict(zip(f.key, f.funding_apr))
    df["fund_apr"] = [fmap.get(d.to_period("M"), 0.0) for d in df.index]
    df["stake_apr"] = [STAKE_APR_BY_YEAR.get(d.year, 0.03) for d in df.index]
    return df


def fetch_live(save=True):
    """Live snapshot: index, perp funding, DVOL, nearest daily ATM options, wstETH ratio, Lido APR."""
    snap = {"ts_utc": dt.datetime.now(dt.UTC).isoformat(timespec="seconds")}
    tk = _get("ticker?instrument_name=ETH-PERPETUAL")
    snap["eth_index"] = tk["index_price"]
    snap["funding_8h"] = tk["funding_8h"]
    now = int(dt.datetime.now(dt.UTC).timestamp() * 1000)
    dv = _get(f"get_volatility_index_data?currency=ETH&start_timestamp={now-3*86400000}&end_timestamp={now}&resolution=3600")
    snap["dvol"] = dv["data"][-1][4]
    book = _get("get_book_summary_by_currency?currency=ETH&kind=option")
    exps = sorted({o["instrument_name"].split("-")[1] for o in book}, key=lambda e: dt.datetime.strptime(e, "%d%b%y"))
    e0 = exps[0]
    legs = [o for o in book if o["instrument_name"].split("-")[1] == e0]
    und = legs[0]["underlying_price"]
    strikes = sorted({float(o["instrument_name"].split("-")[2]) for o in legs}, key=lambda k: abs(k - und))
    snap["daily_expiry"] = e0
    snap["daily_options"] = []
    for k in sorted(strikes[:2]):
        for o in legs:
            if float(o["instrument_name"].split("-")[2]) == k:
                snap["daily_options"].append({x: o.get(x) for x in
                                              ["instrument_name", "bid_price", "ask_price", "mark_price", "mark_iv", "underlying_price"]})
    atm = [o for o in snap["daily_options"]]
    snap["daily_atm_iv"] = float(np.mean([o["mark_iv"] for o in atm]))
    # wstETH -> stETH ratio on-chain
    try:
        req = urllib.request.Request(
            "https://ethereum-rpc.publicnode.com",
            data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call",
                             "params": [{"to": WSTETH, "data": "0x035faf82"}, "latest"]}).encode(),
            headers={"content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as r:
            snap["wsteth_ratio"] = int(json.load(r)["result"], 16) / 1e18
    except Exception:
        snap["wsteth_ratio"] = None
    try:
        with urllib.request.urlopen("https://eth-api.lido.fi/v1/protocol/steth/apr/sma", timeout=20) as r:
            snap["steth_apr"] = json.load(r)["data"]["smaApr"] / 100
    except Exception:
        snap["steth_apr"] = None
    if save:
        with open(os.path.join(DATA, "live_snapshot.json"), "w") as fh:
            json.dump(snap, fh, indent=1)
    return snap


def load_live():
    with open(os.path.join(DATA, "live_snapshot.json")) as fh:
        return json.load(fh)
