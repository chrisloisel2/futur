import pandas as pd

from pipeline.books.allocator import MultiBookAllocator, AllocatorConfig
from domain.state.targets import TargetPosition


def test_allocator_cluster_caps():
    allocator = MultiBookAllocator(AllocatorConfig(max_cluster_exposure_usd=100))
    t = TargetPosition(event_time=pd.Timestamp("2024-01-01"), book="book_a", symbol="BTC", instrument_type="perp", side="LONG", notional_usd=200.0, leverage=1.0, entry_style="taker")
    tp, dec, _ = allocator.merge_and_cap([t], [], [], {}, {"BTC": "default"}, {}, {}, "run", "v1", "v1")
    assert tp.targets == []
