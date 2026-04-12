"""
Complete Pipeline Integration Example.

Demonstrates the corrected architecture:
    Market Data → Regime (binary) → Impulse (event) → Edge → MetaControl → Execution

CRITICAL CHANGES:
- Regime is now BINARY: {calm, reversal} (impulse removed)
- Impulse is now an EVENT detector (not a regime)
- Impulse affects MetaControl (downscale) and Execution (MAKER→TAKER)
"""

import numpy as np
import pandas as pd
from typing import Dict, Any

# Import our corrected modules
from regime_classifier_v2 import (
    train_calibrated_regime_classifier,
    evaluate_regime_classifier,
    production_gates,
)
from impulse_detector import ImpulseDetector
from impulse_gates import ImpulseGates, validate_impulse_production
from meta_control import MetaControl, MetaControlConfig
from execution_engine import ExecutionEngine, ExecutionConfig


class TradingPipeline:
    """
    Complete trading pipeline with corrected architecture.

    Flow:
    1. Market data preprocessing
    2. Regime classification (binary)
    3. Impulse detection (event)
    4. Edge estimation (regime-conditional)
    5. Meta-control (position sizing with impulse downscale)
    6. Execution (impulse-aware routing)
    """

    def __init__(
        self,
        regime_model,  # Trained regime classifier
        impulse_detector: ImpulseDetector,
        meta_control: MetaControl,
        execution_engine: ExecutionEngine,
    ):
        self.regime_model = regime_model
        self.impulse_detector = impulse_detector
        self.meta_control = meta_control
        self.execution_engine = execution_engine

    def process_tick(
        self,
        timestamp: pd.Timestamp,
        market_data: Dict[str, Any],
        base_signal_size: float,
    ) -> Dict[str, Any]:
        """
        Process single tick through full pipeline.

        Args:
            timestamp: Current timestamp
            market_data: Dict with {close, volume, rv_60, volume_ma, volume_std, ...}
            base_signal_size: Base position size from edge/alpha model

        Returns:
            Dict with pipeline outputs
        """
        # Step 1: Regime classification (binary)
        regime_features = self._extract_regime_features(market_data)
        regime_proba = self.regime_model.predict_proba([regime_features])[0]
        regime_label = self.regime_model.predict([regime_features])[0]
        regime = 'calm' if regime_label == 0 else 'reversal'

        # Step 2: Impulse detection (event)
        is_impulse, impulse_score = self.impulse_detector.detect(
            timestamp=timestamp,
            ret_1m=market_data['ret_1m'],
            rv_60=market_data['rv_60'],
            volume=market_data['volume'],
            volume_ma=market_data['volume_ma'],
            volume_std=market_data['volume_std'],
            spread_z=market_data.get('spread_z', 0.0),
            regime=regime,
        )

        # Step 3: Edge estimation (simplified here)
        # In production: regime-conditional alpha model
        edge_multiplier = 1.0  # Placeholder

        # Step 4: Meta-control (position sizing)
        meta_output = self.meta_control.compute_position_size(
            timestamp=timestamp,
            base_size=base_signal_size * edge_multiplier,
            regime=regime,
            impulse_score=impulse_score,
            is_impulse=is_impulse,
            recent_pnl=market_data.get('recent_pnl', 0.0),
        )

        # Step 5: Execution (impulse-aware routing)
        if meta_output.position_size > 0:
            order = self.execution_engine.place_order(
                symbol='BTCUSDT',
                side='BUY',  # Simplified: assume always BUY
                size=meta_output.position_size,
                regime=meta_output.regime,
                impulse_active=meta_output.impulse_active,
                impulse_score=meta_output.impulse_score,
                mid_price=market_data['close'],
            )

            if order is not None:
                execution_result = self.execution_engine.submit_order(order)
            else:
                execution_result = None
        else:
            order = None
            execution_result = None

        # Return full pipeline state
        return {
            'timestamp': timestamp,
            'regime': regime,
            'regime_proba': {
                'calm': regime_proba[0],
                'reversal': regime_proba[1],
            },
            'impulse_active': is_impulse,
            'impulse_score': impulse_score,
            'meta_control': {
                'position_size': meta_output.position_size,
                'leverage': meta_output.leverage,
                'multipliers': meta_output.multipliers,
                'in_cooldown': meta_output.in_cooldown,
            },
            'order': order,
            'execution_result': execution_result,
        }

    def _extract_regime_features(self, market_data: Dict[str, Any]) -> np.ndarray:
        """
        Extract features for regime classifier.
        This should match the features used during training.
        """
        # Must match the number of features used during model training
        features = np.array([
            market_data.get('ret_5m', 0.0),
            market_data.get('ret_60m', 0.0),
            market_data.get('rv_60', 0.0),
            market_data.get('volume_ratio', 1.0),
            market_data.get('spread_z', 0.0),
        ])
        return features


def example_backtest_simulation():
    """
    Example: Run pipeline on mock data to demonstrate integration.
    """
    print("=" * 80)
    print("TRADING PIPELINE INTEGRATION EXAMPLE")
    print("=" * 80)
    print()

    # 1. Setup components
    print("Setting up pipeline components...")

    # Mock regime classifier (in production: load trained model)
    from sklearn.linear_model import LogisticRegression
    regime_model = LogisticRegression()
    # Train on dummy data (in production: load from disk)
    X_dummy = np.random.randn(100, 5)
    y_dummy = np.random.randint(0, 2, 100)  # Binary: 0=calm, 1=reversal
    regime_model.fit(X_dummy, y_dummy)

    # Impulse detector
    impulse_detector = ImpulseDetector(threshold=0.7)

    # Meta-control
    meta_control = MetaControl(config=MetaControlConfig(
        regime_mult_calm=1.0,
        regime_mult_reversal=0.7,
        impulse_hard_mult=0.3,
    ))

    # Execution engine
    execution_engine = ExecutionEngine()

    # Pipeline
    pipeline = TradingPipeline(
        regime_model=regime_model,
        impulse_detector=impulse_detector,
        meta_control=meta_control,
        execution_engine=execution_engine,
    )

    print("Pipeline ready.")
    print()

    # 2. Simulate ticks
    print("Simulating market ticks...")
    print()

    timestamps = pd.date_range('2024-01-01', periods=10, freq='1min')

    for i, ts in enumerate(timestamps):
        # Mock market data
        market_data = {
            'close': 50000 + np.random.randn() * 100,
            'volume': 1000 + np.random.randn() * 100,
            'ret_1m': np.random.randn() * 0.001,
            'ret_5m': np.random.randn() * 0.002,
            'ret_60m': np.random.randn() * 0.005,
            'rv_60': 0.01 + np.random.randn() * 0.002,
            'volume_ma': 1000,
            'volume_std': 100,
            'spread_z': np.random.randn() * 0.5,
            'recent_pnl': np.random.randn() * 0.001,
            'volume_ratio': 1.0 + np.random.randn() * 0.2,
        }

        # Inject impulse event at tick 5
        if i == 5:
            market_data['ret_1m'] = 0.015  # Large return
            market_data['rv_60'] = 0.005   # Low RV → high z-score
            market_data['volume'] = 2000   # Volume spike
            print(">>> IMPULSE EVENT INJECTED <<<")
            print()

        # Process tick
        output = pipeline.process_tick(
            timestamp=ts,
            market_data=market_data,
            base_signal_size=1.0,
        )

        # Display results
        print(f"[{ts}]")
        print(f"  Regime: {output['regime']} (calm={output['regime_proba']['calm']:.2f}, reversal={output['regime_proba']['reversal']:.2f})")
        print(f"  Impulse: {'ACTIVE' if output['impulse_active'] else 'inactive'} (score={output['impulse_score']:.3f})")
        print(f"  Position size: {output['meta_control']['position_size']:.3f} (multipliers={output['meta_control']['multipliers']})")

        if output['order'] is not None:
            print(f"  Order: {output['order'].order_type.value} {output['order'].side.value} {output['order'].size:.3f}")
            if output['execution_result'] is not None:
                print(f"  Execution: cost={output['execution_result'].execution_cost_bps:.1f}bps")
        else:
            print(f"  Order: SKIPPED (size below min)")

        print()

    # 3. Display impulse event metrics
    print("=" * 80)
    print("IMPULSE EVENT METRICS")
    print("=" * 80)
    impulse_metrics = impulse_detector.get_event_metrics(total_days=10 / 1440)  # 10 minutes
    for k, v in impulse_metrics.items():
        print(f"  {k}: {v}")
    print()

    # 4. Validate impulse production gates
    print("=" * 80)
    print("IMPULSE PRODUCTION VALIDATION")
    print("=" * 80)
    gates = ImpulseGates(
        min_freq_per_day=0.1,  # Relaxed for demo
        max_freq_per_day=50.0,
    )
    passed, failures = gates.check_all(impulse_metrics, normal_metrics={})
    print(f"  Gates passed: {passed}")
    if failures:
        print("  Failures:")
        for f in failures:
            print(f"    - {f}")
    else:
        print("  All gates passed!")
    print()

    print("=" * 80)
    print("EXAMPLE COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    example_backtest_simulation()
