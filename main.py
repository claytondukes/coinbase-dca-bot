#!/usr/bin/env python3
from bot import auth_coinbase, scheduler
import logging
import os
from dotenv import load_dotenv

if __name__ == '__main__':
    # Load .env for local (non-Docker) runs
    try:
        load_dotenv()
    except Exception:
        pass

    # Configure logging at process startup if verbose mode is enabled
    verbose_env = os.getenv('COINBASE_VERBOSE', '').strip().lower()
    if verbose_env in ('1', 'true', 'yes', 'on', 'debug'):
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
    
    print('start DCA bot')
    print('Connecting to Coinbase API')
    coinbase = auth_coinbase.ConnectCoinbase()

    print('Setting Schedules')
    task_schedule = scheduler.scheduleSetup('schedule.json')

    for task in task_schedule.schedule_data:
        currency_pair = task['currency_pair']
        quote_currency_amount = task['quote_currency_amount']
        # Get order_type from task or use 'limit' as default
        order_type = task.get('order_type', 'limit')  # Default to limit orders
        limit_price_pct = task.get('limit_price_pct', 0.01)  # Default to 0.01% below market price
        order_timeout_seconds = task.get('order_timeout_seconds', 600)  # Default to 600 seconds (10 minutes)
        post_only = task.get('post_only', True)  # Default to post-only for maker fees
        
        task_schedule.create_schedule(
            task, 
            lambda cp=currency_pair, qca=quote_currency_amount, ot=order_type, lpp=limit_price_pct, ots=order_timeout_seconds, po=post_only: 
                coinbase.create_order(cp, qca, order_type=ot, limit_price_pct=lpp, order_timeout_seconds=ots, post_only=po)
        )
        #task_schedule.create_schedule(task, lambda cp=currency_pair: coinbase.get_markets(cp))

    task_schedule.show_schedule()
    task_schedule.start_schedule()
