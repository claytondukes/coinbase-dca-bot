from coinbase.rest import RESTClient
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import uuid
import time
from decimal import Decimal, ROUND_DOWN
import threading

class ConnectCoinbase():
    """
    Class for connecting to Coinbase Advanced Trade API using the official SDK.
    This replaces the previous CCXT implementation.
    """

    def __init__(self):
        """Initialize the Coinbase connection using API keys from environment variables."""
        load_dotenv()
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
        v = os.getenv('COINBASE_VERBOSE')
        verbose_flag = False
        if v is not None:
            vv = v.strip().lower()
            verbose_flag = vv in ('1', 'true', 'yes', 'on', 'debug')
        self.verbose = verbose_flag
        self.client = RESTClient(api_key=self.api_key, api_secret=self.api_secret, verbose=self.verbose)
        
        # Verify connection by getting account information
        try:
            accounts = self.client.get_accounts()
            if accounts:
                print("API credentials verified successfully")
            else:
                print("Could not retrieve account information")
                raise RuntimeError("Could not retrieve account information during Coinbase API initialization")
        except Exception as e:
            print(f"API credentials are incorrect or not set properly: {e}")
            raise RuntimeError(f"API credentials are incorrect or not set properly: {e}")
        
        self.current_datetime = datetime.utcnow()
        print(f"Current UTC time is: {self.current_datetime}")
    
    def get_balance(self):
        """Get balance information for all accounts."""
        try:
            accounts = self.client.get_accounts()
            balances = {}
            
            for account in accounts:
                currency = account.get('currency', '')
                available_balance = account.get('available_balance', {}).get('value', '0')
                balances[currency] = {
                    'available': float(available_balance),
                    'currency': currency
                }
                print(f"{currency}: {available_balance}")
            
            return balances
        except Exception as e:
            print(f"Failed to get balance: {e}")
            return None

    def get_markets(self, currency_pair=None):
        """Get market information for a specific currency pair."""
        if currency_pair is None:
            print('No currency pair provided, using default')
            currency_pair = 'BTC/USDC'
        
        # Convert from BTC/USDC format to BTC-USDC format
        product_id = currency_pair.replace('/', '-')
        
        try:
            product = self.client.get_product(product_id)
            if product:
                # Access price as an attribute of the product object (not as a dictionary)
                # The Coinbase API returns a GetProductResponse object, not a dictionary
                price = getattr(product, 'price', None)
                # Validate price
                try:
                    price_float = float(price)
                except (TypeError, ValueError):
                    print(f"Error: Retrieved price for {product_id} is not a valid number: {price}")
                    return None
                if price_float <= 0:
                    print(f"Error: Retrieved price for {product_id} is not positive: {price_float}")
                    return None
                print(f"Retrieved {product_id} price: {price_float}")
                
                market_info = {
                    'symbol': currency_pair,
                    'id': product_id,
                    'price': price_float,
                    'base': currency_pair.split('/')[0],
                    'quote': currency_pair.split('/')[1]
                }
                print(market_info)
                return market_info
            else:
                print(f"Could not retrieve market information for {currency_pair}")
                return None
        except Exception as e:
            print(f"Failed to get market information: {e}")
            return None

    def create_order(self, currency_pair, amount_quote_currency, client_order_id=None, order_type="limit", limit_price_pct=0.01, order_timeout_seconds=600, post_only=True, max_retries=3):
        """
        Create a buy order for cryptocurrency using quote currency amount.
        
        Args:
            currency_pair (str): Currency pair in format 'BTC/USDC'
            amount_quote_currency (float): Amount of quote currency to spend
            order_type (str): Order type to create ('market' or 'limit')
            limit_price_pct (float): For limit orders, percent of 100 to set as limit price
                                    (e.g., 0.01 means 0.01% below current price)
            max_retries (int): Maximum number of retries for limit order price checks (not currently used for price checks)
            
        Returns:
            dict: Order information if successful, None otherwise
        """
        if not currency_pair or not amount_quote_currency:
            print("Currency pair and amount are required")
            return None
        
        self.current_datetime = datetime.utcnow()
        print(f"Current UTC time is: {self.current_datetime}")
        print(f'Creating {order_type} order')
        print(f'Currency pair: {currency_pair}')
        print(f'Amount of quote currency: {amount_quote_currency}')
        
        # Convert from BTC/USDC format to BTC-USDC format
        product_id = currency_pair.replace('/', '-')
        
        # Use provided client_order_id or generate a unique one using UUID
        if client_order_id is None:
            client_order_id = str(uuid.uuid4())
            print(f'Generated client_order_id: {client_order_id}')
        else:
            print(f'Using provided client_order_id: {client_order_id}')
        
        try:
            if order_type.lower() == 'market':
                # Use market order with quote size (amount of quote currency to spend)
                order = self.client.market_order_buy(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    quote_size=str(amount_quote_currency)
                )
            else:  # limit order
                # Get current market price for the product
                market_info = self.get_markets(currency_pair)
                if not market_info:
                    print(f"Could not get market price for {currency_pair}")
                    return None
                
                # Calculate limit price with discount from market price
                market_price = float(market_info['price'])
                
                # Apply discount percentage to calculate limit price
                # Default is 0.01% below market price to ensure maker status
                limit_price = market_price * (1 - (limit_price_pct / 100))
                
                # Adjust precision based on trading pair
                # Coinbase requires specific decimal precision for each pair
                product = self.client.get_product(product_id)
                price_increment = getattr(product, 'price_increment', None)
                base_increment = getattr(product, 'base_increment', None)
                quote_min_size = getattr(product, 'quote_min_size', None)
                base_min_size = getattr(product, 'base_min_size', None)

                if price_increment:
                    try:
                        limit_price = float(Decimal(str(limit_price)).quantize(Decimal(str(price_increment)), rounding=ROUND_DOWN))
                    except Exception:
                        if currency_pair.startswith('BTC'):
                            limit_price = round(limit_price, 2)
                        elif currency_pair.startswith('ETH'):
                            limit_price = round(limit_price, 2)
                        else:
                            limit_price = round(limit_price, 2)
                else:
                    if currency_pair.startswith('BTC'):
                        limit_price = round(limit_price, 2)  # BTC typically uses 2 decimal places for price
                    elif currency_pair.startswith('ETH'):
                        limit_price = round(limit_price, 2)  # ETH typically uses 2 decimal places for price
                    else:
                        limit_price = round(limit_price, 2)  # Default to 2 decimal places for other pairs
                
                print(f"Market price: {market_price}, Limit price: {limit_price}")
                print(f"Using {limit_price_pct}% discount for limit order")
                
                # Calculate base currency amount (how much crypto we're buying)
                base_size = amount_quote_currency / limit_price
                # Format to appropriate decimal places based on typical crypto requirements
                # Most exchanges require BTC to 8 decimal places, ETH to 6, etc.
                if base_increment:
                    try:
                        base_size = float(Decimal(str(base_size)).quantize(Decimal(str(base_increment)), rounding=ROUND_DOWN))
                    except Exception:
                        if currency_pair.startswith('BTC'):
                            base_size = round(base_size, 8)
                        elif currency_pair.startswith('ETH'):
                            base_size = round(base_size, 6)
                        else:
                            base_size = round(base_size, 6)  # Default precision
                else:
                    if currency_pair.startswith('BTC'):
                        base_size = round(base_size, 8)
                    elif currency_pair.startswith('ETH'):
                        base_size = round(base_size, 6)
                    else:
                        base_size = round(base_size, 6)  # Default precision

                # Validate against product minimum sizes if available
                try:
                    if quote_min_size and Decimal(str(amount_quote_currency)) < Decimal(str(quote_min_size)):
                        print(f"Amount {amount_quote_currency} is below quote_min_size {quote_min_size} for {currency_pair}")
                        return None
                except Exception:
                    pass
                try:
                    if base_min_size and Decimal(str(base_size)) < Decimal(str(base_min_size)):
                        print(f"Computed base_size {base_size} is below base_min_size {base_min_size} for {currency_pair}")
                        return None
                except Exception:
                    pass
                
                print(f"Buying {base_size} {currency_pair.split('/')[0]} at {limit_price}")
                
                # Calculate end_time for limit orders (RFC3339 format)
                end_time = (datetime.utcnow() + timedelta(seconds=order_timeout_seconds)).strftime('%Y-%m-%dT%H:%M:%SZ')
                print(f"Order will expire at: {end_time} ({order_timeout_seconds} seconds from now)")
                
                # Debug logging is configured at process startup if verbose=True
                
                try:
                    # Try placing a GTD limit order with expiration time
                    # Using explicit parameter ordering to match SDK source code exactly
                    order = self.client.limit_order_gtd(
                        client_order_id,
                        product_id,
                        "BUY",
                        str(base_size),
                        str(limit_price),
                        end_time,
                        post_only
                    )
                    print("GTD order placed successfully with expiration")
                except Exception as e:
                    print(f"GTD order failed: {e}")
                    print("Falling back to GTC order without expiration")
                    
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
                print("Order placed successfully")
                
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
                        
                        print(f"Order ID: {order_id if order_id else 'Not available'}")
                        print(f"Product: {sr_product_id}")
                        print(f"Side: {side}")
                        print(f"Client Order ID: {client_id}")

                        # Start fallback-to-market monitor only for limit orders
                        if order_type.lower() != 'market' and order_id:
                            self._start_fallback_thread(sr_product_id, order_id, amount_quote_currency, order_timeout_seconds)

                        return success_response
                    else:
                        # Handle typed success object
                        order_id = getattr(success_response, 'order_id', None)
                        sr_product_id = getattr(success_response, 'product_id', sr_product_id)
                        side = getattr(success_response, 'side', 'Unknown')
                        client_id = getattr(success_response, 'client_order_id', 'Unknown')

                        print(f"Order ID: {order_id if order_id else 'Not available'}")
                        print(f"Product: {sr_product_id}")
                        print(f"Side: {side}")
                        print(f"Client Order ID: {client_id}")

                        if order_type.lower() != 'market' and order_id:
                            self._start_fallback_thread(sr_product_id, order_id, amount_quote_currency, order_timeout_seconds)
                        
                        # Return a basic dict for consistency
                        return {
                            'order_id': order_id,
                            'product_id': sr_product_id,
                            'side': side,
                            'client_order_id': client_id
                        }
                else:
                    print("Order successful but no details available")
                
                return {}
            else:
                error_msg = order.error_response if hasattr(order, 'error_response') else 'Unknown error'
                print(f"Failed to create order: {error_msg}")
                return None
                
        except Exception as e:
            print(f'Failed to create order: {e}')
            return None

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

            remaining_quote = float(original_quote_amount) - filled_value
            if remaining_quote <= 0:
                print(f"Fallback: Nothing remaining to buy for order {order_id}; status={status}")
                return

            # Attempt to cancel any remaining order (safe if already expired/cancelled)
            try:
                self.client.cancel_orders([order_id])
                print(f"Fallback: Cancel request sent for order {order_id}")
            except Exception as e:
                print(f"Fallback: Cancel failed or not needed for {order_id}: {e}")

            # Round remaining quote by quote_increment and ensure >= quote_min_size
            try:
                product = self.client.get_product(product_id)
                quote_increment = getattr(product, 'quote_increment', None)
                quote_min_size = getattr(product, 'quote_min_size', None)

                if quote_increment:
                    try:
                        remaining_quote = float(Decimal(str(remaining_quote)).quantize(Decimal(str(quote_increment)), rounding=ROUND_DOWN))
                    except Exception:
                        # Fallback to 2 decimals for quote rounding
                        remaining_quote = round(remaining_quote, 2)
                else:
                    remaining_quote = round(remaining_quote, 2)

                if quote_min_size and Decimal(str(remaining_quote)) < Decimal(str(quote_min_size)):
                    print(f"Fallback: Remaining {remaining_quote} below quote_min_size {quote_min_size} for {product_id}; skipping market buy.")
                    return
            except Exception as e:
                print(f"Fallback: Failed to prepare remaining quote rounding/min check: {e}")

            # Place market order for the remaining amount
            try:
                client_order_id = str(uuid.uuid4())
                mo = self.client.market_order_buy(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    quote_size=str(remaining_quote)
                )
                print(f"Fallback: Placed market buy for remaining {remaining_quote} {product_id} (order_id={order_id})")
            except Exception as e:
                print(f"Fallback: Failed to place market buy for remaining amount: {e}")
        except Exception as e:
            print(f"Fallback worker error: {e}")


if __name__ == '__main__':
    # Test the implementation
    coinbase = ConnectCoinbase()
    # Uncomment these lines to test functionality
    # coinbase.get_balance()
    # coinbase.get_markets('BTC/USDC')
    # coinbase.create_order('BTC/USDC', 10)
