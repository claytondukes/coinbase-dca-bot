# Crypto DCA Bot

A tool to automate scheduled cryptocurrency purchases on Coinbase Advanced
Trade (previously branded as Coinbase Pro).

Frequently used for DCA (Dollar-Cost Averaging) investment strategies to reduce
market volatility impact by making regular, fixed-amount purchases regardless of
price.

## Features

- Schedule cryptocurrency purchases on a seconds, hourly, daily, weekly,
  monthly, or once basis
- Configure purchases for multiple currency pairs
- Specify the exact time and day for transactions
- Supports post-only limit (maker) and market (taker) orders; optional
  maker-first with fallback
- Lower fees compared to standard Coinbase purchases
- Flexible deployment options (local Python or Docker)

## Background

Using the Coinbase Advanced Trade API automates time-based cryptocurrency
purchases and helps minimize fees. Executing trades directly through Advanced
Trade reduces transaction costs compared to recurring buys on the standard
Coinbase platform.

## Installation

### Prerequisites

- Python 3.11+
- Coinbase account with API access
- The required Python packages:

```text
python-dotenv
requests
schedule
coinbase-advanced-py
```

### Setup

1. Clone the repository or download the source code
2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Configuration Guide

Basic familiarity with Python environments and script execution is assumed.

1. Create a Coinbase account and generate an API key & secret with the following
   permissions:

   ```text
   View permissions
   Trade permissions
   ```

   Select both View and Trade permissions when creating the API key in Coinbase
   Advanced.

2. Create a `.env` file in the project root and add the following variables with
   API details from Coinbase Advanced Trade:

   ```text
   COINBASE_API_KEY="organizations/your-org-id/apiKeys/your-key-id"
   COINBASE_API_SECRET="-----BEGIN EC PRIVATE KEY-----\n...your private key...\n-----END EC PRIVATE KEY-----\n"
   ```

   **Note:** The Coinbase Advanced Trade API uses a different format for API
   keys than the previous version. Use the full
   `organizations/<org-id>/apiKeys/<key-id>` format as shown above.

3. Create `schedule.json` from `schedule-sample.json` and define the schedule.

Times are in 24‑hour format and interpreted in the process timezone.

Each transaction can be configured as a limit (maker) or market (taker) order.
Limit orders help reduce fees and eliminate price slippage. By default, the bot
uses limit orders with a price set slightly below market price to prefer maker
execution. For example, `BTC/USDC` indicates buying BTC with the specified USDC
amount.

```json
[
    {
        "frequency": "daily",
        "day_of_week": null,
        "time": "10:30",
        "currency_pair": "ETH/USDC",
        "quote_currency_amount": 1,
        "order_type": "limit",
        "limit_price_pct": 0.01,
        "order_timeout_seconds": 600
    },
    {
        "frequency": "weekly",
        "day_of_week": "wednesday",
        "time": "15:45",
        "currency_pair": "BTC/USDC",
        "quote_currency_amount": 1,
        "order_type": "market"
    }
]
```

## Running the Bot

### Method 1: Python Script

Start the bot:

```bash
python main.py
```

The bot starts and executes trades according to the defined schedule.

### Method 2: Docker Deployment

The project includes Docker configuration for easy deployment and automated
restarts.

1. Docker and Docker Compose must be installed.
2. Start the container:

```bash
docker compose build
docker compose up -d
```

The container runs the bot in the background with automatic restarts. View
logs with:

```bash
docker compose logs -f --tail=100
```

## Notes

- The bot runs indefinitely, executing trades based on the schedule
  configuration.
- All transactions are logged for reference and troubleshooting.
- For limit orders, the price is set at a percentage (`limit_price_pct`) of the
  current market price (default: 0.01%) to ensure orders execute as maker orders
  with lower fees.
- Limit orders can be configured with an auto-cancellation time using
  `order_timeout_seconds`. This setting determines how long (in seconds) a limit
  order remains active before being auto-cancelled if not filled. Default is 600
  seconds (10 minutes). This prevents stuck orders.
- If `order_type` is not specified, limit orders are used by default. To use
  market orders, set `order_type` to "market".

- Alternatively, set `limit_price_absolute` to place a fixed-price limit order.
  When provided, `limit_price_pct` is ignored. The bot logs a warning if the
  absolute price is more than 5% above market for a buy.
- Set `time_in_force` to `"GTC"` to create a Good‑Til‑Cancelled order that
  does not auto‑expire. Any value other than `"GTC"` will be treated as `"GTD"`.
  Unsupported values (such as `"IOC"`, `"FOK"`, etc.) will not result in an
  error, but will fallback to `"GTD"` behavior.
- Set `disable_fallback: true` to skip the fallback‑to‑market step after timeout
  or the end of a repricing window.
- Use `"once"` frequency for one‑off jobs. It runs at the configured `time` and
  cancels itself after execution. If the time has already passed today at
  startup, it executes immediately.

## Environment (.env)

A `.env` file in the project root is loaded by Docker Compose via `env_file:
.env`.

```env
# Coinbase Advanced Trade credentials
COINBASE_API_KEY="organizations/<org-id>/apiKeys/<key-id>"
# Paste the EC private key exactly as provided by Coinbase Advanced Trade.
# Both multiline PEM and \n-escaped formats are supported.
COINBASE_API_SECRET="-----BEGIN EC PRIVATE KEY-----\n...your-key...\n-----END EC PRIVATE KEY-----\n"

# Timezone used by the scheduler (container/local process)
# Example: America/New_York (Eastern Time)
TZ=America/New_York

# Verbose SDK logging (optional). Accepted truthy values:
# 1, true, yes, on, debug (case-insensitive)
COINBASE_VERBOSE=false

# Schedule file path (optional). Defaults to schedule.json
SCHEDULE_FILE=schedule.json
```

## Maker-first with quick fallback

- **Post-only limit orders** keep fees low by resting on the book.
- Set a small discount using `limit_price_pct` (percent-of-100), e.g. `0.01`
  (0.01%), `0.3` (0.3%), `0.4` (0.4%).
- A short `order_timeout_seconds` (for example, `300–600`) guarantees completion
  via fallback-to-market after expiry.

Example schedule entry (maker-first):

```json
{
  "frequency": "daily",
  "time": "15:45",
  "currency_pair": "BTC/USDC",
  "quote_currency_amount": 20,
  "order_type": "limit",
  "limit_price_pct": 0.01,
  "post_only": true,
  "order_timeout_seconds": 600
}
```

## Maker repricing before fallback (optional)

- To improve the chance of maker fills while price moves, enable periodic
  repricing. During a configurable duration, the bot cancels and reposts a
  post-only limit every interval at the current market price minus
  `limit_price_pct`. If the order still is not filled by the end of the
  duration, the bot performs the same safe fallback described above.
- Configure per schedule entry with two optional fields:
  - `reprice_interval_seconds`: how often to reprice (e.g., 60).
  - `reprice_duration_seconds`: how long to keep repricing before fallback.

Example schedule entry with repricing:

```json
{
  "frequency": "daily",
  "time": "15:45",
  "currency_pair": "BTC/USDC",
  "quote_currency_amount": 20,
  "order_type": "limit",
  "limit_price_pct": 0.10,
  "post_only": true,
  "order_timeout_seconds": 1800,
  "reprice_interval_seconds": 60,
  "reprice_duration_seconds": 1800
}
```

## Absolute price, GTC, and one‑off orders

- To target an exact price, provide `limit_price_absolute`. Combine with
  `time_in_force: "GTC"` to leave the order open until filled, and optionally
  `disable_fallback: true` to prevent a market buy after expiry or repricing.
- Use `"once"` frequency to place a single order at the scheduled time. If the
  time has already passed when the bot starts, the order is placed immediately.

Example one‑off absolute GTC order:

```json
{
  "frequency": "once",
  "time": "17:45",
  "currency_pair": "BTC/USDC",
  "quote_currency_amount": 2000,
  "order_type": "limit",
  "limit_price_absolute": 101830,
  "post_only": true,
  "time_in_force": "GTC",
  "disable_fallback": true
}
```

## Timezone behavior

- The scheduler interprets `time` in the process timezone.
- In Docker, set `TZ` in `.env` (for example, `TZ=America/New_York`) so jobs run
  in that timezone.
- On bare Python, the host system timezone determines interpretation; configure
  as needed.

## Schedule files

- The bot loads `schedule.json` by default, or the path provided via the
  `SCHEDULE_FILE` environment variable.
- Keep a full plan in another file (for example, `schedule-full.json`) and set
  `SCHEDULE_FILE` accordingly for deployment.
