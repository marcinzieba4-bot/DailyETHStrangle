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

## Layout

```
sim/engine.py      vectorised strategy engine (options, perp hedge, carry, costs, exit/income modes)
sim/data.py        Deribit settlement prices, DVOL, funding; live snapshot
sim/scenarios.py   illustrative paths, bootstrap Monte Carlo regimes, historical rolling windows
run_all.py         quick-exit variants -> results/results.json
run_accumulate.py  dip-buying plan     -> results/accumulate.json
run_range.py       1 ETH/day + floor   -> results/range.json
run_weekly.py      weekly calls        -> results/weekly.json
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
python3 report/build_report.py   # rebuild results/report.html
```

Not modelled: taxes, gas, margin calls/liquidation, wstETH discount, exchange risk, intraday moves. Not financial advice.
