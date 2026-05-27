import pandas as pd

from pipeline.data.event_time import EventTimeAligner


def test_event_time_alignment_and_watermark():
    df = pd.DataFrame(
        {
            "event_time": pd.to_datetime(["2024-01-01T00:00:00Z", "2024-01-01T00:00:01Z"]),
            "recv_time": pd.to_datetime(["2024-01-01T00:00:00.010Z", "2024-01-01T00:00:01.020Z"]),
        }
    )
    aligner = EventTimeAligner(watermark_ms=5)
    aligned = aligner.align(df)
    assert "event_time_aligned" in aligned
    watermarked = aligner.watermark(aligned)
    assert "is_late" in watermarked
