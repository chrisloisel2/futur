"""
SIGNAL VISUALIZATION
====================
Visualise les signaux alpha de manière claire et actionnable.
"""
import pandas as pd
import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List


class SignalVisualizer:
    """Visualisateur de signaux alpha."""
    
    def __init__(self, dataset_path: Path):
        self.dataset_path = dataset_path
        self.signals = self.load_signals()
    
    def load_signals(self) -> List[Dict]:
        """Charger les signaux."""
        signals_file = self.dataset_path / "alpha_signals_report.json"
        
        if not signals_file.exists():
            return []
        
        with open(signals_file, 'r') as f:
            return json.load(f)
    
    def print_dashboard(self):
        """Afficher un dashboard ASCII des signaux."""
        
        if not self.signals:
            print("No signals found!")
            return
        
        df = pd.DataFrame(self.signals)
        
        # Header
        print("\n" + "═" * 100)
        print("                          🚀 ALPHA SIGNALS DASHBOARD 🚀")
        print("═" * 100)
        
        # Summary
        print(f"\n📊 SUMMARY")
        print("─" * 100)
        print(f"Total Signals: {len(df)}")
        
        if 'direction' in df.columns:
            bullish = len(df[df['direction'] == 'BULLISH'])
            bearish = len(df[df['direction'] == 'BEARISH'])
            neutral = len(df[df['direction'] == 'NEUTRAL'])
            
            print(f"  🟢 Bullish: {bullish}  ({bullish/len(df)*100:.1f}%)")
            print(f"  🔴 Bearish: {bearish}  ({bearish/len(df)*100:.1f}%)")
            print(f"  ⚪ Neutral: {neutral}  ({neutral/len(df)*100:.1f}%)")
        
        if 'strength' in df.columns:
            strong = len(df[df['strength'] == 'STRONG'])
            medium = len(df[df['strength'] == 'MEDIUM'])
            
            print(f"\n  💪 Strong: {strong}")
            print(f"  ✋ Medium: {medium}")
        
        # Top Assets
        print(f"\n🏆 TOP ASSETS BY SIGNAL COUNT")
        print("─" * 100)
        
        if 'asset' in df.columns:
            top_assets = df['asset'].value_counts().head(10)
            
            for i, (asset, count) in enumerate(top_assets.items(), 1):
                bullish_count = len(df[(df['asset'] == asset) & (df['direction'] == 'BULLISH')])
                bearish_count = len(df[(df['asset'] == asset) & (df['direction'] == 'BEARISH')])
                
                bar_length = int(count / top_assets.max() * 30)
                bar = "█" * bar_length
                
                direction_emoji = "🟢" if bullish_count > bearish_count else "🔴" if bearish_count > bullish_count else "⚪"
                
                print(f"  {i:2d}. {direction_emoji} {asset:15s} {bar:30s} {count} signals (↑{bullish_count} ↓{bearish_count})")
        
        # Strong Signals
        strong_signals = df[df['strength'] == 'STRONG'] if 'strength' in df.columns else df
        
        if len(strong_signals) > 0:
            print(f"\n⚡ STRONG SIGNALS ({len(strong_signals)})")
            print("─" * 100)
            
            for i, signal in strong_signals.head(15).iterrows():
                direction_emoji = {
                    'BULLISH': '🟢',
                    'BEARISH': '🔴',
                    'NEUTRAL': '⚪'
                }.get(signal.get('direction', 'NEUTRAL'), '⚪')
                
                asset = signal.get('asset', 'N/A')
                signal_type = signal.get('signal_type', 'UNKNOWN').replace('_', ' ').title()
                reasoning = signal.get('reasoning', 'No reasoning provided')
                
                print(f"\n  {direction_emoji} {asset} - {signal_type}")
                print(f"     └─ {reasoning}")
        
        # Signal Type Breakdown
        print(f"\n📈 SIGNAL TYPE BREAKDOWN")
        print("─" * 100)
        
        if 'signal_type' in df.columns:
            signal_counts = df['signal_type'].value_counts()
            
            for signal_type, count in signal_counts.items():
                formatted_type = signal_type.replace('_', ' ').title()
                bar_length = int(count / signal_counts.max() * 40)
                bar = "▓" * bar_length
                
                print(f"  {formatted_type:30s} {bar:40s} {count}")
        
        # Trading Recommendations
        print(f"\n💡 ACTIONABLE TRADES")
        print("─" * 100)
        
        # Calculer les scores par asset
        asset_scores = {}
        
        for _, signal in df.iterrows():
            asset = signal.get('asset', 'N/A')
            direction = signal.get('direction', 'NEUTRAL')
            strength = signal.get('strength', 'MEDIUM')
            
            if asset not in asset_scores:
                asset_scores[asset] = 0
            
            # Score calculation
            base_score = 1.5 if strength == 'STRONG' else 1.0
            
            if direction == 'BULLISH':
                asset_scores[asset] += base_score
            elif direction == 'BEARISH':
                asset_scores[asset] -= base_score
        
        # Trier par score absolu
        sorted_assets = sorted(asset_scores.items(), key=lambda x: abs(x[1]), reverse=True)
        
        print("\n  📊 Recommended Actions:")
        print()
        
        for asset, score in sorted_assets[:10]:
            if score > 2.0:
                action = "BUY 🟢"
                confidence = "HIGH" if score > 3.0 else "MEDIUM"
            elif score < -2.0:
                action = "SELL 🔴"
                confidence = "HIGH" if score < -3.0 else "MEDIUM"
            else:
                action = "HOLD ⚪"
                confidence = "LOW"
            
            score_bar_length = int(abs(score) * 5)
            score_bar = "█" * score_bar_length
            
            print(f"  {asset:15s} → {action:12s} (Score: {score:+.1f}) [{confidence}] {score_bar}")
        
        # Footer
        print("\n" + "═" * 100)
        print(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}")
        print("═" * 100 + "\n")
    
    def export_trading_plan(self):
        """Exporter un plan de trading actionnable."""
        
        if not self.signals:
            print("No signals to export")
            return
        
        df = pd.DataFrame(self.signals)
        
        # Calculer les scores
        asset_scores = {}
        asset_signals_detail = {}
        
        for _, signal in df.iterrows():
            asset = signal.get('asset', 'N/A')
            direction = signal.get('direction', 'NEUTRAL')
            strength = signal.get('strength', 'MEDIUM')
            signal_type = signal.get('signal_type', 'UNKNOWN')
            
            if asset not in asset_scores:
                asset_scores[asset] = 0
                asset_signals_detail[asset] = []
            
            base_score = 1.5 if strength == 'STRONG' else 1.0
            
            if direction == 'BULLISH':
                asset_scores[asset] += base_score
            elif direction == 'BEARISH':
                asset_scores[asset] -= base_score
            
            asset_signals_detail[asset].append({
                'type': signal_type,
                'direction': direction,
                'strength': strength,
                'reasoning': signal.get('reasoning', '')
            })
        
        # Générer le plan
        trading_plan = []
        
        for asset, score in asset_scores.items():
            if abs(score) >= 2.0:  # Seuil de trading
                action = 'BUY' if score > 0 else 'SELL'
                confidence = 'HIGH' if abs(score) > 3.0 else 'MEDIUM'
                
                trading_plan.append({
                    'asset': asset,
                    'action': action,
                    'score': score,
                    'confidence': confidence,
                    'signals': asset_signals_detail[asset]
                })
        
        # Sauvegarder
        output_file = self.dataset_path / "trading_plan.json"
        with open(output_file, 'w') as f:
            json.dump(trading_plan, f, indent=2)
        
        print(f"\n✓ Trading plan exported to {output_file}")
        
        # Afficher le plan
        print("\n" + "=" * 80)
        print("TRADING PLAN")
        print("=" * 80 + "\n")
        
        for plan in sorted(trading_plan, key=lambda x: abs(x['score']), reverse=True):
            action_emoji = "🟢 BUY" if plan['action'] == 'BUY' else "🔴 SELL"
            
            print(f"\n{action_emoji}  {plan['asset']}")
            print(f"  Score: {plan['score']:+.2f}")
            print(f"  Confidence: {plan['confidence']}")
            print(f"  Supporting signals ({len(plan['signals'])}):")
            
            for sig in plan['signals'][:3]:  # Top 3 signals
                print(f"    • {sig['type'].replace('_', ' ').title()} ({sig['strength']})")
                print(f"      → {sig['reasoning'][:80]}...")


def main():
    """Point d'entrée principal."""
    
    # Trouver le dataset le plus récent
    datasets_path = Path("datasets/alpha_trading")
    dataset_folders = sorted(datasets_path.glob("dataset_*"), reverse=True)
    
    if not dataset_folders:
        print("No dataset found. Run mass_data_collector_v2.py first.")
        return
    
    latest_dataset = dataset_folders[0]
    
    # Visualiser
    visualizer = SignalVisualizer(latest_dataset)
    visualizer.print_dashboard()
    visualizer.export_trading_plan()


if __name__ == "__main__":
    main()
