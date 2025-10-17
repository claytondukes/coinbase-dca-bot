# Crypto DCA Bot

A tool to automate scheduled cryptocurrency purchases on Coinbase Advanced
Trader (Previously branded as Coinbase Pro).

Frequently used for DCA (Dollar-Cost Averaging) investment strategies to reduce
market volatility impact by making regular, fixed-amount purchases regardless of
price.

## Features

- Schedule cryptocurrency purchases on a daily or weekly basis
- Configure purchases for multiple currency pairs
- Specify the exact time and day for transactions
- Supports post-only limit (maker) and market (taker) orders; optional
  maker-first with fallback
- Lower fees compared to standard Coinbase purchases
- Flexible deployment options (local Python or Docker)

## Background

Using the Coinbase Advanced Trader API automates time-based cryptocurrency
purchases and helps minimize fees. Executing trades directly through Advanced
Trade reduces transaction costs compared to recurring buys on the standard
Coinbase platform.

## Installation

### Prerequisites

- Python 3.6+
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

3. Create `schedule.json` from `schedule_template.json` and define the schedule.

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
2. A `logs` directory in the project root is required.
3. Start the container:

```bash
docker compose build
docker compose up -d
```

The container runs the bot in the background with automatic restarts and logs to
`logs/dcabot.log`.

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
  "buy_or_sell": "buy",
  "currency_pair": "BTC/USDC",
  "quote_currency_amount": 20,
  "order_type": "limit",
  "limit_price_pct": 0.01,
  "post_only": true,
  "order_timeout_seconds": 600
}
```

## Timezone behavior

- The scheduler interprets `time` in the process timezone.
- In Docker, set `TZ` in `.env` (for example, `TZ=America/New_York`) so jobs run
  in that timezone.
- On bare Python, the host system timezone determines interpretation; configure
  as needed.

## Schedule files

- The bot loads `schedule.json` by default in `main.py`.
- Keep your full plan in `schedule-full.json` if desired, then copy to
  `schedule.json` for deployment, or change the loader in `main.py` to point to
  your preferred file.
