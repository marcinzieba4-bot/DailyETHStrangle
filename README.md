# DailyETHStrangle

Simulator for exiting a **50 wstETH** position by selling Deribit **daily ATM options**, hedging with **ETH-PERP**
on up days and re-taking exposure on down days — compared against **selling everything today**.

## Strategy rules simulated (one period = one 08:00 UTC daily expiry)

| Variant | Daily rule |
|---|---|
| **A** calls only | Sell ATM call on the unhedged ETH. Up day → short that much ETH-PERP. No puts. Stop when flat. |
| **A + stop** | As A, and sell everything if a settlement is ≥5% below the start price. |
| **B** your plan, adds to 2× | Sell ATM call (covered) + ATM put. Up → add perp short. Down → cover perp, or buy more wstETH (cap 2× position). |
| **C** your plan, no cap | As B, but keeps adding wstETH on every down day. |
| **D** 25% ladder | As B with 25% lots and no adding above 1×. |
| **Wheel** | Never stops; measured after 90 days. |

Economics: an ITM call + perp short at settlement = selling at the strike; an ITM put + buying at settlement = buying at the strike.

## Headline results (snapshot 12 Nov 2026, ETH $2,009, 50 wstETH = 62.45 ETH ≈ $125.5k)

* Daily ATM straddles 2021–2026: premium 2.79%/day, realised move 2.61%/day; after bid-side execution and fees the
  edge is ~+0.02%/day (~1% of premium). **No real edge** — outcomes are driven by the ETH you keep while waiting.
* Historical (1,880 start days): **A** beats selling 76% of the time, median +1.1%, worst 5% −6.7%, worst 1% −17%.
  **B** median +2.4%, worst 5% −19%, worst −69% (June 2022). **C** worst 1% −231% (loses more than the position).
* Starting right after a >5% 4-day drop (as on 12 Nov 2026) roughly doubles the tail (A worst 5%: −12% vs −6%).
* Conclusion: to be out of ETH, sell today. If you want to be paid to wait, use A with a stop; never sell the put leg while exiting.

## The dip-buying plan (`run_accumulate.py`)

Every day sell an ATM put and an ATM call of `lot` ETH. Put assigned → keep the extra wstETH (no cap).
Call assigned → hedge that much with an ETH-PERP short (wstETH keeps staking). Done when fully hedged.
One-year Monte Carlo regimes plus 1- and 2-year historical windows (2021–2026).

* ETH has not mean-reverted since 2020: lag-1 autocorrelation ≈ 0, variance ratios 1.10 (10d) to 1.51 (250d).
* 6.25 ETH/day, no view: mean +3.4%/yr vs selling today (−1.1% if spare cash could earn 4%), median +12%,
  worst 5% −108%. Options add ~0.7%, staking ~2.6%; the rest is ETH price × a position that grows in sell-offs.
  Needs $122k extra USD (median), $336k (worst 5%).
* History 1-year windows: beat selling 68%, median +20%, worst 5% −45%. 2022-style slide: −124% of the position.
* The rule sells on every up day regardless of price; selling calls only ≥ $2,000 and capping at 125 ETH fixes
  the "sells the rebound cheaply" problem (history 1-year median +39%, but worst 5% −104%).

## 1 ETH a day with a 50 wstETH floor below $2,000 (`run_range.py`)

Same daily put + call, 1 ETH each. Below the start price (~$2,000) calls are only sold on ETH bought above the
50 wstETH base, so exposure never drops below it there. Two-year horizon.

* Range around $2,000: +$10.8k over keeping the 50 wstETH (beats holding in 93% of paths); wanders and returns
  to $2,000: +$20k; real 2-year windows 2021–2024: +$25k average, 94% beat holding.
* Premium collected ≈ $28k, paid back ≈ $27.7k: option edge ≈ $0.4k. The gain is the range effect (more ETH after
  drops, less after rises) plus ~$2k of staking/funding.
* Still long ETH, never fully out in 2 years. 2022-style slide: −40% vs selling, −$5k vs holding.
  Cash reserve ≈ $35–40k per 1 ETH/day (up to $75–125k in a bad stretch).

## Weekly covered calls, 2 ETH a week (`run_weekly.py`)

Each week sell a weekly call on 2 ETH; short 2 ETH-PERP only if it expires in the money. No puts. Two-year horizon.

* Slow: ATM strikes take ~65 weeks to be fully out; +5% strikes are out within 2 years in only ~4% of paths;
  +10% leaves ~46 ETH after 2 years.
* Weekly calls since 2021 kept ≈0% of premium after costs (ATM −0.1%, +5% −2.9%, +10% +0.1%).
* Without a floor the strike follows the price down and you sell into dips (dip to $1,200 and back: −19% vs selling).
  With strike = max(ATM, $2,000): +13% in that path, +15% in a range, but in a 2022-style slide nothing sells (−38%, like holding).

## Yearly ATM calls on 50 ETH that are never sold (`run_annual.py`)

Each year sell a 1-year ATM call on 50 ETH for 26% of spot, cash-settle at expiry, roll.

* Nov 2022 → Nov 2026: +$22.6k vs holding 50 ETH (+22%), but −$62k of cumulative call losses to fund by Nov 2024.
  At 20% premium: −$7.8k.
* All 1,000 four-year windows since Feb 2020: beats holding 42% of the time (20% premium: 21%); median −7% of
  holding value; 2020 starts −83%.
* Today the longest Deribit call (Sep 2027) pays 19.5% of spot (IV ~54%), so a full year is ~21–22%, not 26%.

## Monthly ladder of yearly calls (`run_ladder.py`)

Every month sell a 1-year ATM call on 50/12 ETH; ITM losses paid in cash, ETH never sold; open calls bought back at the end.

* Nov 2022 → Nov 2026: +$12.0k vs holding at 26% premium (yearly all-at-once: +$22.6k), −$14.4k at 20%.
  Most cash ever needed: $12.5k (yearly: $62k).
* All 4-year windows since 2020: beats holding 44% at 26% (yearly 42%), 11% at 20% (yearly 21%);
  narrower range (worst 10%: −69% vs −94%). ~62% of 1-year ATM calls ended in the money.

## Yearly straddles instead of ETH, laddered monthly, recovery sizing (`run_straddle.py`)

No ETH held; every month sell a 1-year ATM straddle on 50/12 ETH (44% premium). After a losing tranche the next is
sized up: rule A = half of the new premium equals the loss (min 1×); rule B = base + extra tranche whose half-premium covers it.

* Nov 2022 → Nov 2026: plain +$11.3k, A +$13.0k, B +$35.0k (holding 50 ETH: +$39.2k); cash needed at worst $73–87k.
* All 4-year windows since 2020: made money 29% (plain), 28% (A), 33% (B); largest tranche median 4.6× (A) / 6.2× (B), up to 28×.
* ETH moved more than 44% over a year on 59% of start days since 2020. At 50% premium: 56% of windows profitable; at 38%: 0%.

## Yearly puts only, laddered monthly, sized up to cover losses (`run_puts.py`)

No ETH held; every month sell a 1-year ATM put with notional = 1/12 of capital (USD-sized). After a losing tranche:
rule A = whole new premium equals the loss (min 1×); rule B = base + extra tranche whose premium equals the loss.
History extended to Aug 2017 with Bitstamp daily closes (covers the 2018 crash).

* 22% premium, all 4-year windows since Aug 2017: made money 97% (no size-up, median +27%), 100% with rule B
  (median +47%, peak collateral up to 2.3× capital). Deepest fall ~51–60% (Jan 2018 start) before recovering.
* Today's 1-year ATM put is ~18% (Sep 2027 $2,000 put bids 17.2%): median +13% over 4 years, beats 4% cash only 29%;
  rule B median +35% but needs up to 2.8× capital as collateral.
* Beats holding ETH in only 16–28% of windows. Last 4 years at 22%: +31% (plain) / +37% (B) vs holding +62%.

## Put ladder from 2020 with leverage (`run_leverage.py`)

Continuous, compounding account from 1 Jan 2020; monthly 1-year ATM puts on leverage × equity / 12; market-priced premiums
(1y IV = 0.5 × DVOL + 26, realised-vol proxy before Mar 2021, 5%/yr futures basis); Deribit-style margin with liquidation;
puts bought back at 95% profit.

| Version | CAGR | Max drawdown |
|---|---|---|
| Puts 1× cash-secured | +6.5% | −43% |
| 1× + size-up (rule B, cap 3×) + 4% on collateral | **+14.6%** | −42% |
| 1.5× + size-up + yield | +18.7% | −60% |
| 2× + size-up + yield | +21.7% | −78% |
| Puts 3× | liquidated Jun 2022 | −97% |
| Put spreads / 200-day MA filter | 3% or less | −40% to −59% |

`leverage_starts.json`: same variants from 2021, Nov 2021 top, Jun 2022 bottom, Mar 2024.

### + delta hedging (short ETH-PERP against the puts' delta, daily, 2% band, real funding)

| Version (from Jan 2020) | CAGR | Max drawdown |
|---|---|---|
| Puts 1×, fully delta-hedged | −3.0% | −22% |
| Puts 1×, hedged only below 200-day MA | +5.6% | −22% |
| 1× + size-up + 4% yield, hedged below 200-day MA | **+12.5%** | **−21%** |
| 2× + size-up + 4% yield, hedged below 200-day MA | +19.5% | −43% |

Full hedging loses from every start date (the 1-year put premium did not beat ETH's realised volatility);
the trend hedge halves drawdowns but whipsaws in sideways markets (Mar 2024 start: ~0%/yr). `hedge_starts.json`.

## Calls + puts + delta hedge (`run_straddle_hedge.py`)

Monthly ladder of 1-year ATM straddles from Jan 2020, delta-hedged daily with ETH-PERP (real funding), per-leg Deribit margin.

* From Jan 2020: hedged straddles liquidated in Apr 2021 (−64%), unhedged in Nov 2020. The 10× rally inflated the short
  calls, and the long-perp hedge paid 30–37%/yr funding (−58% of capital). Same with options priced +10/+20 vol points.
* ETH realised more than the modelled 1-year implied vol in every year except 2023 (e.g. 2021: 104 vs 84).
* From the Nov 2021 top: −3.5%/yr at model prices; +2.9%/yr (+9.2% with size-up + yield, DD ≈ −12%) if options were 10 vol
  points richer. Today 1-year IV ≈ 54 vs 1-year realised ≈ 58 → no edge now. `straddle_hedge_ivbump.json`.

## Buying daily straddles with a loss-recovery size-up (`run_long_straddle.py`)

Buy a daily ATM straddle on 10% of capital; after losses add an extra size so a 2×-premium move recovers the accumulated
loss; reset when recovered. Real DVOL pricing from Mar 2021, bought at mark + 3.5% + fees.

* Buyer gets back 88% of premiums (cost 2.95%/day vs average move 2.61%); move > premium on 33% of days, > 2× on 9%.
* Fixed size: −69% since Mar 2021 (−12…−16%/yr). Size-up: bets reach 100–230× base; the rule breaks (next bet > cash)
  in Aug–Nov 2021. Monte Carlo 2 years from today: breaks within 1 year in 97% (no cap), within 2 years 99% (cap 10×), 70% (cap 5×).

## Selling daily vol at real prices → weekend calendar (`run_daily_vol.py`, `run_daily_vol_select.py`, `run_weekend_vol.py`)

Real implied vol of Deribit's next-day ETH options traded 08:00–09:30 UTC on 2,056 days (`sim/fetch_daily_iv.py`,
history API) plus hourly ETH-PERP bars. Components chosen on 2021–23, tested on 2024–26, block bootstrap.

* Real daily IV was ~0.87× the DVOL×weekday model used earlier; selling the daily ATM straddle every day lost in 2022–26,
  and hourly delta hedging, IV filters, wings and recovery did not fix it.
* Seller kept +21% of premium on Sat 08:00→Sun 08:00 (positive 4 of 6 years); buyer earned +23% on Sun 08:00→Mon 08:00
  (all 6 years).
* **Sell Sat straddle + buy Sun straddle, 1× each, no hedge**: 2021–23 +53%/yr, 2024–26 (unseen) +32.5%/yr, full +42%/yr,
  max DD −18%; robust to costs (+32% at 5% from mark). Optional recovery (8 Saturdays, cap 1.5×): +46%/yr, DD −21%.
* t-stats ≈2.7–2.8 per leg (~290 trades), Sunday profit concentrated in rare big moves; pattern found by searching weekdays.

## Layout

```
sim/engine.py      vectorised strategy engine (options, perp hedge, carry, costs, exit/income modes)
sim/data.py        Deribit settlement prices, DVOL, funding; live snapshot
sim/scenarios.py   illustrative paths, bootstrap Monte Carlo regimes, historical rolling windows
run_all.py         quick-exit variants -> results/results.json
run_accumulate.py  dip-buying plan     -> results/accumulate.json
run_range.py       1 ETH/day + floor   -> results/range.json
run_weekly.py      weekly calls        -> results/weekly.json
run_annual.py      yearly calls        -> results/annual.json
run_ladder.py      monthly ladder      -> results/ladder.json
run_straddle.py    yearly straddles    -> results/straddle.json
run_puts.py        yearly puts only    -> results/puts.json
run_leverage.py    puts from 2020, lev -> results/leverage.json
run_straddle_hedge.py straddles + hedge -> results/straddle_hedge.json
run_long_straddle.py buy daily straddles -> results/long_straddle.json
run_daily_vol*.py  daily vol selling   -> results/daily_vol.json
run_weekend_vol.py weekend calendar    -> results/weekend_vol*.json
sim/fetch_daily_iv.py  real daily IVs + hourly bars -> data/daily_atm_iv.csv, data/eth_perp_hourly.csv
report/            HTML report builder -> results/report.html
data/              cached market data (Deribit public API)
```

## Run

```bash
pip install numpy pandas scipy
python3 run_all.py --refresh     # re-download data + live quotes, then simulate quick exits
python3 run_accumulate.py        # dip-buying plan
python3 run_range.py             # 1 ETH/day with the 50 wstETH floor
python3 run_weekly.py            # weekly covered calls, 2 ETH a week
python3 run_annual.py            # yearly ATM calls on 50 ETH, last 4 years + all 4-year windows
python3 run_ladder.py            # monthly ladder of yearly calls
python3 run_straddle.py          # laddered yearly straddles with recovery sizing
python3 run_puts.py              # laddered yearly puts with recovery sizing (2017+ windows)
python3 run_leverage.py          # continuous put ladder from 2020 with leverage, yearly returns, drawdown
python3 run_straddle_hedge.py    # straddle ladder + delta hedge from 2020
python3 run_long_straddle.py     # buy daily straddles with loss-recovery size-up (+ Monte Carlo)
python3 sim/fetch_daily_iv.py    # download real daily-option IVs + hourly bars (~40 min)
python3 run_daily_vol_select.py  # selling daily vol every day: staged selection
python3 run_weekend_vol.py       # weekend calendar: sell Sat, buy Sun
python3 report/build_report.py   # rebuild results/report.html
```

Not modelled: taxes, gas, margin calls/liquidation, wstETH discount, exchange risk, intraday moves. Not financial advice.
