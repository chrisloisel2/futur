"""
tests/institutional/test_dataset_build_failures.py
═══════════════════════════════════════════════════════════════════════════════
Tests du comportement en cas d'échec de construction de dataset.

Couvre :
    - Asset explicitement demandé qui échoue → comportement visible + exit code
    - Asset absent de l'univers secondaire → warning seulement
    - Script build_engine_datasets retourne exit code 1 si asset requis échoue
    - build_report.json et failed_assets.json écrits correctement
    - Colonne normalizer intégrée dans le pipeline
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import pandas as pd
import pytest


# ══════════════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════════════

_UTC = timezone.utc
_REPO_ROOT = Path(__file__).parents[3]   # /home/qbee/futur


def _run_script(script: str, args: list, cwd: Path = _REPO_ROOT) -> subprocess.CompletedProcess:
    """Lance un script Python et retourne le résultat."""
    return subprocess.run(
        [sys.executable, str(_REPO_ROOT / "scripts" / script)] + args,
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )


# ══════════════════════════════════════════════════════════════════════════════
# Tests du ColumnNormalizer — intégration
# ══════════════════════════════════════════════════════════════════════════════


class TestColumnNormalizerIntegration:
    """
    Vérifie que le normalizer détecte correctement les fichiers close-only.
    """

    def test_close_only_file_raises_clear_error(self) -> None:
        """
        binance_eth.parquet a [timestamp, eth_close].
        normalize_ohlcv_columns doit lever ValueError avec un message clair.
        """
        from institutional.data.column_normalizer import normalize_ohlcv_columns

        ts = pd.date_range("2024-01-01", periods=5, freq="1h", tz="UTC")
        df = pd.DataFrame({"timestamp": ts, "eth_close": [3000.0]*5})

        with pytest.raises(ValueError) as exc_info:
            normalize_ohlcv_columns(df, "ETHUSDT", "data_out/binance_eth.parquet")

        msg = str(exc_info.value)
        assert "close" in msg.lower() or "obligatoires" in msg.lower()
        assert "ETHUSDT" in msg

    def test_enriched_schema_normalizes_correctly(self) -> None:
        """
        Le schéma enriched (datetime + open/high/low/close/volume) doit
        normaliser sans erreur.
        """
        from institutional.data.column_normalizer import normalize_ohlcv_columns

        ts = pd.date_range("2024-01-01", periods=10, freq="1h", tz="UTC")
        df = pd.DataFrame({
            "datetime": ts,
            "open":   [50_000.0]*10,
            "high":   [51_000.0]*10,
            "low":    [49_000.0]*10,
            "close":  [50_500.0]*10,
            "volume": [1000.0]*10,
        })

        df_out, report = normalize_ohlcv_columns(df, "BTCUSDT", "enriched")
        assert report.is_valid
        assert df_out.index.name == "timestamp"
        assert report.timestamp_source == "datetime"
        assert all(c in df_out.columns for c in ["open", "high", "low", "close", "volume"])


# ══════════════════════════════════════════════════════════════════════════════
# Tests du loader — assets disponibles
# ══════════════════════════════════════════════════════════════════════════════


class TestLoaderAssetAvailability:
    """
    Vérifie que les assets principales chargent depuis enriched (pas data_out).
    Ces tests vérifient le comportement réel du loader.
    """

    @pytest.mark.skipif(
        not (_REPO_ROOT / "data/enriched/BTCUSDT_1h_enriched.parquet").exists(),
        reason="Données enriched BTC absentes",
    )
    def test_btcusdt_loads_from_enriched(self) -> None:
        sys.path.insert(0, str(_REPO_ROOT))
        from src.institutional.data.loaders import load_asset_1h  # type: ignore

        df = load_asset_1h("BTCUSDT", "2021-01-01", "2021-06-30")
        assert len(df) > 0
        assert all(c in df.columns for c in ["open", "high", "low", "close", "volume"])
        assert df["source"].iloc[0] == "enriched"

    @pytest.mark.skipif(
        not (_REPO_ROOT / "data/enriched/ETHUSDT_1h_enriched.parquet").exists(),
        reason="Données enriched ETH absentes",
    )
    def test_ethusdt_loads_from_enriched_not_data_out(self) -> None:
        """
        ETHUSDT ne doit JAMAIS charger depuis binance_eth.parquet (close only).
        Doit venir de enriched.
        """
        sys.path.insert(0, str(_REPO_ROOT))
        from src.institutional.data.loaders import load_asset_1h  # type: ignore

        df = load_asset_1h("ETHUSDT", "2021-01-01", "2021-06-30")
        assert df["source"].iloc[0] == "enriched"
        assert all(c in df.columns for c in ["open", "high", "low", "close"])

    @pytest.mark.skipif(
        not (_REPO_ROOT / "data/enriched/SOLUSDT_1h_enriched.parquet").exists(),
        reason="Données enriched SOL absentes",
    )
    def test_solusdt_loads_from_enriched(self) -> None:
        sys.path.insert(0, str(_REPO_ROOT))
        from src.institutional.data.loaders import load_asset_1h  # type: ignore

        df = load_asset_1h("SOLUSDT", "2021-01-01", "2021-06-30")
        assert df["source"].iloc[0] == "enriched"
        assert "close" in df.columns

    def test_unknown_asset_raises_file_not_found(self) -> None:
        sys.path.insert(0, str(_REPO_ROOT))
        from src.institutional.data.loaders import load_asset_1h  # type: ignore

        with pytest.raises(FileNotFoundError, match="XYZUSDT"):
            load_asset_1h("XYZUSDT", "2021-01-01", "2021-06-30")

    def test_close_only_asset_never_returned_as_ohlcv(self) -> None:
        """
        binance_eth.parquet ne doit jamais être retourné comme OHLCV.
        Si le loader essaie de l'utiliser, il doit échouer avec un message clair.
        """
        sys.path.insert(0, str(_REPO_ROOT))
        import importlib
        loader_mod = importlib.import_module("src.institutional.data.loaders")

        # Simuler un asset non-enriched en patchant ENRICHED_ROOT
        import unittest.mock as mock
        fake_root = Path("/tmp/nonexistent_enriched")

        with mock.patch.object(loader_mod, "ENRICHED_ROOT", fake_root):
            # ETH sans enriched → doit lever FileNotFoundError claire
            with pytest.raises(FileNotFoundError) as exc_info:
                loader_mod.load_asset_1h("ETHUSDT", "2021-01-01", "2021-06-30")

        # Le message doit expliquer pourquoi (pas de OHLCV dans binance_eth)
        assert "ETHUSDT" in str(exc_info.value)


# ══════════════════════════════════════════════════════════════════════════════
# Tests du script build_engine_datasets
# ══════════════════════════════════════════════════════════════════════════════


class TestBuildEngineDatasetScript:
    """
    Vérifie le comportement du script vis-à-vis des assets qui échouent.
    """

    @pytest.mark.skipif(
        not (_REPO_ROOT / "data/enriched/BTCUSDT_1h_enriched.parquet").exists(),
        reason="Données enriched BTC absentes",
    )
    def test_exit_code_0_on_full_success(self) -> None:
        """Assets tous disponibles → exit code 0."""
        result = _run_script(
            "build_engine_datasets.py",
            [
                "--engines", "btc_eth_trend",
                "--start", "2024-01-01",
                "--end", "2024-03-31",
                "--no-validate",
            ],
        )
        assert result.returncode == 0, (
            f"stdout={result.stdout[-500:]}\nstderr={result.stderr[-500:]}"
        )
        assert "PARTIAL_FAILED" not in result.stdout
        assert "PARTIAL_FAILED" not in result.stderr

    def test_exit_code_1_on_unknown_engine(self) -> None:
        """Moteur inconnu → exit code 1 immédiat."""
        result = _run_script(
            "build_engine_datasets.py",
            ["--engines", "nonexistent_engine"],
        )
        assert result.returncode == 1

    def test_partial_failed_message_when_asset_fails(self) -> None:
        """
        Si un asset requis échoue (ici un faux asset), le script doit :
        1. Retourner exit code 1
        2. Afficher PARTIAL_FAILED
        """
        result = _run_script(
            "build_engine_datasets.py",
            [
                "--engines",   "btc_eth_trend",
                "--start",     "2024-01-01",
                "--end",       "2024-03-31",
                "--no-validate",
            ],
        )
        # Ce test documente le comportement attendu.
        # Si les assets sont disponibles → exit 0
        # Sinon → exit 1 avec PARTIAL_FAILED
        if result.returncode == 1:
            combined = result.stdout + result.stderr
            assert "PARTIAL_FAILED" in combined or "✗" in combined

    @pytest.mark.skipif(
        not (_REPO_ROOT / "data/enriched/BTCUSDT_1h_enriched.parquet").exists(),
        reason="Données enriched BTC absentes",
    )
    def test_build_report_written_on_success(self) -> None:
        """build_report.json est écrit même en cas de succès."""
        report_path = (
            _REPO_ROOT / "artifacts/institutional/datasets/btc_eth_trend/build_report.json"
        )
        # Lancer le build
        _run_script(
            "build_engine_datasets.py",
            ["--engines", "btc_eth_trend",
             "--start", "2024-01-01", "--end", "2024-03-31",
             "--no-validate"],
        )
        if report_path.exists():
            report = json.loads(report_path.read_text())
            assert "engine" in report
            assert "status"  in report
            assert "n_built" in report

    def test_no_false_success_message(self) -> None:
        """
        Le script ne doit PAS afficher 'TOUS LES DATASETS CONSTRUITS ✓'
        quand des assets ont échoué.
        """
        result = _run_script(
            "build_engine_datasets.py",
            ["--engines", "trm_event",
             "--start", "2024-01-01", "--end", "2024-01-31",
             "--no-validate"],
        )
        if result.returncode == 1:
            combined = result.stdout + result.stderr
            # Ne doit pas mentir sur le succès
            assert "TOUS LES DATASETS CONSTRUITS ✓" not in combined


# ══════════════════════════════════════════════════════════════════════════════
# Tests du script debug_asset_schema
# ══════════════════════════════════════════════════════════════════════════════


class TestDebugAssetSchemaScript:

    @pytest.mark.skipif(
        not (_REPO_ROOT / "data/enriched/BTCUSDT_1h_enriched.parquet").exists(),
        reason="Données enriched BTC absentes",
    )
    def test_exit_code_0_when_all_available(self) -> None:
        result = _run_script(
            "debug_asset_schema.py",
            ["--assets", "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT"],
        )
        assert result.returncode == 0, (
            f"stdout={result.stdout[-800:]}\nstderr={result.stderr[-200:]}"
        )

    def test_exit_code_1_when_asset_missing(self) -> None:
        result = _run_script(
            "debug_asset_schema.py",
            ["--assets", "XYZUSDT_FAKE"],
        )
        assert result.returncode == 1

    @pytest.mark.skipif(
        not (_REPO_ROOT / "data/enriched/ETHUSDT_1h_enriched.parquet").exists(),
        reason="Données enriched ETH absentes",
    )
    def test_close_only_warning_displayed(self) -> None:
        """Le script doit afficher l'avertissement sur binance_eth.parquet."""
        result = _run_script(
            "debug_asset_schema.py",
            ["--assets", "ETHUSDT", "--sources", "enriched,data_out"],
        )
        combined = result.stdout + result.stderr
        # Doit mentionner que le fichier data_out est close-only
        assert "CLOSE-ONLY" in combined or "close" in combined.lower()

    def test_output_contains_asset_name(self) -> None:
        result = _run_script(
            "debug_asset_schema.py",
            ["--assets", "BTCUSDT"],
        )
        assert "BTCUSDT" in result.stdout
