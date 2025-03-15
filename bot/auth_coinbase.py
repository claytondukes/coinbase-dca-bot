from coinbase.rest import RESTClient
from dotenv import load_dotenv
import os
from datetime import datetime
import uuid

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
                market_info = {
                    'symbol': currency_pair,
                    'id': product_id,
                    'price': product.get('price', '0'),
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

    def create_order(self, currency_pair=None, amount_quote_currency=None):
        """
        Create a market order to buy cryptocurrency using quote currency amount.
        
        Args:
            currency_pair (str): Currency pair in format 'BTC/USDC'
            amount_quote_currency (float): Amount of quote currency to spend
            
        Returns:
            dict: Order information if successful, None otherwise
        """
        if not currency_pair or not amount_quote_currency:
            print("Currency pair and amount are required")
            return None
        
        self.current_datetime = datetime.utcnow()
        print(f"Current UTC time is: {self.current_datetime}")
        print('Creating market order')
        print(f'Currency pair: {currency_pair}')
        print(f'Amount of quote currency: {amount_quote_currency}')
        
        # Convert from BTC/USDC format to BTC-USDC format
        product_id = currency_pair.replace('/', '-')
        
        # Generate a unique client_order_id using UUID
        client_order_id = str(uuid.uuid4())
        print(f'Generated client_order_id: {client_order_id}')
        
        try:
            # Use market order with quote size (amount of quote currency to spend)
            order = self.client.market_order_buy(
                client_order_id=client_order_id,
                product_id=product_id,
                quote_size=str(amount_quote_currency)
            )
            
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
