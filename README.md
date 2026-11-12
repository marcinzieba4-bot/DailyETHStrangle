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

## Layout

```
sim/engine.py      vectorised strategy engine (options, perp hedge, carry, costs, exit/income modes)
sim/data.py        Deribit settlement prices, DVOL, funding; live snapshot
sim/scenarios.py   illustrative paths, bootstrap Monte Carlo regimes, historical rolling windows
run_all.py         runs everything -> results/results.json
report/            HTML report builder -> results/report.html
data/              cached market data (Deribit public API)
```

## Run

```bash
pip install numpy pandas scipy
python3 run_all.py --refresh     # re-download data + live quotes, then simulate
python3 report/build_report.py   # rebuild results/report.html
```

Not modelled: taxes, gas, margin calls/liquidation, wstETH discount, exchange risk, intraday moves. Not financial advice.
