from coinbase.rest import RESTClient
from dotenv import load_dotenv
import os
from datetime import datetime, timedelta
import uuid
import time

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

        # Initialize the Coinbase client
        self.client = RESTClient(api_key=self.api_key, api_secret=self.api_secret)
        
        # Verify connection by getting account information
        try:
            accounts = self.client.get_accounts()
            if accounts:
                print("API credentials verified successfully")
            else:
                print("Could not retrieve account information")
                exit(1)
        except Exception as e:
            print(f"API credentials are incorrect or not set properly: {e}")
            exit(1)
        
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
                price = getattr(product, 'price', '0')
                print(f"Retrieved {product_id} price: {price}")
                
                market_info = {
                    'symbol': currency_pair,
                    'id': product_id,
                    'price': price,
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

    def create_order(self, currency_pair=None, amount_quote_currency=None, order_type='limit', limit_price_pct=0.999, order_timeout_hours=24, max_retries=3):
        """
        Create a buy order for cryptocurrency using quote currency amount.
        
        Args:
            currency_pair (str): Currency pair in format 'BTC/USDC'
            amount_quote_currency (float): Amount of quote currency to spend
            order_type (str): Order type to create ('market' or 'limit')
            limit_price_pct (float): For limit orders, percentage of current price to set as limit
                                    (e.g., 0.999 means 99.9% of market price, making you a maker)
            max_retries (int): Maximum number of retries for limit order price checks
            
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
        
        # Generate a unique client_order_id using UUID
        client_order_id = str(uuid.uuid4())
        print(f'Generated client_order_id: {client_order_id}')
        
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
                
                # Calculate limit price (slightly below market to ensure maker status)
                market_price = float(market_info['price'])
                limit_price = market_price * limit_price_pct
                print(f"Market price: {market_price}, Limit price: {limit_price}")
                
                # Calculate base currency amount (how much crypto we're buying)
                base_size = amount_quote_currency / limit_price
                # Format to appropriate decimal places based on typical crypto requirements
                # Most exchanges require BTC to 8 decimal places, ETH to 6, etc.
                # This is a simple approach - ideally we'd get product details to determine precision
                if currency_pair.startswith('BTC'):
                    base_size = round(base_size, 8)
                elif currency_pair.startswith('ETH'):
                    base_size = round(base_size, 6)
                else:
                    base_size = round(base_size, 6)  # Default precision
                
                print(f"Buying {base_size} {currency_pair.split('/')[0]} at {limit_price}")
                
                # Calculate end_time for limit orders (RFC3339 format)
                end_time = (datetime.utcnow() + timedelta(hours=order_timeout_hours)).strftime('%Y-%m-%dT%H:%M:%SZ')
                print(f"Order will expire at: {end_time}")
                
                # Place limit order using GTD (Good-Till-Date) with expiration time
                order = self.client.limit_order_gtd(
                    client_order_id=client_order_id,
                    product_id=product_id,
                    base_size=str(base_size),
                    limit_price=str(limit_price),
                    side="BUY",  # Required parameter for limit_order_gtd
                    end_time=end_time  # When the order should expire if not filled
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
                print("Order completed successfully")
                
                # Extract order details from success_response dictionary
                if hasattr(order, 'success_response'):
                    success_response = order.success_response
                    if isinstance(success_response, dict):
                        # Extract and display order details
                        order_id = success_response.get('order_id', 'Not available')
                        product_id = success_response.get('product_id', 'Unknown')
                        side = success_response.get('side', 'Unknown')
                        client_id = success_response.get('client_order_id', 'Unknown')
                        
                        print(f"Order ID: {order_id}")
                        print(f"Product: {product_id}")
                        print(f"Side: {side}")
                        print(f"Client Order ID: {client_id}")
                        
                        return success_response
                    else:
                        print("Order successful but details not available in expected format")
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


if __name__ == '__main__':
    # Test the implementation
    coinbase = ConnectCoinbase()
    # Uncomment these lines to test functionality
    # coinbase.get_balance()
    # coinbase.get_markets('BTC/USDC')
    # coinbase.create_order('BTC/USDC', 10)
