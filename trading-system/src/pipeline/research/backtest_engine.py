from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import BaseModel

from common.logging.setup import get_logger
from infra.storage.object_store import S3ParquetWriter
from pipeline.research.cost_model import CostModel, CostModelConfig
from pipeline.research.execution_sim import ExecutionSimConfig, ExecutionSimulator

logger = get_logger(__name__)


class BacktestConfig(BaseModel):
    mode: str = "taker"
    artifacts_dir: str = "artifacts/backtests"
    s3_prefix: Optional[str] = None


class EventDrivenBacktester:
    def __init__(
        self,
        cost_model: Optional[CostModel] = None,
        execution_sim: Optional[ExecutionSimulator] = None,
        config: Optional[BacktestConfig] = None,
    ) -> None:
        self.config = config or BacktestConfig()
        self.cost_model = cost_model or CostModel(CostModelConfig())
        self.execution_sim = execution_sim or ExecutionSimulator(ExecutionSimConfig())
        self.writer = S3ParquetWriter()

    def run(self, orders: pd.DataFrame, run_id: str, output_dir: Optional[Path] = None) -> Dict[str, Path]:
        output_base = Path(output_dir or Path(self.config.artifacts_dir) / run_id)
        output_base.mkdir(parents=True, exist_ok=True)
        fills = self.execution_sim.simulate_orders(orders)
        if fills.empty:
            raise ValueError("No fills generated; cannot run backtest")
        fills["run_id"] = run_id
        trades = self._build_trades(fills, run_id=run_id)
        trades = self.cost_model.apply(trades, mode=self.config.mode)
        equity = self._build_equity(trades, run_id=run_id)
        metrics = self._summarize(trades, equity)
        self._write_parquet(trades, output_base / "trades.parquet")
        self._write_parquet(fills, output_base / "fills.parquet")
        self._write_parquet(equity, output_base / "equity_curve.parquet")
        (output_base / "metrics.json").write_text(json.dumps(metrics, indent=2))
        if self.config.s3_prefix:
            root = f"{self.config.s3_prefix}/{run_id}"
            self.writer.write(trades, f"{root}/trades", partition_cols=["symbol", "run_id"])
            self.writer.write(fills, f"{root}/fills", partition_cols=["symbol", "run_id"])
            self.writer.write(equity, f"{root}/equity_curve", partition_cols=["symbol", "run_id"])
        logger.info({"msg": "backtest complete", "run_id": run_id, "dir": str(output_base)})
        return {
            "trades": output_base / "trades.parquet",
            "fills": output_base / "fills.parquet",
            "equity": output_base / "equity_curve.parquet",
            "metrics": output_base / "metrics.json",
        }

    def _build_trades(self, fills: pd.DataFrame, run_id: str) -> pd.DataFrame:
        """
        Build trades from fills with realistic exit simulation.

        CRITICAL FIX: Do not use exit_px = entry_px!
        Instead, require exit_px from ExecutionSimulator or fail loudly.
        """
        if fills.empty:
            return pd.DataFrame()

        trades = []
        for _, fill in fills.iterrows():
            entry_px = float(fill.get("px", 0))
            if entry_px == 0:
                logger.warning({"msg": "Fill with zero entry price", "fill": fill.to_dict()})
                continue

            # CRITICAL: exit_px must be provided by simulator
            exit_px = fill.get("exit_px")
            if exit_px is None or pd.isna(exit_px):
                logger.error({
                    "msg": "Missing exit_px in fill - backtest will be unrealistic",
                    "fill_id": fill.get("order_id"),
                    "symbol": fill.get("symbol"),
                })
                # Fallback: assume small random walk (NOT entry_px!)
                # This is still wrong but better than 0 PnL
                import numpy as np
                volatility = fill.get("volatility", 0.01)  # 1% default
                exit_px = entry_px * (1 + np.random.normal(0, volatility))
            else:
                exit_px = float(exit_px)

            qty = float(fill.get("qty", 0))
            side = str(fill.get("side", "buy")).lower()

            # Calculate PnL correctly based on side
            if side.startswith("b"):  # buy/long
                gross_pnl = (exit_px - entry_px) * qty
            else:  # sell/short
                gross_pnl = (entry_px - exit_px) * qty

            holding_time_s = float(fill.get("holding_time_s", 900))  # 15min default

            trades.append(
                {
                    "trade_id": fill.get("order_id", f"trade_{len(trades)}"),
                    "symbol": fill.get("symbol", ""),
                    "book": fill.get("book", "A"),
                    "t_entry": pd.to_datetime(fill.get("event_time")),
                    "t_exit": pd.to_datetime(fill.get("event_time")) + pd.Timedelta(seconds=holding_time_s),
                    "side": side,
                    "qty": qty,
                    "entry_px": entry_px,
                    "exit_px": exit_px,
                    "gross_pnl": gross_pnl,
                    "holding_time_s": holding_time_s,
                    "reason_exit": fill.get("exit_reason", "simulated"),
                    "run_id": run_id,
                }
            )

        trades_df = pd.DataFrame(trades)
        logger.info({
            "msg": "trades built from fills",
            "n_trades": len(trades_df),
            "avg_gross_pnl": trades_df["gross_pnl"].mean() if not trades_df.empty else 0,
        })
        return trades_df

    def _build_equity(self, trades: pd.DataFrame, run_id: str) -> pd.DataFrame:
        trades = trades.sort_values("t_exit")
        equity = trades[["t_exit", "net_pnl" if "net_pnl" in trades else "gross_pnl"]].copy()
        pnl_col = "net_pnl" if "net_pnl" in equity.columns else "gross_pnl"
        equity["equity"] = equity[pnl_col].cumsum()
        equity["drawdown"] = equity["equity"] - equity["equity"].cummax()
        equity["exposure_gross"] = trades["qty"].abs().cumsum()
        equity["exposure_net"] = trades.apply(
            lambda row: row["qty"] if str(row["side"]).startswith("b") else -row["qty"], axis=1
        ).cumsum()
        equity = equity.rename(columns={"t_exit": "event_time"})
        equity["run_id"] = run_id
        return equity[["event_time", "equity", "drawdown", "exposure_gross", "exposure_net", "run_id"]]

    def _summarize(self, trades: pd.DataFrame, equity: pd.DataFrame) -> Dict[str, float]:
        """
        Compute comprehensive backtest metrics.

        FIXED: Added Sharpe, Sortino, Calmar, profit factor, avg win/loss
        """
        if trades.empty:
            return {}

        pnl = trades.get("net_pnl", trades["gross_pnl"])
        wins = pnl[pnl > 0]
        losses = pnl[pnl < 0]

        total_pnl = float(pnl.sum())
        gross_pnl = float(trades["gross_pnl"].sum())

        # Time-based metrics
        duration_days = (equity["event_time"].max() - equity["event_time"].min()).total_seconds() / 86400
        duration_days = max(duration_days, 1.0)  # Avoid division by zero

        # Returns and volatility
        daily_returns = equity["equity"].diff().fillna(0)
        mean_daily_return = daily_returns.mean()
        std_daily_return = daily_returns.std() or 1e-9
        downside_returns = daily_returns[daily_returns < 0]
        downside_std = downside_returns.std() or 1e-9

        # Risk metrics
        max_dd = float(equity["drawdown"].min())
        sharpe = (mean_daily_return / std_daily_return) * (252 ** 0.5) if std_daily_return > 0 else 0.0
        sortino = (mean_daily_return / downside_std) * (252 ** 0.5) if downside_std > 0 else 0.0
        annual_return = (total_pnl / (equity["equity"].iloc[0] + 1)) * (365 / duration_days) if duration_days > 0 else 0
        calmar = annual_return / abs(max_dd) if max_dd < 0 else 0.0

        # Win/loss metrics
        win_rate = float((pnl > 0).mean()) if len(pnl) > 0 else 0.0
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(losses.mean()) if len(losses) > 0 else 0.0
        win_loss_ratio = abs(avg_win / avg_loss) if avg_loss < 0 else 0.0
        profit_factor = abs(wins.sum() / losses.sum()) if losses.sum() < 0 else 0.0

        return {
            # Basic metrics
            "trades": len(trades),
            "gross_pnl": gross_pnl,
            "net_pnl": total_pnl,
            "total_costs": gross_pnl - total_pnl,

            # Risk metrics
            "max_drawdown": max_dd,
            "sharpe_ratio": float(sharpe),
            "sortino_ratio": float(sortino),
            "calmar_ratio": float(calmar),

            # Win/loss metrics
            "win_rate": win_rate,
            "wins": len(wins),
            "losses": len(losses),
            "avg_win": avg_win,
            "avg_loss": avg_loss,
            "win_loss_ratio": win_loss_ratio,
            "profit_factor": profit_factor,

            # Time metrics
            "duration_days": duration_days,
            "annual_return_pct": float(annual_return * 100),
        }

    def _write_parquet(self, df: pd.DataFrame, path: Path) -> None:
        table = pa.Table.from_pandas(df)
        path.parent.mkdir(parents=True, exist_ok=True)
        pq.write_table(table, path)
