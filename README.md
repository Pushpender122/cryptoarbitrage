# ARBITER — Crypto Options Arbitrage & Copy-Trading Terminal

A two-venue options terminal: **Delta Exchange India** (live public market data) and
**CoinSwitch PRO** (options API is in private beta and allowlisted per key).

It matches identical option contracts across both venues, prices the spread after
fees and slippage, routes single-leg or paired orders through one interface, and
tracks positions, risk and copy-trading rules.

![stack](https://img.shields.io/badge/backend-FastAPI%20%2B%20SQLAlchemy-0b0e14) ![stack](https://img.shields.io/badge/frontend-React%20%2B%20Tailwind%20v4-0b0e14)

---

## What is actually connected

| | Market data | Order routing |
| --- | --- | --- |
| **Delta Exchange** | **Live**, public `v2/products` + `v2/tickers` (no key needed) | Implemented (`POST /v2/orders`, HMAC-SHA256 signing). Needs your key with Trading permission. |
| **CoinSwitch PRO** | Private beta — returns `401 Signature validation failed` without an allowlisted key | Implemented (`POST /v5/order/create`, `category: option`, Ed25519 signing). Needs allowlisted options access. |

Because the CoinSwitch options API is gated, the terminal runs that leg on a
**locally generated book** that mirrors Delta's contract keys, anchored to the
live venue's tradable mid, with a bounded per-contract basis dislocation. Every quote carries
`source: "live" | "simulated"`, the UI badges it `SIM`, and `/api/status` says so.
**Nothing simulates a live feed silently.** To require both legs live:

```bash
COINSWITCH_SIMULATED=false   # CoinSwitch leg disappears instead of being simulated
```

To request real options access: email **api@coinswitch.co** from your registered
address with your public API key, subject "Request for Options Trading API Access"
(<https://api-trading.coinswitch.co/options/access>).

---

## Quick start

Requires Python 3.11+ and Node 20+.

```bash
# 1. Backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env          # optional: keys live here, never in git
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000

# 2. Frontend (in another shell)
cd frontend
npm install
npm run build                 # FastAPI serves frontend/dist on :8000
# or, for hot reload, `npm run dev` on :5173 (proxies /api and /ws to :8000)
```

Then open <http://localhost:8000>. Or run everything with `./run.sh`.

### First-run authentication

Before starting the backend, edit `backend/.env` and replace `ADMIN_PASSWORD` with a long,
unique password. Generate a strong signing secret and paste it into `SESSION_SECRET`:

    python -c "import secrets; print(secrets.token_urlsafe(48))"

Use `SESSION_COOKIE_SECURE=true` when deployed behind HTTPS. The app has one administrator
login (not multi-user accounts). Login sessions expire after `SESSION_TTL_SECONDS` and
mutating requests require a CSRF token. Login attempts are throttled per source IP in the
running backend process. Keep `.env` out of version control.

Tests: `cd backend && .venv/bin/python -m pytest -q` (20 engine tests).

Database: SQLite by default (`backend/terminal.db`). Postgres works through
`DATABASE_URL` after `pip install "psycopg[binary]"`. Redis is not used in this build.

---

## Architecture

```
React terminal (Vite + Tailwind v4 + lightweight-charts)
   │  REST /api/*            ▲ WebSocket /ws (per-client snapshot every poll)
   ▼                         │
FastAPI ── MarketService ────┴── contract/quote cache, edge history
   │            │
   │            ├── DeltaProvider      (live public REST, HMAC-signed orders)
   │            └── CoinSwitchProvider (Ed25519 HERS orders)  ── or SimulatedProvider
   │
   ├── matching.py     contract matching + arbitrage maths (pure functions)
   ├── orders.py       unified routing, paper fills, position ledger, paired legs
   ├── risk.py         pre-trade gates, exposure, stop/target exits
   └── copytrading.py  mirror fills/exits between venues
```

### Comparison rules

Two legs are only compared when **underlying, option type, strike and expiry**
match exactly. Equivalence then additionally requires matching **contract size**
(`contract_value`) and settlement currency group; USD/USDT are normalised and the
row is flagged (`settlement_normalized`) with a note. Expiries inferred from a
symbol rather than venue metadata, and simulated legs, are also flagged per row.

Per direction (`buy A / sell B`):

```
gross      = sell_bid − buy_ask
fees       = buy_ask × taker_A + sell_bid × taker_B
slippage   = (buy_ask + sell_bid) × slippage_bps / 10_000
net        = gross − fees − slippage
edge_bps   = net / buy_ask × 10_000
cash/ctr   = net × contract_value        (only when contract sizes match)
```

Ranked opportunities exclude non-equivalent pairs and pairs without top-of-book
size on both legs. Quantity is capped by the smaller of the two top-of-book sizes.
A **sanity guard** additionally rejects spreads that are more likely noise than
arbitrage — a premium within 10 ticks of the minimum, an edge above 8%, or a leg
whose own bid/ask is wider than 15% — and the scanner reports how many were
dropped for that reason instead of showing them as opportunities.

### Execution and risk

- **Paper (default)** — fills are simulated against the current touch with a
  configurable slippage buffer, resting limits fill when the market crosses, and
  positions/P&L/fees are kept in the local ledger. Real venue quotes drive the fills.
- **Live** — orders are signed and routed to the venue, then reconciled against it:
  - every working order is polled (`LIVE_SYNC_MS`, default 3 s) for venue status,
    cumulative filled quantity, average price and fees;
  - each execution is stored **once** per venue fill id (`/api/fills`), so restarts,
    retries and overlapping polls cannot double-count a partial fill;
  - copy rules receive only the **newly filled delta**, not the cumulative size;
  - positions are **replaced** by venue snapshots (`LIVE_POSITIONS_SYNC_MS`, default
    5 s): size, entry, mark, unrealized/realized P&L, margin and liquidation price.
    Positions closed on the exchange disappear locally on the next poll.
  - risk gates (open positions, gross exposure, daily loss) use venue positions in
    live mode, not the paper ledger. Local automatic stop/target exits stay
    paper-only; use the venue's own risk orders or the row's reduce-only Close.
- **Every** order, including arbitrage and copy legs, passes the same pre-trade
  gates: kill switch, max order qty/notional, max open positions, gross exposure,
  daily loss limit, and price-vs-mid deviation.
- Paired execution validates **both** legs before sending either, then places the
  sell leg first; a partial pair is reported loudly rather than hidden.
- Copy rules mirror filled orders (and optionally exits) with a size ratio, a max
  qty cap and the same risk gates. Rules start paused.

---

## API surface

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/status` | Mode, exchange health, assumptions, risk settings |
| GET | `/api/market/snapshot` | Indexes, expiries, matched rows, arbitrage, stats |
| GET | `/api/market/pair`, `/api/market/history` | One row / spread history for the chart |
| POST | `/api/orders` | Single-leg order |
| POST | `/api/orders/pair` | Two-leg arbitrage execution |
| POST | `/api/orders/{id}/cancel` | Cancel a resting order |
| GET | `/api/positions` | Position ledger (paper) or venue snapshots (live) + summaries |
| POST | `/api/positions/{id}/close` | Market close a paper position |
| POST | `/api/venue-positions/{id}/close` | Reduce-only market close of a venue position |
| POST | `/api/positions/sync` | Force an immediate venue reconciliation |
| POST | `/api/orders/{id}/refresh` | Reconcile one live order on demand |
| GET | `/api/fills` | Stored venue executions (unique per fill id) |
| GET | `/api/history`, `/api/history/summary` | Orders, fills, P&L, win rate |
| GET/PUT | `/api/risk` | Risk limits |
| GET/POST/PATCH/DELETE | `/api/copy-strategies` | Copy rules |
| WS | `/ws?underlying=BTC&expiry=…&option_type=call` | Streaming snapshots |

## Known limits (read before risking capital)

1. **CoinSwitch options are simulated** in this build; its symbol grammar, contract
   size and settlement currency are unverified until your key is allowlisted.
   Instrument metadata is used when present, with a heuristic symbol parser as
   fallback (failing instruments are counted and reported, not guessed).
2. Delta options settle in **USD**; the CoinSwitch leg is assumed USDT. Normalised
   and flagged, but stablecoin/FX risk is real.
3. Fees are venue taker rates; the CoinSwitch taker fee is an **assumption**
   (configurable) until account specs are known.
4. Margin/leverage per venue is displayed from contract metadata only; the terminal
   does not simulate margin calls or liquidation.
5. Quantities: Delta `size` is integer contracts. CoinSwitch documents options
   `qty` in the base asset, which is ambiguous for options — verify with your key.
6. Live reconciliation is polling-based, not a private WebSocket stream, and has been
   exercised against stubbed venue responses only — it has not been run against a
   funded exchange account in this build. Delta's signed `size` convention and
   CoinSwitch's options `positionIdx`/qty semantics must be confirmed with your first
   real order before trusting the P&L figures.
7. The terminal now requires a single administrator password and signed session cookie.
   Use HTTPS, a private network/VPN and firewall rules in production. This is not a
   multi-user system and does not include MFA or distributed login throttling. Keys stay
   server-side in `.env` and are never sent to the browser.
