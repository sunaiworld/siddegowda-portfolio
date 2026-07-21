# Data Schema Documentation

This document describes the schema layouts and data definitions of the transaction logs and analysis files.

---

## 1. Master Ledger Schema (`data/trade_log.csv`)

The transaction log is an append-only ledger composed of 17 columns:

| Column Name | Data Type | Description / Constraints | Example |
| :--- | :--- | :--- | :--- |
| **trade_id** | String | Auto-incrementing chronological ID (`TRD0000x`) | `TRD00001` |
| **date** | String | Normalised date in `DD-MM-YYYY` format | `15-01-2025` |
| **symbol** | String | Ticker symbol (e.g. NSE equity name) | `HDFCBANK` |
| **exchange** | String | Market exchange | `NSE` |
| **segment** | String | Product segment | `EQ` |
| **action** | String | Transaction type: `BUY` or `SELL` | `BUY` |
| **quantity** | Integer | Number of units purchased or sold | `10` |
| **price** | Float | Purchase or selling price per share | `1420.00` |
| **gross_amount** | Float | Quantity * price | `14200.00` |
| **brokerage** | Float | Brokerage fees paid | `20.00` |
| **taxes_charges**| Float | Statutory taxes and charges paid | `2.50` |
| **net_amount** | Float | Net cash inflow or outflow | `14222.50` |
| **broker** | String | Statement broker name | `Zerodha` |
| **broker_order_id**| String | Unique transaction ID from the broker (primary key) | `12345678` |
| **currency** | String | Currency identifier | `INR` |
| **import_source**| String | Import metadata detail | `zerodha_export.csv` |
| **notes** | String | Supplementary notes or commentary | `Long term buy` |

---

## 2. GITHUB DATA Schema

The reference worksheet `"GITHUB DATA"` contains 38 columns (0-indexed indices):

| Index | Column Header | Data Type | Description |
| :---: | :--- | :---: | :--- |
| **0** | Symbol | String | Ticker symbol |
| **1** | Sector | String | Company industry sector |
| **2** | Industry | String | Industry subcategory |
| **3** | Archetype | String | Business archetype tag |
| **4** | CMP | Float | Current Market Price |
| **5** | 52W High | Float | 52-week peak price |
| **6** | 52W Low | Float | 52-week trough price |
| **7** | Day Chg% | Float | Daily percentage change |
| **8** | Buy 20% Less | Float | Buy zone price target |
| **9** | PE | Float | Price-to-Earnings ratio |
| **10** | EPS | Float | Earnings per Share |
| **11** | Book Value | Float | BV per share |
| **12** | P/B | Float | Price-to-Book ratio |
| **13** | Div Yield% | Float | Dividend Yield percentage |
| **14** | ROE% | Float | Return on Equity |
| **15** | ROA% | Float | Return on Assets |
| **16** | Debt/Equity | Float | Leverage ratio |
| **17** | Rev Growth% | Float | Revenue growth percentage |
| **18** | Beta | Float | Volatility index |
| **19** | Quality Score | Float | Metric quality rating |
| **20** | Valuation Score| Float | Valuation evaluation rating |
| **21** | Timing Score | Float | Technical entry rating |
| **22** | Total Score | Float | Integrated scoring result |
| **23** | Final Action | String | Scoring advice (`BUY`, `AVOID`, `SELL`) |
| **24** | Strengths | String | Key positive attributes |
| **25** | Weaknesses | String | Key risk attributes |
| **26** | XIRR% | Float | Live annualized cash-flow rate of return |
| **27** | Updated | String | Update timestamp |
| **28** | RSI | Float | Relative Strength Index (14) |
| **29** | SMA 50 | Float | 50-day Simple Moving Average |
| **30** | SMA 200 | Float | 200-day Simple Moving Average |
| **31** | EMA 20 | Float | 20-day Exponential Moving Average |
| **32** | Vol Spike | Float | Volume activity spikes |
| **33** | Trend | String | Technical price trend description |
| **34** | Mkt Cap Cr | Float | Market Capitalization in Cr |
| **35** | Cap Type | String | Cap size category (`Large`, `Mid`, `Small`) |
| **36** | Quantity | Float | Total holding shares (calculated) |
| **37** | Avg Buy Price | Float | Average purchase price (calculated) |
