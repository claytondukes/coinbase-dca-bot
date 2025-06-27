# Crypto DCA Bot

A tool to automate scheduled cryptocurrency purchases on Coinbase Advanced Trader (Previously branded as Coinbase Pro).

Frequently used for DCA (Dollar-Cost Averaging) investment strategies to reduce market volatility impact by making regular, fixed-amount purchases regardless of price.

## Features

- Schedule cryptocurrency purchases on a daily or weekly basis
- Configure purchases for multiple currency pairs
- Specify the exact time and day for transactions
- Support for both limit (maker) and market (taker) orders
- Lower fees compared to standard Coinbase purchases
- Flexible deployment options (local Python or Docker)

## Background

This project was created to automate weekly cryptocurrency purchases while avoiding the high fees charged by mainstream providers. By executing trades directly through the Coinbase Advanced Trader API, it significantly reduces transaction costs compared to recurring buys on the standard Coinbase platform.

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

1. Clone this repository or download the source code
2. Install dependencies:

```bash
pip install -r requirements.txt
```

## Configuration Guide

This guide assumes you have some knowledge of setting up a Python environment and running Python scripts.

1. Create a Coinbase account and generate an API key & secret with the following permissions:

   ```text
   View permissions
   Trade permissions
   ```

   Make sure to select both View and Trade permissions when creating your API key in Coinbase Advanced.

2. Create a `.env` file in the root directory of the project and add the following variables with your API details from Coinbase Advanced Trade API:

   ```text
   COINBASE_API_KEY="organizations/your-org-id/apiKeys/your-key-id"
   COINBASE_API_SECRET="-----BEGIN EC PRIVATE KEY-----\n...your private key...\n-----END EC PRIVATE KEY-----\n"
   ```

   **Note:** The new Coinbase Advanced Trade API uses a different format for API keys than the previous version. Make sure to use the full organization/apiKeys format as shown above.

3. Rename or copy the `schedule_template.json` to `schedule.json` and add your schedule details.

The time set is in 24 hour format and is based on the local time of the machine running the script.

You can specify whether to use limit orders (maker) or market orders (taker) for each transaction. Limit orders help reduce fees and eliminate price slippage. By default, the bot uses limit orders with a price set slightly below market price (99.9%) to ensure you act as a maker. e.g. `BTC/GBP` means you are buying `BTC` with the `quote_currency_amount` of `GBP`.

```json
[
    {
        "frequency": "daily",
        "day_of_week": null,
        "time": "10:30",
        "currency_pair": "ETH/GBP",
        "quote_currency_amount": 1,
        "order_type": "limit",
        "limit_price_pct": 0.999,
        "order_timeout_hours": 24
    },
    {
        "frequency": "weekly",
        "day_of_week": "wednesday",
        "time": "15:45",
        "currency_pair": "BTC/GBP",
        "quote_currency_amount": 1,
        "order_type": "market"
    }
]
```

## Running the Bot

### Method 1: Python Script

Run the main Python script:

```bash
python main.py
```

The bot will start and execute trades according to your schedule.

### Method 2: Docker Deployment

The project includes Docker configuration for easy deployment and automated restarts.

1. Make sure Docker and Docker Compose are installed on your system
2. Create a `logs` directory in the project root (if it doesn't exist)
3. Start the container:

```bash
docker compose build
docker compose up -d
```

This will run the bot in the background with automatic restarts and log output to the `logs/dcabot.log` file.

## Notes

- Originally started working on Binance and connecting to their APIs, but due to some personal consumer issues, focus shifted to Coinbase Advanced Trader.
- The bot will run indefinitely, executing trades based on your schedule configuration.
- All transactions are logged for reference and troubleshooting.
- For limit orders, the price is set at a percentage (`limit_price_pct`) of the current market price (default: 99.9%) to ensure your orders execute as maker orders with lower fees.
- Limit orders can be configured with an auto-cancellation time using `order_timeout_hours` (default: 24). This sets an expiration time after which unfilled limit orders will automatically be cancelled, preventing stuck orders.
- If `order_type` is not specified, limit orders are used by default. Set `order_type` to `"market"` if you want to use market orders instead.
