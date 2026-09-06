from __future__ import annotations

import pandas as pd

from common.logging.setup import get_logger
from domain.events.cross_venue import CrossVenuePremiumEvent

logger = get_logger(__name__)


class CrossSourceSynchronizer:
    def __init__(self, tolerance_bps: float = 50.0):
        self.tolerance_bps = tolerance_bps

    def synchronize(self, spot_df: pd.DataFrame, futures_df: pd.DataFrame) -> pd.DataFrame:
        if spot_df.empty or futures_df.empty:
            return pd.DataFrame()
        spot = spot_df.copy().set_index("event_time_aligned")
        fut = futures_df.copy().set_index("event_time_aligned")
        joined = spot.join(fut, lsuffix="_spot", rsuffix="_fut", how="inner")
        if joined.empty:
            return pd.DataFrame()
        records = []
        for ts, row in joined.iterrows():
            ref = float(row.get("mid_price_spot", row.get("price_spot", 0)))
            tgt = float(row.get("mid_price_fut", row.get("price_fut", 0)))
            premium_bps = (tgt / ref - 1) * 10_000 if ref else 0.0
            ok = abs(premium_bps) <= self.tolerance_bps
            evt = CrossVenuePremiumEvent(
                event_time=ts,
                recv_time=ts,
                event_time_aligned=ts,
                skew_ms=0,
                symbol=row.get("symbol_spot", ""),
                venue=str(row.get("venue_fut", "")),
                source="cross_venue",
                event_type="cross_venue_premium",
                seq=0,
                ingest_run_id=str(row.get("ingest_run_id_spot", "")),
                payload_version=1,
                is_snapshot=True,
                ref_venue=str(row.get("venue_spot", "")),
                ref_price=ref,
                target_venue=str(row.get("venue_fut", "")),
                target_price=tgt,
                premium_bps=premium_bps,
                basis_bps=premium_bps,
                cross_source_ok=ok,
                cross_source_error_code=0 if ok else 1,
            )
            records.append(evt.dict())
        df = pd.DataFrame(records)
        logger.info({"msg": "cross source sync complete", "rows": len(df)})
        return df
