"""
Daily ATM option-selling "wheel" on a wstETH position, hedged with ETH perps.

Mechanics (one period = one Deribit daily expiry, 08:00 UTC -> 08:00 UTC):
  * At each 08:00 settlement we sell ATM options (strike = current price):
      - CALL of size c = min(lot, exposure)            (covered: never go net short)
      - PUT  of size p = min(lot, cap - exposure)      (re-entry / "add new wstETH")
  * Next 08:00:
      - Up day  (S1 > K): call is ITM -> we pay (S1-K)*c and SHORT c ETH-PERP at S1
                          == we "sold" c ETH at K. Exposure -= c. wstETH is kept (staking continues).
      - Down day (S1 < K): put is ITM -> we pay (K-S1)*p and re-take p ETH of exposure at S1:
                          first cover perp shorts ("come back"), remainder = buy new wstETH ("add new").
                          == we "bought" p ETH at K. Exposure += p.
  * Exit mode: stop the first time exposure hits 0 (fully hedged = "cleared out of ETH exposure"),
    unwind (sell wstETH, close perp) and hold USD.
  * Income mode: keep running for the whole horizon, mark to market at the end.

All values in USD. Everything is vectorised over paths (axis 0).
"""
from __future__ import annotations

from dataclasses import dataclass, field
import numpy as np
from scipy.special import ndtr

DAY = 1.0 / 365.0


def bs_call_put(S, K, sigma, T=DAY):
    """Black-Scholes (r=0) call and put prices, vectorised."""
    sigma = np.maximum(sigma, 1e-6)
    sd = sigma * np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    call = S * ndtr(d1) - K * ndtr(d2)
    put = call - S + K
    return call, put


@dataclass
class Costs:
    spread_haircut: float = 0.035   # we sell ~3.5% below mark (Deribit daily ATM bid vs mark today)
    opt_fee: float = 0.0003         # Deribit option fee: 0.03% of underlying per contract ...
    opt_fee_cap: float = 0.125      # ... capped at 12.5% of option price
    perp_fee: float = 0.0003        # blended maker/taker on each hedge change
    spot_slip: float = 0.0003       # buying wstETH (USDC->ETH->wstETH) when "adding new"
    exit_cost: float = 0.0005       # final sale of wstETH -> USD (same as selling today)


@dataclass
class Carry:
    stake_apr: float = 0.0233       # Lido stETH 7d SMA APR (live, Nov-2026)
    fund_apr: float = 0.015         # avg ETH-PERP funding received by shorts (last 12m avg ~1.5%)
    usd_apr: float = 0.0            # yield on idle USD (applied to both strategy cash and benchmark)


@dataclass
class Strategy:
    name: str
    lot_frac: float = 1.0           # option lot as fraction of initial ETH exposure Q0
    cap_mult: float | None = 1.0    # max exposure as multiple of Q0 (None = unlimited)
    sell_puts: bool = True
    sell_calls: bool = True
    mode: str = "exit"              # "exit" or "income"
    hold: bool = False              # pure HODL reference (no options)
    stop_frac: float | None = None  # exit mode: sell everything if price closes this far below the start price
    call_floor_rel: float | None = None  # only sell calls when price >= start price * this (e.g. 1.0 = never sell below today's price)


def run(S, iv, Q0, strat: Strategy, costs=Costs(), carry=Carry(), fund=None, stake=None, log=False):
    """
    S    : (n, T+1) settlement prices, S[:,0] = start price
    iv   : (n, T) annualised implied vol used to price the option sold at S[:,t]
    fund : optional (n, T) or (T,) funding APR per day (overrides carry.fund_apr)
    stake: optional (n, T) or (T,) staking APR per day (overrides carry.stake_apr)
    Returns dict of per-path metrics (+ per-day log for n==1 if log=True).
    """
    S = np.atleast_2d(np.asarray(S, float))
    iv = np.atleast_2d(np.asarray(iv, float))
    n, T1 = S.shape
    T = T1 - 1
    lot = strat.lot_frac * Q0
    cap = np.inf if strat.cap_mult is None else strat.cap_mult * Q0
    eps = 1e-9

    W = np.full(n, float(Q0))   # wstETH holdings in ETH-equivalent units
    H = np.zeros(n)             # perp short size (ETH)
    cash = np.zeros(n)
    active = np.ones(n, bool)   # still running (exit mode stops when exposure hits 0)
    tau = np.full(n, -1)        # day index when cleared out
    S_tau = np.full(n, np.nan)
    V_tau = np.full(n, np.nan)
    maxE = np.full(n, float(Q0))
    prem_tot = np.zeros(n)
    n_up = np.zeros(n, int)
    n_dn = np.zeros(n, int)
    bench0 = Q0 * S[:, 0] * (1 - costs.exit_cost)
    min_rel = np.zeros(n)       # worst (V - benchmark) along the path
    stopped = np.zeros(n, bool) # exit forced by the stop-loss
    min_cash = np.zeros(n)      # most USD ever drawn (negative) -> dry powder needed to fund put assignments
    pay_tot = np.zeros(n)       # option payouts
    stake_tot = np.zeros(n)
    fund_tot = np.zeros(n)
    cost_tot = np.zeros(n)      # perp fees + spot slippage + final unwind
    maxW = np.full(n, float(Q0))
    rows = []

    def fund_rate(t):
        if fund is None:
            return carry.fund_apr
        f = np.asarray(fund)
        return f[:, t] if f.ndim == 2 else f[t]

    def stake_rate(t):
        if stake is None:
            return carry.stake_apr
        a = np.asarray(stake)
        return a[:, t] if a.ndim == 2 else a[t]

    for t in range(T):
        S0, S1 = S[:, t], S[:, t + 1]
        E = W - H
        K = S0
        if strat.hold:
            c = p = np.zeros(n)
        else:
            c = np.where(active & strat.sell_calls, np.clip(np.minimum(lot, E), 0, None), 0.0)
            if strat.call_floor_rel is not None:
                c = np.where(S0 >= S[:, 0] * strat.call_floor_rel, c, 0.0)
            p = np.where(active & strat.sell_puts, np.clip(np.minimum(lot, cap - E), 0, None), 0.0)
        call, put = bs_call_put(S0, K, iv[:, t])
        net_c = call * (1 - costs.spread_haircut) - np.minimum(costs.opt_fee * S0, costs.opt_fee_cap * call)
        net_p = put * (1 - costs.spread_haircut) - np.minimum(costs.opt_fee * S0, costs.opt_fee_cap * put)
        prem = c * net_c + p * net_p
        cash += prem
        prem_tot += prem

        # carry over the day (staking on wstETH, funding on perp shorts, USD yield)
        on = active | strat.hold
        stk = np.where(on, W * S0 * stake_rate(t) * DAY, 0.0)
        fnd = np.where(on, H * S0 * fund_rate(t) * DAY, 0.0)
        cash += stk + fnd
        stake_tot += stk
        fund_tot += fnd
        cash += cash * carry.usd_apr * DAY
        # perp mark-to-market (short)
        cash -= H * (S1 - S0)

        # settlement
        up = S1 > K
        dn = S1 < K
        pay = c * np.maximum(S1 - K, 0) + p * np.maximum(K - S1, 0)
        cash -= pay
        pay_tot += pay
        # up day: hedge c more ETH with perp short
        dH_up = np.where(up, c, 0.0)
        H += dH_up
        cash -= dH_up * S1 * costs.perp_fee
        cost_tot += dH_up * S1 * costs.perp_fee
        # down day: take p back -> cover perp first, then buy new wstETH
        take = np.where(dn, p, 0.0)
        cover = np.minimum(take, H)
        H -= cover
        cash -= cover * S1 * costs.perp_fee
        buy = take - cover
        W += buy
        cash -= buy * S1 * (1 + costs.spot_slip)
        cost_tot += cover * S1 * costs.perp_fee + buy * S1 * costs.spot_slip
        maxW = np.maximum(maxW, W)
        min_cash = np.minimum(min_cash, cash)
        n_up += (up & (c > 0)).astype(int)
        n_dn += (dn & (p > 0)).astype(int)

        E = W - H
        maxE = np.maximum(maxE, E)
        V = W * S1 + cash
        min_rel = np.minimum(min_rel, V - bench0)

        if log:
            rows.append(dict(day=t + 1, S0=float(S0[0]), S1=float(S1[0]), K=float(K[0]), iv=float(iv[0, t]),
                             call_sz=float(c[0]), put_sz=float(p[0]), premium=float(prem[0]),
                             move="UP" if up[0] else ("DOWN" if dn[0] else "FLAT"),
                             wst_eth=float(W[0]), perp_short=float(H[0]), exposure=float(E[0]),
                             value=float(V[0]), vs_sell_today=float(V[0] - bench0[0])))

        if strat.mode == "exit" and not strat.hold:
            done = active & (E <= eps)
            if strat.stop_frac is not None:
                hit = active & (S1 <= S[:, 0] * (1 - strat.stop_frac))
                stopped |= hit & ~done
                done |= hit
            if done.any():
                # unwind: sell wstETH, close perp (delta-neutral -> value locked)
                unwind = W * S1 * costs.exit_cost + H * S1 * costs.perp_fee
                cost_tot += np.where(done, unwind, 0.0)
                val = W * S1 + cash - unwind
                tau = np.where(done, t + 1, tau)
                S_tau = np.where(done, S1, S_tau)
                V_tau = np.where(done, val, V_tau)
                cash = np.where(done, val, cash)
                W = np.where(done, 0.0, W)
                H = np.where(done, 0.0, H)
                active &= ~done

    S_T = S[:, -1]
    unwind_T = W * S_T * costs.exit_cost + H * S_T * costs.perp_fee
    cost_tot += unwind_T
    V_T = W * S_T + cash - unwind_T  # liquidation value
    benchT = bench0 * (1 + carry.usd_apr * DAY) ** T
    out = dict(V_T=V_T, bench=benchT, rel=V_T - benchT, rel_pct=V_T / benchT - 1,
               min_cash=min_cash, maxW=maxW, W_T=W, H_T=H, S_T=S_T,
               pnl=dict(premium=prem_tot, payout=pay_tot, staking=stake_tot, funding=fund_tot, costs=cost_tot),
               cleared=tau > 0, tau=tau, S_tau=S_tau, V_tau=V_tau,
               exit_px=np.where(tau > 0, V_tau, V_T) / Q0,
               maxE=maxE, E_T=W - H, stopped=stopped, prem=prem_tot, n_up=n_up, n_dn=n_dn, min_rel=min_rel)
    if log:
        out["log"] = rows
    return out
