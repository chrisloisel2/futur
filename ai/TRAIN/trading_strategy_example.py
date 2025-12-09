"""
TRADING STRATEGY EXAMPLE
========================
Exemple de stratégie utilisant les signaux alpha détectés.

Stratégie Multi-Signal:
- Combine plusieurs signaux pour décisions trading
- Pondération par force du signal
- Gestion de risque intégrée
"""
import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import json
from typing import Dict, List, Tuple


class AlphaSignalTrader:
    """
    Trader automatique basé sur les signaux alpha.
    """
    
    def __init__(self, signals_path: Path, initial_capital: float = 10000):
        self.signals_path = signals_path
        self.initial_capital = initial_capital
        self.capital = initial_capital
        self.positions = {}  # {symbol: {'size': X, 'entry_price': Y, 'signal': Z}}
        self.trade_history = []
        
        # Poids des signaux (à ajuster selon backtest)
        self.signal_weights = {
            'ON_CHAIN_ACCUMULATION': 1.5,
            'EXTREME_FEAR': 1.2,
            'EXTREME_GREED': -1.2,
            'HIGH_FUNDING_RATE': -1.0,
            'NEGATIVE_FUNDING_RATE': 1.0,
            'MACRO_DECOUPLING': 0.8,
            'ORDERBOOK_BID_IMBALANCE': 1.3,
            'ORDERBOOK_ASK_IMBALANCE': -1.3,
            'SOCIAL_MOMENTUM': 0.5,
            'EXTREME_LONG_POSITIONING': -1.0,
            'EXTREME_SHORT_POSITIONING': 1.0,
        }
        
        # Multiplicateurs de force
        self.strength_multipliers = {
            'STRONG': 1.5,
            'MEDIUM': 1.0,
            'WEAK': 0.5
        }
        
    def load_signals(self) -> pd.DataFrame:
        """Charger les signaux depuis JSON."""
        signals_file = self.signals_path / "alpha_signals_report.json"
        
        if not signals_file.exists():
            print(f"No signals file found at {signals_file}")
            return pd.DataFrame()
        
        with open(signals_file, 'r') as f:
            signals_data = json.load(f)
        
        return pd.DataFrame(signals_data)
    
    def calculate_signal_score(self, asset: str, signals_df: pd.DataFrame) -> float:
        """
        Calculer le score composite pour un asset.
        
        Score > 0 = bullish, Score < 0 = bearish
        """
        if len(signals_df) == 0:
            return 0.0
        
        asset_signals = signals_df[signals_df['asset'] == asset]
        
        if len(asset_signals) == 0:
            return 0.0
        
        total_score = 0.0
        
        for _, signal in asset_signals.iterrows():
            signal_type = signal.get('signal_type', '')
            strength = signal.get('strength', 'MEDIUM')
            direction = signal.get('direction', 'NEUTRAL')
            
            # Obtenir le poids du signal
            base_weight = self.signal_weights.get(signal_type, 0.5)
            
            # Appliquer multiplicateur de force
            strength_mult = self.strength_multipliers.get(strength, 1.0)
            
            # Appliquer direction
            if direction == 'BULLISH':
                score = base_weight * strength_mult
            elif direction == 'BEARISH':
                score = -base_weight * strength_mult
            else:
                score = 0.0
            
            total_score += score
        
        return total_score
    
    def generate_trading_decisions(self, signals_df: pd.DataFrame) -> Dict[str, str]:
        """
        Générer des décisions de trading basées sur les signaux.
        
        Returns:
            Dict[asset, action] où action = 'BUY', 'SELL', 'HOLD'
        """
        decisions = {}
        
        # Obtenir tous les assets uniques
        unique_assets = signals_df['asset'].unique()
        
        for asset in unique_assets:
            score = self.calculate_signal_score(asset, signals_df)
            
            # Seuils de décision
            BUY_THRESHOLD = 2.0   # Score bullish fort
            SELL_THRESHOLD = -2.0  # Score bearish fort
            
            if score >= BUY_THRESHOLD:
                decisions[asset] = 'BUY'
            elif score <= SELL_THRESHOLD:
                decisions[asset] = 'SELL'
            else:
                decisions[asset] = 'HOLD'
        
        return decisions
    
    def calculate_position_size(self, asset: str, signal_score: float) -> float:
        """
        Calculer la taille de position basée sur Kelly Criterion simplifié.
        
        Returns:
            Fraction du capital à allouer (0.0 à 0.2 max)
        """
        # Limiter la taille de position (max 20% du capital par asset)
        max_position_size = 0.20
        
        # Position size proportionnelle au signal score
        # Score 2.0 = 10% du capital, Score 5.0 = 20% du capital
        position_fraction = min(abs(signal_score) * 0.05, max_position_size)
        
        return position_fraction
    
    def simulate_trades(self, signals_df: pd.DataFrame, current_prices: Dict[str, float]):
        """
        Simuler les trades basés sur les signaux.
        
        Args:
            signals_df: DataFrame des signaux
            current_prices: Dict des prix actuels {asset: price}
        """
        decisions = self.generate_trading_decisions(signals_df)
        
        print("\n" + "=" * 80)
        print("TRADING DECISIONS")
        print("=" * 80 + "\n")
        
        for asset, action in decisions.items():
            score = self.calculate_signal_score(asset, signals_df)
            
            print(f"\n{asset}")
            print(f"  Signal Score: {score:.2f}")
            print(f"  Decision: {action}")
            
            if action == 'BUY' and asset not in self.positions:
                # Entrer en position
                position_size = self.calculate_position_size(asset, score)
                capital_to_use = self.capital * position_size
                
                if asset in current_prices:
                    price = current_prices[asset]
                    quantity = capital_to_use / price
                    
                    self.positions[asset] = {
                        'size': quantity,
                        'entry_price': price,
                        'entry_capital': capital_to_use,
                        'signal_score': score,
                        'entry_date': datetime.utcnow()
                    }
                    
                    self.capital -= capital_to_use
                    
                    self.trade_history.append({
                        'date': datetime.utcnow(),
                        'asset': asset,
                        'action': 'BUY',
                        'price': price,
                        'quantity': quantity,
                        'capital_used': capital_to_use
                    })
                    
                    print(f"  Action: ENTER LONG")
                    print(f"  Entry Price: ${price:,.2f}")
                    print(f"  Position Size: {position_size * 100:.1f}% of capital")
                    print(f"  Quantity: {quantity:.4f}")
            
            elif action == 'SELL' and asset in self.positions:
                # Sortir de position
                position = self.positions[asset]
                
                if asset in current_prices:
                    exit_price = current_prices[asset]
                    exit_value = position['size'] * exit_price
                    pnl = exit_value - position['entry_capital']
                    pnl_pct = (pnl / position['entry_capital']) * 100
                    
                    self.capital += exit_value
                    
                    self.trade_history.append({
                        'date': datetime.utcnow(),
                        'asset': asset,
                        'action': 'SELL',
                        'price': exit_price,
                        'quantity': position['size'],
                        'pnl': pnl,
                        'pnl_pct': pnl_pct
                    })
                    
                    print(f"  Action: EXIT LONG")
                    print(f"  Exit Price: ${exit_price:,.2f}")
                    print(f"  P&L: ${pnl:,.2f} ({pnl_pct:+.2f}%)")
                    
                    del self.positions[asset]
        
        # Résumé du portefeuille
        total_position_value = sum(
            pos['size'] * current_prices.get(asset, pos['entry_price'])
            for asset, pos in self.positions.items()
        )
        
        total_portfolio_value = self.capital + total_position_value
        portfolio_return = ((total_portfolio_value - self.initial_capital) / self.initial_capital) * 100
        
        print("\n" + "=" * 80)
        print("PORTFOLIO SUMMARY")
        print("=" * 80)
        print(f"\nInitial Capital: ${self.initial_capital:,.2f}")
        print(f"Available Cash: ${self.capital:,.2f}")
        print(f"Position Value: ${total_position_value:,.2f}")
        print(f"Total Portfolio Value: ${total_portfolio_value:,.2f}")
        print(f"Portfolio Return: {portfolio_return:+.2f}%")
        
        print(f"\nOpen Positions: {len(self.positions)}")
        for asset, pos in self.positions.items():
            current_price = current_prices.get(asset, pos['entry_price'])
            current_value = pos['size'] * current_price
            unrealized_pnl = current_value - pos['entry_capital']
            unrealized_pnl_pct = (unrealized_pnl / pos['entry_capital']) * 100
            
            print(f"  {asset}: {pos['size']:.4f} @ ${pos['entry_price']:,.2f}")
            print(f"    Current: ${current_price:,.2f} | P&L: ${unrealized_pnl:,.2f} ({unrealized_pnl_pct:+.2f}%)")
    
    def generate_report(self, output_path: Path):
        """Générer un rapport de trading."""
        report = {
            'initial_capital': self.initial_capital,
            'current_capital': self.capital,
            'positions': self.positions,
            'trade_history': self.trade_history,
            'timestamp': datetime.utcnow().isoformat()
        }
        
        with open(output_path / "trading_report.json", 'w') as f:
            json.dump(report, f, indent=2, default=str)
        
        print(f"\nTrading report saved to {output_path / 'trading_report.json'}")


def main():
    """Point d'entrée principal."""
    
    # Trouver le dataset le plus récent
    datasets_path = Path("datasets/alpha_trading")
    dataset_folders = sorted(datasets_path.glob("dataset_*"), reverse=True)
    
    if not dataset_folders:
        print("No dataset found. Run mass_data_collector_v2.py first.")
        return
    
    latest_dataset = dataset_folders[0]
    print(f"Using dataset: {latest_dataset.name}\n")
    
    # Initialiser le trader
    trader = AlphaSignalTrader(latest_dataset, initial_capital=10000)
    
    # Charger les signaux
    signals = trader.load_signals()
    
    if len(signals) == 0:
        print("No signals found. Run alpha_signal_analyzer.py first.")
        return
    
    print(f"Loaded {len(signals)} alpha signals\n")
    
    # Prix actuels (simulés - dans la vraie vie, récupérer via API)
    current_prices = {
        'BTC': 45000,
        'ETH': 2500,
        'SOL': 100,
        'BNB': 350,
        'XRP': 0.60,
        'BTC/USDT': 45000,
        'ETH/USDT': 2500,
        'SOL/USDT': 100,
    }
    
    # Simuler les trades
    trader.simulate_trades(signals, current_prices)
    
    # Générer le rapport
    trader.generate_report(latest_dataset)


if __name__ == "__main__":
    main()
