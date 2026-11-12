"""Scenario generators: illustrative day-by-day paths, Monte Carlo regimes, historical windows."""
from __future__ import annotations

import numpy as np

# Daily-option IV relative to DVOL by start weekday (Mon=0 ... Sun=6).
# Shape taken from the live Deribit chain (Thu->Fri 1.10x, Fri->Sat 0.97x, Sat->Sun 0.59x,
# Sun->Mon 0.82x of DVOL on 2026-11-12), smoothed so a normal weekday = 1.0x.
DOW_IV = np.array([1.00, 1.00, 1.00, 1.00, 0.92, 0.60, 0.85])

SQ365 = np.sqrt(365.0)


def iv_path(level, start_dow, T, day1_iv=None):
    """Annualised IV used to price each daily option (decimal)."""
    level = np.broadcast_to(np.asarray(level, float), (T,))
    dows = (start_dow + np.arange(T)) % 7
    iv = level / 100.0 * DOW_IV[dows]
    if day1_iv is not None:
        iv = iv.copy()
        iv[0] = day1_iv / 100.0
    return iv


def illustrative(P0, moves_pct):
    """Deterministic price path from a list of daily % moves."""
    return P0 * np.cumprod(np.r_[1.0, 1 + np.asarray(moves_pct, float) / 100.0])


def z_pools(hist):
    """Standardised historical 08:00->08:00 moves (in DVOL-implied daily sigmas), per weekday, demeaned.
    Keeps fat tails, the weekend lull and the historical realised-vs-implied gap."""
    pools = {}
    for d in range(7):
        z = hist.loc[hist.dow == d, "z"].dropna().to_numpy()
        pools[d] = z - z.mean()
    return pools


def mc_paths(pools, P0, start_dow, rv_level, mu_log, n, T, seed=0):
    """
    Bootstrap Monte Carlo.
      rv_level: (T,) the 'DVOL' level that scales realised moves (realised abs move ~0.85x of implied, as in history)
      mu_log  : (T,) deterministic log-drift per day (trend scenarios); a martingale correction is applied so
                mu_log=0 means no drift in price.
    """
    rng = np.random.default_rng(seed)
    rv_level = np.broadcast_to(np.asarray(rv_level, float), (T,))
    mu_log = np.broadcast_to(np.asarray(mu_log, float), (T,))
    r = np.empty((n, T))
    for t in range(T):
        pool = pools[(start_dow + t) % 7]
        sd = rv_level[t] / 100.0 / SQ365
        z = pool[rng.integers(0, len(pool), n)]
        corr = np.log(np.mean(np.exp(pool * sd)))
        r[:, t] = z * sd - corr + mu_log[t]
    return P0 * np.exp(np.concatenate([np.zeros((n, 1)), np.cumsum(r, axis=1)], axis=1))


def mc_scenarios(T):
    """Market regimes for the Monte Carlo. Levels are DVOL-equivalent vol (%)."""
    t = np.arange(T)
    sc = {}
    sc["No view (random walk)"] = dict(rv=np.full(T, 56.0), iv=np.full(T, 56.0), mu=np.zeros(T), fund=0.015,
                                      desc="Zero drift, vol at today's DVOL (56). Historical fat tails & weekend effect.")
    sc["Calm chop"] = dict(rv=np.full(T, 40.0), iv=np.full(T, 42.0), mu=np.zeros(T), fund=0.015,
                           desc="Range-bound, vol falls to ~40.")
    sc["Bull +40% / 90d"] = dict(rv=np.full(T, 56.0), iv=np.full(T, 56.0), mu=np.full(T, np.log(1.4) / 90), fund=0.08,
                                 desc="Steady uptrend (~+40% in 90 days), funding rich (+8% APR to shorts).")
    sc["Bear -40% / 90d"] = dict(rv=np.full(T, 65.0), iv=np.full(T, 65.0), mu=np.full(T, np.log(0.6) / 90), fund=0.0,
                                 desc="Steady downtrend (~-40% in 90 days), vol 65.")
    crash = t < 10
    sc["Crash -35% in 10d, then flat"] = dict(
        rv=np.where(crash, 110.0, 70.0), iv=np.where(crash, 75.0, 70.0),
        mu=np.where(crash, np.log(0.65) / 10, 0.0), fund=0.0,
        desc="Sharp crash: realised vol 110 while daily IV lags at 75, then flat with vol 70.")
    sq = t < 10
    sc["Squeeze +30% in 10d, then flat"] = dict(
        rv=np.where(sq, 90.0, 60.0), iv=np.where(sq, 70.0, 60.0),
        mu=np.where(sq, np.log(1.3) / 10, 0.0), fund=0.12,
        desc="Fast rally: realised vol 90, IV 70, funding +12% APR, then flat.")
    return sc


def hist_windows(hist, T, last_start=None):
    """Rolling start dates -> (S, iv, fund, stake, dates) matrices for the historical backtest."""
    px = hist.price.to_numpy()
    dv = hist.dvol.to_numpy()
    dw = hist.dow.to_numpy()
    fu = hist.fund_apr.to_numpy()
    st = hist.stake_apr.to_numpy()
    n_all = len(px)
    starts = np.arange(0, n_all - T)
    if last_start is not None:
        starts = starts[starts <= last_start]
    idx = starts[:, None] + np.arange(T + 1)[None, :]
    S = px[idx]
    iv = (dv[idx[:, :-1]] / 100.0) * DOW_IV[dw[idx[:, :-1]]]
    fund = fu[idx[:, :-1]]
    stake = st[idx[:, :-1]]
    return S, iv, fund, stake, hist.index[starts]
