from coinbase.rest import RESTClient
import os
from datetime import datetime, timedelta
import uuid
import time
from decimal import Decimal, ROUND_DOWN
import threading
import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

def parse_bool_env(name, default=False):
    v = os.getenv(name)
    if v is None:
        return default
    return v.strip().lower() in ('1', 'true', 'yes', 'on', 'debug')

def safe_float(value, default=0.0):
    """Safely convert to float, returning default on failure."""
    try:
        return float(value) if value is not None else default
    except Exception:
        return default

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

@dataclass
class RepriceConfig:
    limit_price_pct: float
    post_only: bool
    reprice_interval_seconds: int
    reprice_duration_seconds: int
    timeout_seconds: int
    disable_fallback: bool = False

class ConnectCoinbase():
    """
    Class for connecting to Coinbase Advanced Trade API using the official SDK.
    This replaces the previous CCXT implementation.
    """

    TERMINAL_STATUSES = {"CANCELLED", "EXPIRED", "FILLED", "REJECTED", "FAILED"}
    DEFAULT_TIMEOUT_SECONDS = 600
    REPRICE_WAIT_MAX = 10
    REPRICE_WAIT_MIN = 3
    REPRICE_POLL_SLEEP = 0.3
    REPRICE_MAX_ITERATIONS = 1000

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

    def create_order(self, currency_pair, amount_quote_currency, client_order_id=None, order_type="limit", limit_price_pct=0.01, order_timeout_seconds=600, post_only=True, max_retries=3, reprice_interval_seconds=None, reprice_duration_seconds=None, limit_price_absolute=None, time_in_force=None, disable_fallback=False):
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
                
                # Determine limit price: absolute if provided, else percentage below market
                market_price = product_info['price']
                using_absolute = False
                if limit_price_absolute is not None:
                    using_absolute = True
                    limit_price = Decimal(str(limit_price_absolute))
                    try:
                        # Warn if absolute limit is >5% above market for BUY orders
                        mp5 = (Decimal(str(market_price)) * Decimal('1.05'))
                        if limit_price > mp5:
                            logger.warning(
                                f"Limit price {limit_price} is more than 5% above market price {market_price}. "
                                f"This may result in unnecessary overpayment for the buy order."
                            )
                    except Exception:
                        pass
                else:
                    limit_price = market_price * (1 - (limit_price_pct / 100))
                
                # Quantize limit_price using helper
                limit_price = quantize_or_round(limit_price, product_info['price_increment'], 2)
                
                logger.info(f"Market price: {market_price}, Limit price: {limit_price}")
                if using_absolute:
                    logger.info("Using absolute limit price mode")
                else:
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
                
                tif = str(time_in_force).upper() if time_in_force else None
                if tif == 'GTC':
                    order = self.client.limit_order_gtc(
                        client_order_id=client_order_id,
                        product_id=product_id,
                        base_size=str(base_size),
                        limit_price=str(limit_price),
                        side="BUY",
                        post_only=post_only
                    )
                else:
                    try:
                        order = self.client.limit_order_gtd(
                            client_order_id=client_order_id,
                            product_id=product_id,
                            side="BUY",
                            base_size=str(base_size),
                            limit_price=str(limit_price),
                            end_time=end_time,
                            post_only=post_only
                        )
                    except Exception as e:
                        logger.warning(f"GTD order failed: {e}")
                        logger.warning("Falling back to GTC order without expiration")
                        order = self.client.limit_order_gtc(
                            client_order_id=client_order_id,
                            product_id=product_id,
                            base_size=str(base_size),
                            limit_price=str(limit_price),
                            side="BUY",
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
                            cfg = self._build_reprice_config(
                                limit_price_pct,
                                post_only,
                                reprice_interval_seconds,
                                reprice_duration_seconds,
                                order_timeout_seconds,
                                disable_fallback,
                            )
                            self._start_reprice_or_fallback_thread(
                                sr_product_id, order_id, amount_quote_currency, cfg
                            )

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
                            cfg = RepriceConfig(
                                limit_price_pct=limit_price_pct,
                                post_only=post_only,
                                reprice_interval_seconds=int(reprice_interval_seconds) if reprice_interval_seconds is not None else 0,
                                reprice_duration_seconds=(
                                    int(reprice_duration_seconds) if reprice_duration_seconds is not None
                                    else int(order_timeout_seconds) if order_timeout_seconds is not None
                                    else 600
                                ),
                                timeout_seconds=int(order_timeout_seconds) if order_timeout_seconds is not None else 600,
                                disable_fallback=bool(disable_fallback)
                            )
                            self._start_reprice_or_fallback_thread(
                                sr_product_id,
                                order_id,
                                amount_quote_currency,
                                cfg
                            )
                        
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
                # If post-only was rejected due to crossing, nudge price down one tick and retry once using helpers
                error_code, err_text = self._extract_error_info(order)
                if (error_code == 'INVALID_LIMIT_PRICE_POST_ONLY') or (error_code is None and ('INVALID_LIMIT_PRICE_POST_ONLY' in err_text or 'POST_ONLY' in err_text)):
                    order2, adjusted_price = self._retry_post_only_with_nudge(product_id, base_size, limit_price, end_time, post_only, product_info)
                    if order2 is not None:
                        if hasattr(order2, 'success') and order2.success:
                            logger.info(f"Adjusted post-only price down one tick to {adjusted_price} and retried successfully")
                        # In both cases, adopt the retry result and let unified error handling below decide
                        order = order2

                if not (hasattr(order, 'success') and order.success):
                    error_code, error_msg = self._extract_error_info(order)
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

    def _start_reprice_or_fallback_thread(self, product_id, order_id, original_quote_amount, config: RepriceConfig):
        try:
            ri = int(config.reprice_interval_seconds) if config.reprice_interval_seconds else 0
        except Exception:
            ri = 0
        if ri > 0:
            t = threading.Thread(
                target=self._reprice_and_fallback_worker,
                args=(product_id, order_id, original_quote_amount, config),
                daemon=True,
            )
            t.start()
        else:
            if not getattr(config, 'disable_fallback', False):
                self._start_fallback_thread(product_id, order_id, original_quote_amount, config.timeout_seconds)

    def _build_reprice_config(self, limit_price_pct, post_only, reprice_interval_seconds, reprice_duration_seconds, order_timeout_seconds, disable_fallback) -> RepriceConfig:
        try:
            ri = int(reprice_interval_seconds) if reprice_interval_seconds is not None else 0
        except Exception:
            ri = 0
        try:
            base_timeout = int(order_timeout_seconds) if order_timeout_seconds is not None else self.DEFAULT_TIMEOUT_SECONDS
        except Exception:
            base_timeout = self.DEFAULT_TIMEOUT_SECONDS
        try:
            rd = int(reprice_duration_seconds) if reprice_duration_seconds is not None else base_timeout
        except Exception:
            rd = base_timeout
        return RepriceConfig(
            limit_price_pct=limit_price_pct,
            post_only=post_only,
            reprice_interval_seconds=ri,
            reprice_duration_seconds=rd,
            timeout_seconds=base_timeout,
            disable_fallback=bool(disable_fallback),
        )

    def _extract_error_info(self, resp):
        error_code = None
        error_text = 'Unknown error'
        try:
            if hasattr(resp, 'error_response'):
                err_resp = resp.error_response
                if isinstance(err_resp, dict):
                    error_code = err_resp.get('error') or err_resp.get('code')
                    error_text = str(err_resp)
                else:
                    error_code = getattr(err_resp, 'error', None) or getattr(err_resp, 'code', None)
                    error_text = str(err_resp)
            elif isinstance(resp, dict):
                error_code = resp.get('error') or resp.get('code')
                error_text = str(resp)
            else:
                error_text = str(resp)
        except Exception:
            pass
        return error_code, error_text

    def _retry_post_only_with_nudge(self, product_id, base_size, price, end_time, post_only, product_info):
        tick = product_info.get('price_increment')
        if not tick:
            return None, None
        lp2 = (Decimal(str(price)) - Decimal(str(tick)))
        lp2 = quantize_or_round(lp2, product_info['price_increment'], 2)
        try:
            order2 = self.client.limit_order_gtd(
                client_order_id=str(uuid.uuid4()),
                product_id=product_id,
                side="BUY",
                base_size=str(base_size),
                limit_price=str(lp2),
                end_time=end_time,
                post_only=post_only
            )
        except Exception:
            order2 = self.client.limit_order_gtc(
                client_order_id=str(uuid.uuid4()),
                product_id=product_id,
                base_size=str(base_size),
                limit_price=str(lp2),
                side="BUY",
                post_only=post_only
            )
        return order2, lp2

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

            filled_value = safe_float(fv, 0.0)
            filled_size = safe_float(fs, 0.0)
            avg_price = safe_float(ap, None)

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
                terminal_statuses = self.TERMINAL_STATUSES
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
                        latest_filled_value = safe_float(fv2, 0.0)
                        # Derive from size*avg if needed
                        fs_f = safe_float(fs2, 0.0)
                        ap_f = safe_float(ap2, None)
                        if latest_filled_value == 0.0 and fs_f and ap_f:
                            latest_filled_value = fs_f * ap_f

                        if latest_status in terminal_statuses:
                            break
                    except Exception:
                        pass
                    time.sleep(self.REPRICE_POLL_SLEEP)

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

    def _reprice_and_fallback_worker(self, product_id, order_id, original_quote_amount, config: RepriceConfig):
        try:
            currency_pair = product_id.replace('-', '/')
            if config.reprice_duration_seconds:
                duration_seconds = int(config.reprice_duration_seconds)
            else:
                duration_seconds = int(config.timeout_seconds)
            deadline = time.time() + max(1, duration_seconds)
            current_order_id = order_id
            status = None
            first_cycle = True
            max_iterations = getattr(config, 'max_reprice_iterations', self.REPRICE_MAX_ITERATIONS)
            iteration_count = 0
            while time.time() < deadline and iteration_count < max_iterations:
                # Initial wait before first cancel to give the fresh order a chance to rest
                if first_cycle:
                    # Ensure at least 1s sleep so a fresh post-only limit can rest
                    interval = int(config.reprice_interval_seconds or 0)
                    initial_sleep = min(max(1, interval), max(0, int(deadline - time.time())))
                    time.sleep(initial_sleep)
                    first_cycle = False
                try:
                    ord_resp = self.client.get_order(current_order_id)
                    ord_obj = getattr(ord_resp, 'order', None)
                    if ord_obj is None and isinstance(ord_resp, dict):
                        ord_obj = ord_resp.get('order', ord_resp)
                    if isinstance(ord_obj, dict):
                        status = ord_obj.get('status')
                        fv = ord_obj.get('filled_value')
                        fs = ord_obj.get('filled_size')
                        ap = ord_obj.get('average_filled_price')
                    else:
                        status = getattr(ord_obj, 'status', None)
                        fv = getattr(ord_obj, 'filled_value', None)
                        fs = getattr(ord_obj, 'filled_size', None)
                        ap = getattr(ord_obj, 'average_filled_price', None)
                    filled_value = safe_float(fv, 0.0)
                    fs_f = safe_float(fs, 0.0)
                    ap_f = safe_float(ap, None)
                    if filled_value == 0.0 and fs_f and ap_f:
                        filled_value = fs_f * ap_f
                    remaining_quote = Decimal(str(original_quote_amount)) - Decimal(str(filled_value))
                    if remaining_quote <= Decimal('0'):
                        logger.info(f"Reprice: Nothing remaining to buy for order {current_order_id}; status={status}")
                        return
                except Exception:
                    status = None
                    remaining_quote = Decimal(str(original_quote_amount))

                try:
                    self.client.cancel_orders([current_order_id])
                    logger.info(f"Reprice: Cancel request sent for order {current_order_id}")
                except Exception as e:
                    logger.warning(f"Reprice: Cancel failed or not needed for {current_order_id}: {e}")

                try:
                    term = self.TERMINAL_STATUSES
                    wait_deadline = time.time() + max(self.REPRICE_WAIT_MIN, min(self.REPRICE_WAIT_MAX, int(config.reprice_interval_seconds)))
                    latest_filled_value = Decimal(str(original_quote_amount)) - remaining_quote
                    latest_status = status
                    while time.time() < wait_deadline:
                        try:
                            ord_resp2 = self.client.get_order(current_order_id)
                            ord_obj2 = getattr(ord_resp2, 'order', None)
                            if ord_obj2 is None and isinstance(ord_resp2, dict):
                                ord_obj2 = ord_resp2.get('order', ord_resp2)
                            if isinstance(ord_obj2, dict):
                                latest_status = ord_obj2.get('status')
                                fv2 = ord_obj2.get('filled_value')
                                fs2 = ord_obj2.get('filled_size')
                                ap2 = ord_obj2.get('average_filled_price')
                            else:
                                latest_status = getattr(ord_obj2, 'status', None)
                                fv2 = getattr(ord_obj2, 'filled_value', None)
                                fs2 = getattr(ord_obj2, 'filled_size', None)
                                ap2 = getattr(ord_obj2, 'average_filled_price', None)
                            latest_filled_value = safe_float(fv2, 0.0)
                            fs2f = safe_float(fs2, 0.0)
                            ap2f = safe_float(ap2, None)
                            if latest_filled_value == 0.0 and fs2f and ap2f:
                                latest_filled_value = fs2f * ap2f
                            if latest_status in term:
                                break
                        except Exception:
                            pass
                        time.sleep(self.REPRICE_POLL_SLEEP)
                    remaining_quote = Decimal(str(original_quote_amount)) - Decimal(str(latest_filled_value))
                    if remaining_quote <= Decimal('0'):
                        logger.info(f"Reprice: Nothing remaining to buy after cancel for order {current_order_id}; status={latest_status}")
                        return
                except Exception:
                    pass

                pi = self.get_product_info(currency_pair)
                if not pi:
                    logger.warning(f"Reprice: Could not get product info for {currency_pair}")
                    break
                try:
                    mp = Decimal(str(pi['price']))
                    pct = Decimal(str(config.limit_price_pct)) / Decimal('100')
                    lp = mp * (Decimal('1') - pct)
                    lp = quantize_or_round(lp, pi['price_increment'], 2)
                    bs = (Decimal(str(remaining_quote)) / Decimal(str(lp)))
                    bs = quantize_or_round(bs, pi['base_increment'], 8)
                    if pi['base_min_size'] and bs < Decimal(str(pi['base_min_size'])):
                        logger.info(f"Reprice: Base size {bs} below base_min_size {pi['base_min_size']}")
                        return
                    try:
                        mqs = pi.get('quote_min_size')
                        if mqs is not None:
                            notional = (Decimal(str(bs)) * Decimal(str(lp)))
                            if notional < Decimal(str(mqs)):
                                logger.info(f"Reprice: Notional {notional} below quote_min_size {mqs}")
                                return
                    except Exception:
                        pass
                    slice_left = int(max(1, min(int(config.reprice_interval_seconds), int(deadline - time.time()))))
                    end_time = (datetime.utcnow() + timedelta(seconds=slice_left)).strftime('%Y-%m-%dT%H:%M:%SZ')
                    try:
                        new_order = self.client.limit_order_gtd(
                            client_order_id=str(uuid.uuid4()),
                            product_id=product_id,
                            side="BUY",
                            base_size=str(bs),
                            limit_price=str(lp),
                            end_time=end_time,
                            post_only=config.post_only
                        )
                    except Exception:
                        new_order = self.client.limit_order_gtc(
                            client_order_id=str(uuid.uuid4()),
                            product_id=product_id,
                            base_size=str(bs),
                            limit_price=str(lp),
                            side="BUY",
                            post_only=config.post_only
                        )
                    try:
                        new_id = None
                        if hasattr(new_order, 'success') and new_order.success:
                            sr = getattr(new_order, 'success_response', None)
                            if isinstance(sr, dict):
                                new_id = sr.get('order_id')
                            else:
                                new_id = getattr(sr, 'order_id', None)
                        elif isinstance(new_order, dict):
                            new_id = new_order.get('order_id')

                        # If post-only invalid, nudge price down one tick and try once using helpers
                        if not new_id:
                            err_code, err_text = self._extract_error_info(new_order)
                            if (err_code == 'INVALID_LIMIT_PRICE_POST_ONLY') or (err_code is None and ('INVALID_LIMIT_PRICE_POST_ONLY' in err_text or 'POST_ONLY' in err_text)):
                                new_order2, lp2 = self._retry_post_only_with_nudge(
                                    product_id, bs, lp, end_time, config.post_only, pi
                                )
                                if new_order2 is not None and hasattr(new_order2, 'success') and new_order2.success:
                                    sr2 = getattr(new_order2, 'success_response', None)
                                    if isinstance(sr2, dict):
                                        new_id = sr2.get('order_id')
                                    else:
                                        new_id = getattr(sr2, 'order_id', None)
                                    lp = lp2
                        if new_id:
                            current_order_id = new_id
                            logger.info(f"Reprice: Placed refreshed limit {product_id} at {lp} for remaining {remaining_quote}")
                        else:
                            # Prefer structured code when available; fall back to error text
                            err_code, err_text = self._extract_error_info(new_order)
                            logger.warning(f"Reprice: Refresh failed (code={err_code}): {err_text}")
                    except Exception:
                        pass

                    sleep_left = deadline - time.time()
                    if sleep_left <= 0:
                        break
                    time.sleep(max(1, min(int(config.reprice_interval_seconds), int(sleep_left))))

                except Exception as e:
                    logger.warning(f"Reprice: Failed to reprice {product_id}: {e}")
                
                # advance iteration guard
                iteration_count += 1

            try:
                ord_resp = self.client.get_order(current_order_id)
                ord_obj = getattr(ord_resp, 'order', None)
                if ord_obj is None and isinstance(ord_resp, dict):
                    ord_obj = ord_resp.get('order', ord_resp)
                if isinstance(ord_obj, dict):
                    status = ord_obj.get('status')
                    fv = ord_obj.get('filled_value')
                    fs = ord_obj.get('filled_size')
                    ap = ord_obj.get('average_filled_price')
                else:
                    status = getattr(ord_obj, 'status', None)
                    fv = getattr(ord_obj, 'filled_value', None)
                    fs = getattr(ord_obj, 'filled_size', None)
                    ap = getattr(ord_obj, 'average_filled_price', None)
                filled_value = safe_float(fv, 0.0)
                fs_f = safe_float(fs, 0.0)
                ap_f = safe_float(ap, None)
                if filled_value == 0.0 and fs_f and ap_f:
                    filled_value = fs_f * ap_f
                remaining_quote = Decimal(str(original_quote_amount)) - Decimal(str(filled_value))
                if remaining_quote <= Decimal('0'):
                    logger.info(f"Reprice: Nothing remaining to buy for order {current_order_id}; status={status}")
                    return
            except Exception:
                remaining_quote = Decimal(str(original_quote_amount))

            try:
                self.client.cancel_orders([current_order_id])
                logger.info(f"Reprice: Final cancel sent for order {current_order_id}")
            except Exception as e:
                logger.warning(f"Reprice: Final cancel failed or not needed for {current_order_id}: {e}")

            try:
                term = self.TERMINAL_STATUSES
                wait_deadline = time.time() + self.REPRICE_WAIT_MAX
                latest_filled_value = Decimal(str(original_quote_amount)) - remaining_quote
                latest_status = status
                while time.time() < wait_deadline:
                    try:
                        ord_resp2 = self.client.get_order(current_order_id)
                        ord_obj2 = getattr(ord_resp2, 'order', None)
                        if ord_obj2 is None and isinstance(ord_resp2, dict):
                            ord_obj2 = ord_resp2.get('order', ord_resp2)
                        if isinstance(ord_obj2, dict):
                            latest_status = ord_obj2.get('status')
                            fv2 = ord_obj2.get('filled_value')
                            fs2 = ord_obj2.get('filled_size')
                            ap2 = ord_obj2.get('average_filled_price')
                        else:
                            latest_status = getattr(ord_obj2, 'status', None)
                            fv2 = getattr(ord_obj2, 'filled_value', None)
                            fs2 = getattr(ord_obj2, 'filled_size', None)
                            ap2 = getattr(ord_obj2, 'average_filled_price', None)
                        latest_filled_value = safe_float(fv2, 0.0)
                        fs2f = safe_float(fs2, 0.0)
                        ap2f = safe_float(ap2, None)
                        if latest_filled_value == 0.0 and fs2f and ap2f:
                            latest_filled_value = fs2f * ap2f
                        if latest_status in term:
                            break
                    except Exception:
                        pass
                    time.sleep(self.REPRICE_POLL_SLEEP)
                remaining_quote = Decimal(str(original_quote_amount)) - Decimal(str(latest_filled_value))
                if remaining_quote <= Decimal('0'):
                    logger.info(f"Reprice: Nothing remaining after final cancel for order {current_order_id}; status={latest_status}")
                    return
            except Exception:
                pass

            # Respect disable_fallback: do not place a market buy for remaining amount
            try:
                if getattr(config, 'disable_fallback', False):
                    logger.info(
                        f"Reprice: Fallback disabled; leaving remaining notional unfilled for {product_id}. "
                        f"Remaining quote: {remaining_quote}"
                    )
                    return
            except Exception:
                # If config is malformed, proceed with safe default behavior
                pass

            try:
                product = self.client.get_product(product_id)
                quote_increment = getattr(product, 'quote_increment', None)
                quote_min_size = getattr(product, 'quote_min_size', None)
                remaining_quote = quantize_or_round(remaining_quote, quote_increment, 2)
                if quote_min_size and Decimal(str(remaining_quote)) < Decimal(str(quote_min_size)):
                    logger.info(f"Reprice: Remaining {remaining_quote} below quote_min_size {quote_min_size} for {product_id}; skipping market buy.")
                    return
            except Exception as e:
                logger.warning(f"Reprice: Failed to prepare remaining quote rounding/min check: {e}")

            try:
                client_order_id = str(uuid.uuid4())
                mo = self.client.market_order_buy(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    quote_size=str(remaining_quote)
                )
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
                    f"Reprice: Placed market buy for remaining {remaining_quote} {product_id} "
                    f"(original_order_id={order_id}, market_order_id={new_market_order_id})"
                )
            except Exception as e:
                logger.error(f"Reprice: Failed to place market buy for remaining amount: {e}")
        except Exception as e:
            logger.error(f"Reprice worker error: {e}")


if __name__ == '__main__':
    # Test the implementation
    coinbase = ConnectCoinbase()
    # Uncomment these lines to test functionality
    # coinbase.get_balance()
    # coinbase.get_product_info('BTC/USDC')
    # coinbase.create_order('BTC/USDC', 10, order_type='market')
