#!/usr/bin/env python3
from bot import auth_coinbase, scheduler
import logging
import time
import datetime
import os
try:
    from dotenv import load_dotenv
except ImportError:
    def load_dotenv(*args, **kwargs):
        return None

if __name__ == '__main__':
    # Load .env for local (non-Docker) runs
    load_dotenv()
    # Apply TZ from environment (if provided)
    try:
        time.tzset()
    except AttributeError:
        pass

    # Configure logging at process startup if verbose mode is enabled
    level = logging.DEBUG if auth_coinbase.parse_bool_env('COINBASE_VERBOSE') else logging.INFO
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    
    logging.info('start DCA bot')
    logging.info("Startup local time: %s TZ: %s", datetime.datetime.now(), time.tzname)
    logging.info('Connecting to Coinbase API')
    coinbase = auth_coinbase.ConnectCoinbase()

    logging.info('Setting Schedules')
    schedule_file = os.getenv('SCHEDULE_FILE', 'schedule.json')
    logging.info('Loading schedule file: %s', schedule_file)
    task_schedule = scheduler.scheduleSetup(schedule_file)

    for task in task_schedule.schedule_data:
        currency_pair = task['currency_pair']
        quote_currency_amount = task['quote_currency_amount']
        # Get order_type from task or use 'limit' as default
        order_type = task.get('order_type', 'limit')  # Default to limit orders
        limit_price_pct = task.get('limit_price_pct', 0.01)  # Default to 0.01% below market price
        order_timeout_seconds = task.get('order_timeout_seconds', 600)  # Default to 600 seconds (10 minutes)
        post_only = task.get('post_only', True)  # Default to post-only for maker fees
        # Optional maker repricing settings
        reprice_interval_seconds = task.get('reprice_interval_seconds')
        reprice_duration_seconds = task.get('reprice_duration_seconds')
        
        task_schedule.create_schedule(
            task, 
            lambda cp=currency_pair, qca=quote_currency_amount, ot=order_type, lpp=limit_price_pct, ots=order_timeout_seconds, po=post_only, ri=reprice_interval_seconds, rd=reprice_duration_seconds: 
                coinbase.create_order(cp, qca, order_type=ot, limit_price_pct=lpp, order_timeout_seconds=ots, post_only=po, reprice_interval_seconds=ri, reprice_duration_seconds=rd)
        )
        #task_schedule.create_schedule(task, lambda cp=currency_pair: coinbase.get_markets(cp))

    task_schedule.show_schedule()
    task_schedule.start_schedule()
