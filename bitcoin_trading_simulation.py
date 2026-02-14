import numpy as np
import pandas as pd

def simulate_bitcoin_prices(days=60, initial_price=50000, mu=0.1, sigma=0.8):
    """
    Simulates Bitcoin prices using Geometric Brownian Motion.

    Parameters:
    days (int): Number of days to simulate.
    initial_price (float): Starting price of Bitcoin.
    mu (float): Expected annual return (drift).
    sigma (float): Annual volatility.

    Returns:
    pd.DataFrame: DataFrame containing Date and Price.
    """
    np.random.seed(42) # For reproducibility
    dt = 1/365 # Time step in years
    prices = [initial_price]

    for _ in range(days - 1):
        # GBM formula: S_t = S_{t-1} * exp((mu - 0.5 * sigma^2) * dt + sigma * sqrt(dt) * Z)
        price = prices[-1] * np.exp((mu - 0.5 * sigma**2) * dt + sigma * np.sqrt(dt) * np.random.normal())
        prices.append(price)

    dates = pd.date_range(start='2023-01-01', periods=days)
    return pd.DataFrame({'Date': dates, 'Price': prices})

def trading_algorithm(df):
    """
    Calculates Moving Averages and implements Golden Cross trading strategy.

    Parameters:
    df (pd.DataFrame): DataFrame with price data.

    Returns:
    pd.DataFrame: Ledger of trades and daily stats.
    """
    # Calculate Moving Averages
    df['SMA_7'] = df['Price'].rolling(window=7).mean()
    df['SMA_30'] = df['Price'].rolling(window=30).mean()

    cash = 10000.0
    holdings = 0.0
    portfolio_value = []
    ledger = []

    position = "Neutral" # Neutral (Cash) or Long (Bitcoin)

    for i in range(len(df)):
        date = df.loc[i, 'Date']
        price = df.loc[i, 'Price']
        sma_7 = df.loc[i, 'SMA_7']
        sma_30 = df.loc[i, 'SMA_30']

        action = "Hold"

        # Golden Cross Logic
        # Buy when SMA_7 > SMA_30
        # Sell when SMA_7 < SMA_30
        # Only trade if both SMAs are available

        if pd.notna(sma_7) and pd.notna(sma_30):
            if sma_7 > sma_30 and position == "Neutral":
                # Buy Signal (Golden Cross)
                holdings = cash / price
                cash = 0
                position = "Long"
                action = "Buy"
            elif sma_7 < sma_30 and position == "Long":
                # Sell Signal (Death Cross)
                cash = holdings * price
                holdings = 0
                position = "Neutral"
                action = "Sell"

        # Calculate current portfolio value
        current_val = cash + holdings * price
        portfolio_value.append(current_val)

        ledger.append({
            'Date': date.strftime('%Y-%m-%d'),
            'Price': f"${price:.2f}",
            'SMA_7': f"${sma_7:.2f}" if pd.notna(sma_7) else "NaN",
            'SMA_30': f"${sma_30:.2f}" if pd.notna(sma_30) else "NaN",
            'Action': action,
            'Portfolio Value': f"${current_val:.2f}"
        })

    return pd.DataFrame(ledger)

if __name__ == "__main__":
    # Simulate Data
    print("Simulating Bitcoin prices...")
    df = simulate_bitcoin_prices()

    # Run Trading Algorithm
    print("Running Golden Cross algorithm...")
    ledger = trading_algorithm(df)

    # Print Ledger
    print("\nDAILY TRADING LEDGER")
    print("-" * 110)
    # Adjust pandas display options to show all columns
    with pd.option_context('display.max_rows', None, 'display.max_columns', None, 'display.width', 120):
        print(ledger.to_string(index=False))
    print("-" * 110)

    # Calculate Final Performance
    initial_val = 10000.0
    # Clean string currency format back to float for calculation
    final_val_str = ledger.iloc[-1]['Portfolio Value']
    final_val = float(final_val_str.replace('$', '').replace(',', ''))

    return_pct = ((final_val - initial_val) / initial_val) * 100

    print(f"\nFINAL PORTFOLIO PERFORMANCE")
    print(f"Initial Portfolio Value: ${initial_val:.2f}")
    print(f"Final Portfolio Value:   ${final_val:.2f}")
    print(f"Total Return:            {return_pct:.2f}%")
