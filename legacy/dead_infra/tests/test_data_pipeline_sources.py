from pathlib import Path

from data_pipeline.sources import load_source_registry


def test_source_registry_contains_public_sources():
    registry = load_source_registry(Path("config/data_sources.yml"))

    assert "binance_vision_spot_klines" in registry
    assert "alternative_me_fear_greed" in registry
    assert "gdelt_crypto_articles" in registry

    for spec in registry.values():
        assert spec.endpoint.startswith("https://")
        assert spec.cadence
        assert spec.license
        assert spec.schema

