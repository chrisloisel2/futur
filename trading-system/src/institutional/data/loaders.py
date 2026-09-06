"""
trading-system/src/institutional/data/loaders.py
═══════════════════════════════════════════════════════════════════════════════
Loaders de données OHLCV pour l'INSTITUTIONAL_ENGINE.

Chaque loader :
    1. Charge le fichier (parquet ou CSV)
    2. Normalise le timestamp → DatetimeIndex UTC strict
    3. Trie chronologiquement
    4. Gère les duplicats selon config (drop | raise)
    5. Retourne (DataFrame, DataQualityReport)

INTERDIT :
    - forward-fill sans limite explicite (ffill_limit=None → jamais de ffill)
    - inférer des données manquantes sans signalement
    - retourner un DataFrame sans rapport qualité associé

GARANTI :
    - l'appelant sait toujours exactement ce qu'il a reçu
    - un rapport invalide bloque le pipeline via PipelineBlockedError
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd

from institutional.data.checker import CheckerConfig, DataQualityChecker, PipelineBlockedError
from institutional.data.schemas import DataQualityReport

# ══════════════════════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class LoadConfig:
    """
    Configuration d'un loader de données.

    on_duplicate : comportement face aux timestamps dupliqués
        "keep_first" → garder le premier  (silencieux)
        "keep_last"  → garder le dernier  (silencieux)
        "raise"      → lever ValueError   (strict)
        "report"     → signaler sans drop (souple)

    ffill_limit : nombre max de barres à forward-fill (None = jamais)
    """

    timestamp_col:    str   = "timestamp"  # ou "datetime", "date"
    on_duplicate:     str   = "raise"      # "keep_first"|"keep_last"|"raise"|"report"
    ffill_limit:      int | None = None    # None = interdiction de ffill
    sort_ascending:   bool  = True
    raise_on_invalid: bool  = True         # PipelineBlockedError si rapport invalide

    def __post_init__(self) -> None:
        valid_dup = {"keep_first", "keep_last", "raise", "report"}
        if self.on_duplicate not in valid_dup:
            raise ValueError(
                f"on_duplicate={self.on_duplicate!r} invalide — "
                f"valeurs acceptées : {sorted(valid_dup)}"
            )
        if self.ffill_limit is not None and self.ffill_limit < 1:
            raise ValueError("ffill_limit doit être ≥ 1 ou None")


# ══════════════════════════════════════════════════════════════════════════════
# Normalisation (fonctions internes)
# ══════════════════════════════════════════════════════════════════════════════


def _normalize_timestamp_col(df: pd.DataFrame, ts_col: str) -> pd.DataFrame:
    """
    Transforme une colonne timestamp en DatetimeIndex UTC.
    Lève ValueError si la conversion échoue.
    """
    df = df.copy()

    if ts_col not in df.columns:
        # Peut-être que le timestamp est déjà l'index
        if isinstance(df.index, pd.DatetimeIndex):
            return _ensure_utc_index(df)
        raise ValueError(
            f"Colonne timestamp {ts_col!r} absente et index non-DatetimeIndex. "
            f"Colonnes disponibles : {list(df.columns)}"
        )

    df[ts_col] = pd.to_datetime(df[ts_col], utc=True, errors="raise")
    df = df.set_index(ts_col)
    return df


def _ensure_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    """Garantit que le DatetimeIndex est UTC. Convertit si nécessaire."""
    if df.index.tz is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")
    elif str(df.index.tz) not in {"UTC", "utc", "UTC+00:00"}:
        df = df.copy()
        df.index = df.index.tz_convert("UTC")
    return df


def _handle_duplicates(
    df: pd.DataFrame,
    on_duplicate: str,
) -> pd.DataFrame:
    """Gère les timestamps dupliqués selon la politique configurée."""
    dup_mask = df.index.duplicated(keep="first")
    n_dup = int(dup_mask.sum())

    if n_dup == 0:
        return df

    if on_duplicate == "raise":
        examples = df.index[dup_mask][:3].tolist()
        raise ValueError(
            f"{n_dup} timestamp(s) dupliqué(s) — "
            f"exemples : {[str(e) for e in examples]}. "
            f"Utiliser on_duplicate='keep_first' pour les ignorer."
        )
    elif on_duplicate == "keep_first":
        return df[~df.index.duplicated(keep="first")]
    elif on_duplicate == "keep_last":
        return df[~df.index.duplicated(keep="last")]
    # "report" : laisser passer — le checker les signalera
    return df


def _apply_ffill(df: pd.DataFrame, limit: int | None) -> pd.DataFrame:
    """
    Applique un forward-fill avec limite explicite.
    Si limit=None, INTERDIT de ffill (retourne df inchangé).
    """
    if limit is None:
        return df
    return df.ffill(limit=limit)


# ══════════════════════════════════════════════════════════════════════════════
# DataLoader
# ══════════════════════════════════════════════════════════════════════════════


class DataLoader:
    """
    Charge des données OHLCV depuis parquet ou CSV.

    Retourne toujours (DataFrame, DataQualityReport).
    Si raise_on_invalid=True (défaut), lève PipelineBlockedError sur échec.

    Usage :
        loader = DataLoader(
            load_config=LoadConfig(on_duplicate="keep_first"),
            check_config=CheckerConfig(expected_freq="1h"),
        )
        df, report = loader.load_parquet(
            path="data/btcusdt_1h.parquet",
            asset="BTCUSDT",
            source="futures",
        )
    """

    def __init__(
        self,
        load_config:  LoadConfig | None = None,
        check_config: CheckerConfig | None = None,
    ) -> None:
        self.load_config  = load_config  or LoadConfig()
        self.check_config = check_config or CheckerConfig()
        self._checker     = DataQualityChecker(self.check_config)

    # ── Parquet ───────────────────────────────────────────────────────────────

    def load_parquet(
        self,
        path: str | Path,
        asset: str,
        source: str,
        columns: Sequence[str] | None = None,
    ) -> tuple[pd.DataFrame, DataQualityReport]:
        """
        Charge un fichier parquet, normalise, vérifie.

        Parameters
        ----------
        path    : chemin vers le fichier .parquet
        asset   : nom de l'actif ("BTCUSDT", …)
        source  : source des données ("futures", "spot", "enriched")
        columns : colonnes à charger (None = toutes)
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Fichier parquet introuvable : {p}")
        if p.suffix not in (".parquet", ".pq"):
            raise ValueError(
                f"Extension {p.suffix!r} inattendue — utiliser load_csv() pour CSV"
            )

        df = pd.read_parquet(p, columns=list(columns) if columns else None)
        return self._normalize_and_check(df, asset, source)

    # ── CSV ───────────────────────────────────────────────────────────────────

    def load_csv(
        self,
        path: str | Path,
        asset: str,
        source: str,
        sep: str = ",",
        columns: Sequence[str] | None = None,
        **read_csv_kwargs: object,
    ) -> tuple[pd.DataFrame, DataQualityReport]:
        """
        Charge un fichier CSV, normalise, vérifie.

        Accepte tout séparateur via `sep`. Les kwargs supplémentaires
        sont passés à pd.read_csv() directement.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Fichier CSV introuvable : {p}")

        df = pd.read_csv(p, sep=sep, **read_csv_kwargs)
        if columns:
            missing = [c for c in columns if c not in df.columns]
            if missing:
                raise ValueError(f"Colonnes demandées absentes : {missing}")
            df = df[list(columns)]

        return self._normalize_and_check(df, asset, source)

    # ── DataFrame brut ────────────────────────────────────────────────────────

    def from_dataframe(
        self,
        df: pd.DataFrame,
        asset: str,
        source: str,
    ) -> tuple[pd.DataFrame, DataQualityReport]:
        """
        Normalise et vérifie un DataFrame déjà chargé.
        Utile pour les tests ou les sources en mémoire.
        """
        return self._normalize_and_check(df, asset, source)

    # ── Pipeline interne ──────────────────────────────────────────────────────

    def _normalize_and_check(
        self,
        df: pd.DataFrame,
        asset: str,
        source: str,
    ) -> tuple[pd.DataFrame, DataQualityReport]:
        """
        Pipeline complet :
            1. Normaliser timestamp → DatetimeIndex UTC
            2. Gérer les duplicats
            3. Trier
            4. FFill (si configuré)
            5. Checker qualité
            6. Bloquer si invalide (si raise_on_invalid)
        """
        cfg = self.load_config

        # 1. Normaliser le timestamp
        df = _normalize_timestamp_col(df, cfg.timestamp_col)
        df = _ensure_utc_index(df)

        # 2. Duplicats
        df = _handle_duplicates(df, cfg.on_duplicate)

        # 3. Tri strict
        if cfg.sort_ascending:
            df = df.sort_index(ascending=True)

        # 4. FFill optionnel (avec limite explicite obligatoire)
        if cfg.ffill_limit is not None:
            df = _apply_ffill(df, cfg.ffill_limit)

        # 5. Qualité
        report = self._checker.check(df, asset=asset, source=source)

        # 6. Gate
        if cfg.raise_on_invalid and not report.is_valid():
            raise PipelineBlockedError(report)

        return df, report
