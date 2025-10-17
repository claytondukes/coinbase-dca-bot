from coinbase.rest import RESTClient
import os
from datetime import datetime, timedelta
import uuid
import time
from decimal import Decimal, ROUND_DOWN
import threading
import logging

logger = logging.getLogger(__name__)

def parse_bool_env(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ('1', 'true', 'yes', 'on', 'debug')

def quantize_or_round(value, increment, default_decimals):
    """
    Helper to quantize a value by increment or fall back to rounding.
    
    Args:
        value: Numeric value to quantize (float, int, or Decimal)
        increment: Increment to quantize by (string or None)
        default_decimals: Number of decimals to round to if increment fails
    
    Returns:
        Decimal: Quantized value
    """
    if increment:
        try:
            return Decimal(str(value)).quantize(Decimal(str(increment)), rounding=ROUND_DOWN)
        except Exception:
            return Decimal(str(value)).quantize(Decimal('1e-{}'.format(default_decimals)), rounding=ROUND_DOWN)
    else:
        return Decimal(str(value)).quantize(Decimal('1e-{}'.format(default_decimals)), rounding=ROUND_DOWN)

class ConnectCoinbase():
    """
    Class for connecting to Coinbase Advanced Trade API using the official SDK.
    This replaces the previous CCXT implementation.
    """

    def __init__(self):
        """Initialize the Coinbase connection using API keys from environment variables."""
        self.api_key = os.getenv('COINBASE_API_KEY')
        self.api_secret = os.getenv('COINBASE_API_SECRET')

        # Sanitize API key (remove wrapping quotes and trim)
        if self.api_key:
            k = self.api_key.strip()
            if (k.startswith('"') and k.endswith('"')) or (k.startswith("'") and k.endswith("'")):
                k = k[1:-1]
            self.api_key = k

        # Normalize EC private key: convert literal "\n" to real newlines, strip quotes/whitespace
        if self.api_secret:
            s = self.api_secret.strip()
            if (s.startswith('"') and s.endswith('"')) or (s.startswith("'") and s.endswith("'")):
                s = s[1:-1]
            s = s.replace("\\n", "\n").replace("\\r", "\r")
            self.api_secret = s

        # Initialize the Coinbase client with verbose logging for diagnostics
        self.verbose = parse_bool_env('COINBASE_VERBOSE')
        self.client = RESTClient(api_key=self.api_key, api_secret=self.api_secret, verbose=self.verbose)
        
        # Verify connection by getting account information
        try:
            accounts = self.client.get_accounts()
            if accounts:
                logger.info("API credentials verified successfully")
            else:
                logger.error("Could not retrieve account information")
                raise RuntimeError("Could not retrieve account information during Coinbase API initialization")
        except Exception as e:
            logger.error(f"API credentials are incorrect or not set properly: {e}")
            raise RuntimeError(f"API credentials are incorrect or not set properly: {e}")
        
    
    def get_balance(self):
        """Get balance information for all accounts."""
        try:
            resp = self.client.get_accounts()
            balances = {}
            
            # Access accounts list from typed response
            accounts_list = getattr(resp, 'accounts', [])
            for account in accounts_list:
                currency = getattr(account, 'currency', '')
                available_balance_obj = getattr(account, 'available_balance', None)
                available_balance = getattr(available_balance_obj, 'value', '0') if available_balance_obj else '0'
                balances[currency] = {
                    'available': float(available_balance),
                    'currency': currency
                }
                logger.info(f"{currency}: {available_balance}")
            
            return balances
        except Exception as e:
            logger.error(f"Failed to get balance: {e}")
            return None

    def get_product_info(self, currency_pair=None):
        """Get full product information including price and increments."""
        if currency_pair is None:
            logger.info('No currency pair provided, using default')
            currency_pair = 'BTC/USDC'
        
        # Convert from BTC/USDC format to BTC-USDC format
        product_id = currency_pair.replace('/', '-')
        
        try:
            product = self.client.get_product(product_id)
            if product:
                # Access price as an attribute of the product object
                price = getattr(product, 'price', None)
                # Validate price
                try:
                    price_float = float(price)
                except (TypeError, ValueError):
                    logger.error(f"Error: Retrieved price for {product_id} is not a valid number: {price}")
                    return None
                if price_float <= 0:
                    logger.error(f"Error: Retrieved price for {product_id} is not positive: {price_float}")
                    return None
                logger.info(f"Retrieved {product_id} price: {price_float}")
                
                # Return full product info including increments
                product_info = {
                    'symbol': currency_pair,
                    'id': product_id,
                    'price': price_float,
                    'base': currency_pair.split('/')[0],
                    'quote': currency_pair.split('/')[1],
                    'price_increment': getattr(product, 'price_increment', None),
                    'base_increment': getattr(product, 'base_increment', None),
                    'quote_increment': getattr(product, 'quote_increment', None),
                    'quote_min_size': getattr(product, 'quote_min_size', None),
                    'base_min_size': getattr(product, 'base_min_size', None)
                }
                return product_info
            else:
                logger.error(f"Could not retrieve product information for {currency_pair}")
                return None
        except Exception as e:
            logger.error(f"Failed to get product information: {e}")
            return None

    def create_order(self, currency_pair, amount_quote_currency, client_order_id=None, order_type="limit", limit_price_pct=0.01, order_timeout_seconds=600, post_only=True, max_retries=3):
        """
        Create a buy order for cryptocurrency using quote currency amount.
        
        Args:
            currency_pair (str): Currency pair in format 'BTC/USDC'
            amount_quote_currency (float): Amount of quote currency to spend
            client_order_id (str): Optional client-provided order ID
            order_type (str): Order type to create ('market' or 'limit')
            limit_price_pct (float): For limit orders, percent of 100 to set as limit price
                                    (e.g., 0.01 means 0.01% below current price)
            order_timeout_seconds (int): Seconds until limit order expires (GTD orders)
            post_only (bool): If True, limit order will only be placed as maker
            max_retries (int): Reserved for future use (order monitoring/retry logic)
            
        Returns:
            dict: Order information if successful, None otherwise
        """
        if not currency_pair or not amount_quote_currency:
            logger.error("Currency pair and amount are required")
            return {
                'success': False,
                'order_id': None,
                'product_id': currency_pair.replace('/', '-') if currency_pair else None,
                'side': 'BUY',
                'client_order_id': client_order_id,
                'error': 'Missing currency_pair or amount_quote_currency'
            }
        
        self.current_datetime = datetime.now()
        try:
            tz = time.tzname[time.localtime().tm_isdst] if time.daylight else time.tzname[0]
        except Exception:
            tz = str(time.tzname)
        logger.info(f"Current local time is: {self.current_datetime} TZ: {tz}")
        logger.info(f'Creating {order_type} order')
        logger.info(f'Currency pair: {currency_pair}')
        logger.info(f'Amount of quote currency: {amount_quote_currency}')
        
        # Convert from BTC/USDC format to BTC-USDC format
        product_id = currency_pair.replace('/', '-')
        
        # Use provided client_order_id or generate a unique one using UUID
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())
            logger.info(f'Generated client_order_id: {client_order_id}')
        else:
            logger.info(f'Using provided client_order_id: {client_order_id}')
        
        try:
            if order_type.lower() == 'market':
                product_info = self.get_product_info(currency_pair)
                if not product_info:
                    logger.error(f"Could not get product information for {currency_pair}")
                    return {
                        'success': False,
                        'order_id': None,
                        'product_id': product_id,
                        'side': 'BUY',
                        'client_order_id': client_order_id,
                        'error': 'Missing product info'
                    }

                quote_increment = product_info.get('quote_increment')
                quote_min_size = product_info.get('quote_min_size')
                quantized_amount = quantize_or_round(amount_quote_currency, quote_increment, 2)
                if quote_min_size and Decimal(str(quantized_amount)) < Decimal(str(quote_min_size)):
                    logger.error(f"Amount {quantized_amount} is below minimum quote size {quote_min_size} for {currency_pair}")
                    return {
                        'success': False,
                        'order_id': None,
                        'product_id': product_id,
                        'side': 'BUY',
                        'client_order_id': client_order_id,
                        'error': f'Amount {quantized_amount} below quote_min_size {quote_min_size}'
                    }
                logger.info(f"Placing market order with quote_size={quantized_amount}")
                order = self.client.market_order_buy(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    quote_size=str(quantized_amount)
                )
            else:  # limit order
                # Get product info (price + increments) in one call
                product_info = self.get_product_info(currency_pair)
                if not product_info:
                    logger.error(f"Could not get product information for {currency_pair}")
                    return {
                        'success': False,
                        'order_id': None,
                        'product_id': product_id,
                        'side': 'BUY',
                        'client_order_id': client_order_id,
                        'error': 'Missing product info'
                    }
                
                # Calculate limit price with discount from market price
                market_price = product_info['price']
                
                # Apply discount percentage to calculate limit price
                # Default is 0.01% below market price to ensure maker status
                limit_price = market_price * (1 - (limit_price_pct / 100))
                
                # Quantize limit_price using helper
                limit_price = quantize_or_round(limit_price, product_info['price_increment'], 2)
                
                logger.info(f"Market price: {market_price}, Limit price: {limit_price}")
                logger.info(f"Using {limit_price_pct}% discount for limit order")
                
                # Calculate base currency amount (how much crypto we're buying)
                base_size = (Decimal(str(amount_quote_currency)) / Decimal(str(limit_price)))
                
                # Quantize base_size using helper (default 8 decimals for crypto)
                base_size = quantize_or_round(base_size, product_info['base_increment'], 8)

                # Validate minimum sizes using Decimal for precision
                if product_info['base_min_size'] and base_size < Decimal(str(product_info['base_min_size'])):
                    logger.error(f"Base size {base_size} is below minimum {product_info['base_min_size']}")
                    return {
                        'success': False,
                        'order_id': None,
                        'product_id': product_id,
                        'side': 'BUY',
                        'client_order_id': client_order_id,
                        'error': f'Base size {base_size} below base_min_size {product_info["base_min_size"]}'
                    }

                # After base_size quantization, ensure notional still meets quote_min_size
                try:
                    min_quote_sz = product_info.get('quote_min_size')
                    if min_quote_sz is not None:
                        notional = (Decimal(str(base_size)) * Decimal(str(limit_price)))
                        if notional < Decimal(str(min_quote_sz)):
                            logger.error(
                                f"Notional {notional} below quote_min_size {min_quote_sz} after quantization; "
                                f"increase quote amount or adjust increments."
                            )
                            return {
                                'success': False,
                                'order_id': None,
                                'product_id': product_id,
                                'side': 'BUY',
                                'client_order_id': client_order_id,
                                'error': f'Notional {notional} below quote_min_size {min_quote_sz} after quantization'
                            }
                except Exception:
                    # If any issue computing notional, proceed; server-side validation will enforce
                    pass

                # Use Decimal for both sides; quantize only the amount to the increment (do not lower the platform minimum)
                if product_info['quote_min_size']:
                    quote_increment = product_info.get('quote_increment')
                    min_quote = Decimal(str(product_info['quote_min_size']))
                    amt_quote = Decimal(str(amount_quote_currency))
                    if quote_increment:
                        amt_quote = amt_quote.quantize(Decimal(str(quote_increment)), rounding=ROUND_DOWN)
                    else:
                        amt_quote = amt_quote.quantize(Decimal('0.01'), rounding=ROUND_DOWN)
                    if amt_quote < min_quote:
                        logger.error(f"Quote amount {amt_quote} is below minimum {min_quote}")
                        return {
                            'success': False,
                            'order_id': None,
                            'product_id': product_id,
                            'side': 'BUY',
                            'client_order_id': client_order_id,
                            'error': f'Amount {amt_quote} below quote_min_size {min_quote}'
                        }
                
                logger.info(f"Buying {base_size} {currency_pair.split('/')[0]} at {limit_price}")
                
                # Calculate end_time for limit orders (RFC3339 format)
                end_time = (datetime.utcnow() + timedelta(seconds=order_timeout_seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')
                logger.info(f"Order will expire at: {end_time} ({order_timeout_seconds} seconds from now)")
                
                # Debug logging is configured at process startup if verbose=True
                
                try:
                    # Try placing a GTD limit order with expiration time
                    order = self.client.limit_order_gtd(
                        client_order_id=client_order_id,
                        product_id=product_id,
                        side="BUY",
                        base_size=str(base_size),
                        limit_price=str(limit_price),
                        end_time=end_time,
                        post_only=post_only
                    )
                    logger.info("GTD order placed successfully with expiration")
                except Exception as e:
                    logger.warning(f"GTD order failed: {e}")
                    logger.info("Falling back to GTC order without expiration")
                    
                    # Fall back to GTC limit order without expiration
                    order = self.client.limit_order_gtc(
                        client_order_id=client_order_id,
                        product_id=product_id,
                        base_size=str(base_size),
                        limit_price=str(limit_price),
                        side="BUY",  # Required parameter for limit_order_gtc
                        post_only=post_only
                    )
                
                # For limit orders, we can optionally monitor the status
                # This is commented out by default as it might block the scheduler
                # Uncomment if you want to wait for the order to fill
                """
                # Check if the order was placed successfully before monitoring
                if hasattr(order, 'success') and order.success and hasattr(order, 'success_response'):
                    order_id = order.success_response.get('order_id')
                    if order_id:
                        # Monitor order status with retries
                        retries = 0
                        while retries < max_retries:
                            try:
                                order_status = self.client.get_order(order_id)
                                status = order_status.get('status', '')
                                print(f"Order status: {status}")
                                if status == 'FILLED':
                                    print("Limit order filled successfully")
                                    break
                                elif status in ['FAILED', 'CANCELLED', 'EXPIRED']:
                                    print(f"Order failed with status: {status}")
                                    break
                                else:
                                    # Wait before checking again
                                    retries += 1
                                    print(f"Waiting for order to fill... (retry {retries}/{max_retries})")
                                    time.sleep(5)  # Wait 5 seconds between checks
                            except Exception as e:
                                print(f"Error checking order status: {e}")
                                retries += 1
                                time.sleep(5)
                        
                        if retries >= max_retries:
                            print("Reached maximum retries, order may still be open")
                """
            
            # Handle CreateOrderResponse object attributes
            if hasattr(order, 'success') and order.success:
                logger.info("Order placed successfully")
                
                # Extract order details from success_response dictionary
                if hasattr(order, 'success_response'):
                    success_response = order.success_response
                    order_id = None
                    sr_product_id = product_id
                    if isinstance(success_response, dict):
                        # Extract and display order details
                        order_id = success_response.get('order_id')
                        sr_product_id = success_response.get('product_id', sr_product_id)
                        side = success_response.get('side', 'Unknown')
                        client_id = success_response.get('client_order_id', 'Unknown')
                        
                        logger.info(f"Order ID: {order_id if order_id else 'Not available'}")
                        logger.info(f"Product: {sr_product_id}")
                        logger.info(f"Side: {side}")
                        logger.info(f"Client Order ID: {client_id}")

                        # Start fallback-to-market monitor only for limit orders
                        if order_type.lower() != 'market' and order_id:
                            self._start_fallback_thread(sr_product_id, order_id, amount_quote_currency, order_timeout_seconds)

                        return {
                            'success': True,
                            'order_id': order_id,
                            'product_id': sr_product_id,
                            'side': side,
                            'client_order_id': client_id,
                            'error': None
                        }
                    else:
                        # Handle typed success object
                        order_id = getattr(success_response, 'order_id', None)
                        sr_product_id = getattr(success_response, 'product_id', sr_product_id)
                        side = getattr(success_response, 'side', 'Unknown')
                        client_id = getattr(success_response, 'client_order_id', 'Unknown')

                        logger.info(f"Order ID: {order_id if order_id else 'Not available'}")
                        logger.info(f"Product: {sr_product_id}")
                        logger.info(f"Side: {side}")
                        logger.info(f"Client Order ID: {client_id}")

                        if order_type.lower() != 'market' and order_id:
                            self._start_fallback_thread(sr_product_id, order_id, amount_quote_currency, order_timeout_seconds)
                        
                        # Return a basic dict for consistency
                        return {
                            'success': True,
                            'order_id': order_id,
                            'product_id': sr_product_id,
                            'side': side,
                            'client_order_id': client_id,
                            'error': None
                        }
                else:
                    logger.info("Order successful but no details available")
                
                return {
                    'success': True,
                    'order_id': None,
                    'product_id': product_id,
                    'side': 'BUY',
                    'client_order_id': client_order_id,
                    'error': None
                }
            else:
                error_msg = order.error_response if hasattr(order, 'error_response') else 'Unknown error'
                logger.error(f"Failed to create order: {error_msg}")
                return {
                    'success': False,
                    'order_id': None,
                    'product_id': product_id,
                    'side': 'BUY',
                    'client_order_id': client_order_id,
                    'error': str(error_msg)
                }
                
        except Exception as e:
            logger.error(f'Failed to create order: {e}')
            return {
                'success': False,
                'order_id': None,
                'product_id': product_id,
                'side': 'BUY',
                'client_order_id': client_order_id,
                'error': str(e)
            }

    def _start_fallback_thread(self, product_id, order_id, original_quote_amount, timeout_seconds):
        """Spawn a background thread to enforce fallback-to-market after timeout."""
        t = threading.Thread(target=self._fallback_worker, args=(product_id, order_id, original_quote_amount, timeout_seconds), daemon=True)
        t.start()

    def _fallback_worker(self, product_id, order_id, original_quote_amount, timeout_seconds):
        try:
            # Sleep until after the GTD expiry window (or chosen timeout for GTC fallback)
            time.sleep(max(1, int(timeout_seconds)) + 2)

            # Fetch order status
            order_resp = self.client.get_order(order_id)
            order_obj = getattr(order_resp, 'order', None)

            # Extract fields resiliently (typed or dict)
            status = None
            filled_value = 0.0
            filled_size = 0.0
            avg_price = None

            if isinstance(order_obj, dict):
                status = order_obj.get('status')
                fv = order_obj.get('filled_value')
                fs = order_obj.get('filled_size')
                ap = order_obj.get('average_filled_price')
            else:
                status = getattr(order_obj, 'status', None)
                fv = getattr(order_obj, 'filled_value', None)
                fs = getattr(order_obj, 'filled_size', None)
                ap = getattr(order_obj, 'average_filled_price', None)

            try:
                filled_value = float(fv) if fv is not None else 0.0
            except Exception:
                filled_value = 0.0
            try:
                filled_size = float(fs) if fs is not None else 0.0
            except Exception:
                filled_size = 0.0
            try:
                avg_price = float(ap) if ap is not None else None
            except Exception:
                avg_price = None

            # If no filled_value but have size and avg price, derive
            if filled_value == 0.0 and filled_size and avg_price:
                filled_value = filled_size * avg_price

            remaining_quote = Decimal(str(original_quote_amount)) - Decimal(str(filled_value))
            if remaining_quote <= Decimal('0'):
                logger.info(f"Fallback: Nothing remaining to buy for order {order_id}; status={status}")
                return

            # Attempt to cancel any remaining order (safe if already expired/cancelled)
            try:
                self.client.cancel_orders([order_id])
                logger.info(f"Fallback: Cancel request sent for order {order_id}")
            except Exception as e:
                logger.warning(f"Fallback: Cancel failed or not needed for {order_id}: {e}")

            # After sending cancel, poll until order reaches a terminal state, then recompute remaining
            try:
                terminal_statuses = {"CANCELLED", "EXPIRED", "FILLED", "REJECTED", "FAILED"}
                deadline = time.time() + 15  # wait up to 15s for terminal state
                latest_filled_value = filled_value
                latest_status = status
                while time.time() < deadline:
                    try:
                        ord_resp = self.client.get_order(order_id)
                        ord_obj = getattr(ord_resp, 'order', None)
                        if ord_obj is None and isinstance(ord_resp, dict):
                            ord_obj = ord_resp.get('order', ord_resp)
                        if isinstance(ord_obj, dict):
                            latest_status = ord_obj.get('status')
                            fv2 = ord_obj.get('filled_value')
                            fs2 = ord_obj.get('filled_size')
                            ap2 = ord_obj.get('average_filled_price')
                        else:
                            latest_status = getattr(ord_obj, 'status', None)
                            fv2 = getattr(ord_obj, 'filled_value', None)
                            fs2 = getattr(ord_obj, 'filled_size', None)
                            ap2 = getattr(ord_obj, 'average_filled_price', None)
                        try:
                            latest_filled_value = float(fv2) if fv2 is not None else 0.0
                        except Exception:
                            latest_filled_value = 0.0
                        # Derive from size*avg if needed
                        try:
                            fs_f = float(fs2) if fs2 is not None else 0.0
                        except Exception:
                            fs_f = 0.0
                        try:
                            ap_f = float(ap2) if ap2 is not None else None
                        except Exception:
                            ap_f = None
                        if latest_filled_value == 0.0 and fs_f and ap_f:
                            latest_filled_value = fs_f * ap_f

                        if latest_status in terminal_statuses:
                            break
                    except Exception:
                        pass
                    time.sleep(0.5)

                # Recompute remaining from latest fill
                remaining_quote = Decimal(str(original_quote_amount)) - Decimal(str(latest_filled_value))
                if remaining_quote <= Decimal('0'):
                    logger.info(f"Fallback: Nothing remaining to buy after cancel poll for order {order_id}; status={latest_status}")
                    return
            except Exception as e:
                logger.warning(f"Fallback: Poll after cancel encountered an issue, proceeding cautiously: {e}")

            # Round remaining quote by quote_increment and ensure >= quote_min_size
            try:
                product = self.client.get_product(product_id)
                quote_increment = getattr(product, 'quote_increment', None)
                quote_min_size = getattr(product, 'quote_min_size', None)

                # Use helper to quantize remaining_quote
                remaining_quote = quantize_or_round(remaining_quote, quote_increment, 2)

                if quote_min_size and Decimal(str(remaining_quote)) < Decimal(str(quote_min_size)):
                    logger.info(f"Fallback: Remaining {remaining_quote} below quote_min_size {quote_min_size} for {product_id}; skipping market buy.")
                    return
            except Exception as e:
                logger.warning(f"Fallback: Failed to prepare remaining quote rounding/min check: {e}")

            # Place market order for the remaining amount
            try:
                client_order_id = str(uuid.uuid4())
                mo = self.client.market_order_buy(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    quote_size=str(remaining_quote)
                )
                # Extract new market order ID from response (dict or typed)
                new_market_order_id = None
                try:
                    if hasattr(mo, 'success') and mo.success:
                        sr = getattr(mo, 'success_response', None)
                        if isinstance(sr, dict):
                            new_market_order_id = sr.get('order_id')
                        else:
                            new_market_order_id = getattr(sr, 'order_id', None)
                    elif isinstance(mo, dict):
                        new_market_order_id = mo.get('order_id')
                except Exception:
                    pass
                logger.info(
                    f"Fallback: Placed market buy for remaining {remaining_quote} {product_id} "
                    f"(original_order_id={order_id}, market_order_id={new_market_order_id})"
                )
            except Exception as e:
                logger.error(f"Fallback: Failed to place market buy for remaining amount: {e}")
        except Exception as e:
            logger.error(f"Fallback worker error: {e}")


if __name__ == '__main__':
    # Test the implementation
    coinbase = ConnectCoinbase()
    # Uncomment these lines to test functionality
    # coinbase.get_balance()
    # coinbase.get_product_info('BTC/USDC')
    # coinbase.create_order('BTC/USDC', 10, order_type='market')
