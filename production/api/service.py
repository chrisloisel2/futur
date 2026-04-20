from __future__ import annotations

from production.live.runtime import CanonicalLiveRuntime


def load_live_runtime(short_enabled: bool = True) -> CanonicalLiveRuntime:
    return CanonicalLiveRuntime.load_latest(short_enabled=short_enabled)

