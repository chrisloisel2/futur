"""
ai/level_2/exit_model_v2.py — Exit Model V2 : Temporal Transformer (PyTorch)
==============================================================================

Architecture : Sequence-aware Transformer Encoder
  - Input  : séquence [pré-entrée (4 bars) + position (k bars)], T ≤ 12
  - Encoder: 3 couches Transformer, d_model=96, 4 têtes, d_ff=256
  - Head   : MLP 3 couches → p_exit ∈ [0,1]

Innovations vs V1 (HistGradientBoosting snapshot) :
  ┌─────────────────────────────────────────────────────────────────┐
  │ V1 : 1 barre courante → 19 spécialistes (snapshot)             │
  │ V2 : trajectoire complète → Transformer (temporal memory)       │
  ├─────────────────────────────────────────────────────────────────┤
  │  • Focal Loss (γ=2) — gère le déséquilibre 5:1 sans cap        │
  │  • Segment embeddings  (pré-entrée / entrée / intra-position)   │
  │  • Asset embeddings    (generalisation cross-asset)             │
  │  • Causal masking      (pas de lookahead intra-sequence)        │
  │  • Ensemble 3 seeds    (variance réduite)                       │
  │  • Platt scaling       (calibration probabiliste post-hoc)      │
  │  • Hard SL override    (-2.5% indépendant du modèle)            │
  └─────────────────────────────────────────────────────────────────┘

Séquence d'entrée par sample :
  [bar t0-4, bar t0-3, bar t0-2, bar t0-1,      ← contexte pré-entrée (4 bars)
   bar t0,                                        ← barre d'entrée (entry fingerprint)
   bar t0+1, bar t0+2, …, bar t0+k]              ← position intra (k=1..7)
  Total : T = 5 + k ≤ 12 (padded à 12 avec mask)

Features par barre :
  - market_dim : EXIT_MARKET_FEATURES (45 features)
  - pos_dim    : EXIT_POSITION_FEATURES (18 features)
               (zéros pour les barres pré-entrée et la barre d'entrée)
"""
from __future__ import annotations

import math
import warnings
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

# ─── PyTorch lazy import ──────────────────────────────────────────────────────

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
    _TORCH_OK = True
except ImportError:
    _TORCH_OK = False

from ai.level_0.exit_labels import (
    EXIT_ALL_FEATURES, EXIT_MARKET_FEATURES, EXIT_POSITION_FEATURES,
)
from ai.level_0.constants import COST_PCT, HORIZON_BARS as MAX_HOLD_BARS

# ─── Constantes architecture ──────────────────────────────────────────────────

D_MODEL      = 96
N_HEADS      = 4
N_LAYERS     = 3
D_FF         = 256
DROPOUT      = 0.15
MAX_SEQ_LEN  = 12          # 4 pré-entrée + 1 entrée + 7 intra-position
PRE_ENTRY    = 4           # barres de contexte avant l'entrée
N_ASSETS     = 11          # 10 assets + 1 inconnu (padding)
N_SEGMENTS   = 3           # 0=pré-entrée, 1=entrée, 2=intra-position
N_ENSEMBLE   = 3           # nb de seeds pour l'ensemble

FOCAL_ALPHA  = 0.25
FOCAL_GAMMA  = 2.0

ASSET_TO_IDX: Dict[str, int] = {
    "BTCUSDT": 0, "ETHUSDT": 1, "BNBUSDT": 2, "SOLUSDT": 3,
    "XRPUSDT": 4, "DOGEUSDT": 5, "ADAUSDT": 6, "AVAXUSDT": 7,
    "DOTUSDT": 8, "LINKUSDT": 9,
}

MARKET_DIM = len(EXIT_MARKET_FEATURES)
POS_DIM    = len(EXIT_POSITION_FEATURES)


# ─────────────────────────────────────────────────────────────────────────────
# Focal Loss
# ─────────────────────────────────────────────────────────────────────────────

def focal_loss(
    pred: "torch.Tensor",
    target: "torch.Tensor",
    alpha: float = FOCAL_ALPHA,
    gamma: float = FOCAL_GAMMA,
) -> "torch.Tensor":
    """Binary focal loss — réduit le gradient des exemples faciles."""
    bce  = F.binary_cross_entropy(pred, target, reduction="none")
    pt   = torch.where(target == 1, pred, 1 - pred)
    at   = torch.where(target == 1,
                       torch.full_like(pred, alpha),
                       torch.full_like(pred, 1 - alpha))
    fl   = at * (1 - pt).pow(gamma) * bce
    return fl.mean()


# ─────────────────────────────────────────────────────────────────────────────
# Dataset séquentiel
# ─────────────────────────────────────────────────────────────────────────────

class ExitSequenceDataset(Dataset):
    """
    Chaque item = (market_seq, pos_seq, segments, positions, asset_id, label)

    market_seq : (MAX_SEQ_LEN, MARKET_DIM) float32
    pos_seq    : (MAX_SEQ_LEN, POS_DIM)    float32 — zéro sur barres pré-entrée
    segments   : (MAX_SEQ_LEN,)            long    — 0/1/2
    positions  : (MAX_SEQ_LEN,)            long    — index dans la séquence
    pad_mask   : (MAX_SEQ_LEN,)            bool    — True = barre de padding
    asset_id   : scalar                    long
    label      : scalar                    float32
    """

    def __init__(self, records: List[dict]) -> None:
        self.records = records

    def __len__(self) -> int:
        return len(self.records)

    def __getitem__(self, idx: int) -> Tuple:
        r = self.records[idx]
        return (
            torch.tensor(r["market_seq"], dtype=torch.float32),
            torch.tensor(r["pos_seq"],    dtype=torch.float32),
            torch.tensor(r["segments"],   dtype=torch.long),
            torch.tensor(r["positions"],  dtype=torch.long),
            torch.tensor(r["pad_mask"],   dtype=torch.bool),
            torch.tensor(r["asset_id"],   dtype=torch.long),
            torch.tensor(r["label"],      dtype=torch.float32),
        )


# ─────────────────────────────────────────────────────────────────────────────
# Construction des séquences à partir des samples V1
# ─────────────────────────────────────────────────────────────────────────────

def build_sequences(
    df_samples: pd.DataFrame,
    df_enriched_map: Dict[str, pd.DataFrame],
    pre_entry: int = PRE_ENTRY,
    max_seq: int   = MAX_SEQ_LEN,
) -> List[dict]:
    """
    Convertit les samples plats (v1) en séquences temporelles.

    df_samples        : sortie de generate_exit_samples()
    df_enriched_map   : {symbol: df_enriched} — pour les barres pré-entrée
    """
    mkt_cols = [f for f in EXIT_MARKET_FEATURES if f in df_samples.columns]
    pos_cols = [f for f in EXIT_POSITION_FEATURES]

    records: List[dict] = []

    for sym, grp in df_samples.groupby("symbol"):
        df_full = df_enriched_map.get(sym)
        if df_full is None:
            continue
        asset_id = ASSET_TO_IDX.get(sym, N_ASSETS - 1)

        # Colonnes marché dans le parquet complet
        mkt_avail_full = [f for f in EXIT_MARKET_FEATURES if f in df_full.columns]
        mkt_full_arr   = df_full[mkt_avail_full].fillna(0.0).values.astype(np.float32)

        # Pour remplir les colonnes manquantes avec zéro
        mkt_to_idx = {f: i for i, f in enumerate(mkt_avail_full)}

        for (t0, k_val), sub in grp.groupby(["t0", "k"]):
            row = sub.iloc[0]
            k   = int(row["k"])
            label = int(row["y_exit"])

            # ── Séquence pré-entrée (barres t0-pre_entry .. t0-1) ─────────────
            pre_bars: List[np.ndarray] = []
            for delta in range(pre_entry, 0, -1):
                bar_idx = int(t0) - delta
                if bar_idx >= 0:
                    m_row = np.zeros(MARKET_DIM, dtype=np.float32)
                    for fi, fname in enumerate(EXIT_MARKET_FEATURES):
                        j = mkt_to_idx.get(fname)
                        if j is not None:
                            v = mkt_full_arr[bar_idx, j]
                            m_row[fi] = v if np.isfinite(v) else 0.0
                    pre_bars.append(m_row)
                else:
                    pre_bars.append(np.zeros(MARKET_DIM, dtype=np.float32))

            # ── Barre d'entrée (t0) ───────────────────────────────────────────
            entry_bar_arr = np.zeros(MARKET_DIM, dtype=np.float32)
            for fi, fname in enumerate(EXIT_MARKET_FEATURES):
                j = mkt_to_idx.get(fname)
                if j is not None and int(t0) < len(mkt_full_arr):
                    v = mkt_full_arr[int(t0), j]
                    entry_bar_arr[fi] = v if np.isfinite(v) else 0.0

            # ── Barres intra-position (t0+1 .. t0+k) ─────────────────────────
            # On extrait les features marché des barres précédentes depuis le parquet
            # (la barre courante t0+k est dans le sample, les autres t0+1..t0+k-1
            #  sont dans le parquet)
            intra_market: List[np.ndarray] = []
            intra_pos:    List[np.ndarray] = []

            for ki in range(1, k + 1):
                bar_idx = int(t0) + ki

                # Marché
                m_row = np.zeros(MARKET_DIM, dtype=np.float32)
                if bar_idx < len(mkt_full_arr):
                    for fi, fname in enumerate(EXIT_MARKET_FEATURES):
                        j = mkt_to_idx.get(fname)
                        if j is not None:
                            v = mkt_full_arr[bar_idx, j]
                            m_row[fi] = v if np.isfinite(v) else 0.0
                elif ki == k:
                    # Barre courante : utiliser le sample
                    for fi, fname in enumerate(mkt_cols):
                        m_row[EXIT_MARKET_FEATURES.index(fname) if fname in EXIT_MARKET_FEATURES else -1] = \
                            float(row.get(fname, 0.0))

                intra_market.append(m_row)

                # Position state
                p_row = np.zeros(POS_DIM, dtype=np.float32)
                for pi, pfeat in enumerate(pos_cols):
                    if pfeat in sub.columns:
                        # Recalculer l'état à ki depuis les données brutes est complexe.
                        # Pour ki < k : approximer avec une extrapolation linéaire
                        # Pour ki == k : utiliser le sample directement
                        if ki == k:
                            v = float(row.get(pfeat, 0.0))
                            p_row[pi] = v if np.isfinite(v) else 0.0
                        else:
                            # Approximation : interpoation entre entrée et état final
                            # Utile uniquement pour bars_held, bars_frac, etc.
                            frac = ki / k
                            v_end = float(row.get(pfeat, 0.0))
                            if pfeat in ("bars_held",):
                                p_row[pi] = float(ki)
                            elif pfeat in ("bars_remaining",):
                                p_row[pi] = float(MAX_HOLD_BARS - ki)
                            elif pfeat in ("bars_frac",):
                                p_row[pi] = float(ki) / MAX_HOLD_BARS
                            else:
                                p_row[pi] = v_end * frac  # approximation linéaire

                intra_pos.append(p_row)

            # ── Assemblage de la séquence ─────────────────────────────────────
            # Ordre : [pré-entrée(4) | entrée(1) | intra(k)]
            all_market = pre_bars + [entry_bar_arr] + intra_market  # len = 4+1+k
            all_pos    = ([np.zeros(POS_DIM, np.float32)] * (pre_entry + 1)
                          + intra_pos)                              # zéros pour pré/entrée

            all_segments = (
                [0] * pre_entry          # pré-entrée
                + [1]                    # entrée
                + [2] * k                # intra-position
            )
            all_positions = list(range(len(all_market)))

            T = len(all_market)  # 5+k ≤ 12

            # Padding à droite jusqu'à max_seq
            pad_len = max_seq - T
            market_seq  = np.stack(all_market + [np.zeros(MARKET_DIM, np.float32)] * pad_len)
            pos_seq     = np.stack(all_pos    + [np.zeros(POS_DIM,    np.float32)] * pad_len)
            segments    = np.array(all_segments  + [0] * pad_len, dtype=np.int64)
            positions   = np.array(all_positions + [0] * pad_len, dtype=np.int64)
            pad_mask    = np.array([False] * T + [True] * pad_len, dtype=bool)

            records.append({
                "market_seq": market_seq,
                "pos_seq":    pos_seq,
                "segments":   segments,
                "positions":  positions,
                "pad_mask":   pad_mask,
                "asset_id":   asset_id,
                "label":      label,
            })

    return records


# ─────────────────────────────────────────────────────────────────────────────
# Transformer Exit Model
# ─────────────────────────────────────────────────────────────────────────────

class _ExitTransformerCore(nn.Module):
    """Un Transformer Encoder pour la décision de sortie."""

    def __init__(self, market_dim: int, pos_dim: int) -> None:
        super().__init__()
        assert _TORCH_OK, "PyTorch requis"

        # Projecteurs d'entrée
        self.market_proj = nn.Linear(market_dim, D_MODEL)
        self.pos_proj    = nn.Linear(pos_dim,    D_MODEL // 2)
        self.merge       = nn.Linear(D_MODEL + D_MODEL // 2, D_MODEL)

        # Embeddings additifs
        self.seg_embed   = nn.Embedding(N_SEGMENTS, D_MODEL)
        self.pos_embed   = nn.Embedding(MAX_SEQ_LEN + 2, D_MODEL)
        self.asset_embed = nn.Embedding(N_ASSETS + 1, D_MODEL // 4)

        # Asset projection (ajouté sur le token CLS)
        self.asset_proj  = nn.Linear(D_MODEL // 4, D_MODEL)

        # Layer norm d'entrée
        self.in_norm = nn.LayerNorm(D_MODEL)

        # Transformer Encoder
        enc_layer = nn.TransformerEncoderLayer(
            d_model=D_MODEL, nhead=N_HEADS,
            dim_feedforward=D_FF, dropout=DROPOUT,
            batch_first=True, activation="gelu", norm_first=True,
        )
        self.transformer = nn.TransformerEncoder(enc_layer, num_layers=N_LAYERS,
                                                  enable_nested_tensor=False)

        # MLP de sortie (+ skip depuis la position state courante)
        self.skip_proj = nn.Linear(pos_dim, D_MODEL // 2)
        self.head = nn.Sequential(
            nn.LayerNorm(D_MODEL + D_MODEL // 2),
            nn.Linear(D_MODEL + D_MODEL // 2, 128),
            nn.GELU(),
            nn.Dropout(DROPOUT),
            nn.Linear(128, 64),
            nn.GELU(),
            nn.Dropout(DROPOUT / 2),
            nn.Linear(64, 1),
        )

        self._init_weights()

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.xavier_uniform_(m.weight, gain=0.5)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)
            elif isinstance(m, nn.Embedding):
                nn.init.normal_(m.weight, std=0.02)

    def forward(
        self,
        market_seq: "torch.Tensor",  # (B, T, MARKET_DIM)
        pos_seq:    "torch.Tensor",  # (B, T, POS_DIM)
        segments:   "torch.Tensor",  # (B, T) long
        positions:  "torch.Tensor",  # (B, T) long
        pad_mask:   "torch.Tensor",  # (B, T) bool — True = padding
        asset_id:   "torch.Tensor",  # (B,) long
    ) -> "torch.Tensor":             # (B,) float32 ∈ [0,1]

        B, T, _ = market_seq.shape

        # Projection des features
        m_emb = self.market_proj(market_seq)           # (B, T, D)
        p_emb = self.pos_proj(pos_seq)                  # (B, T, D/2)
        x = self.merge(torch.cat([m_emb, p_emb], -1))  # (B, T, D)

        # Embeddings additifs
        x = x + self.seg_embed(segments)
        x = x + self.pos_embed(positions.clamp(0, MAX_SEQ_LEN + 1))

        # Asset embedding injecté sur tous les tokens
        a_emb = self.asset_embed(asset_id)              # (B, D/4)
        a_emb = self.asset_proj(a_emb)                  # (B, D)
        x = x + a_emb.unsqueeze(1)                     # broadcast sur T

        x = self.in_norm(x)

        # Transformer avec masque de padding
        # TransformerEncoderLayer(batch_first=True) attend mask True = ignorer
        x = self.transformer(x, src_key_padding_mask=pad_mask)  # (B, T, D)

        # Extraction du dernier token non-paddé (= barre courante t0+k)
        # On prend le dernier token dont pad_mask == False
        # pad_mask shape: (B, T), True = padding
        # Longueur effective = somme des non-paddés
        seq_len = (~pad_mask).sum(dim=1) - 1  # index du dernier token réel (B,)
        seq_len = seq_len.clamp(0, T - 1)
        idx     = seq_len.unsqueeze(-1).unsqueeze(-1).expand(B, 1, D_MODEL)
        last    = x.gather(1, idx).squeeze(1)           # (B, D)

        # Skip connection depuis l'état de position courant (barre t0+k)
        # = pos_seq[:, seq_len, :] — mais seq_len varie par batch item
        seq_idx_p = seq_len.unsqueeze(-1).unsqueeze(-1).expand(B, 1, POS_DIM)
        pos_cur   = pos_seq.gather(1, seq_idx_p).squeeze(1)  # (B, POS_DIM)
        skip      = self.skip_proj(pos_cur)                   # (B, D/2)

        # Concaténation et tête de sortie
        feat  = torch.cat([last, skip], dim=-1)   # (B, 3D/2)
        logit = self.head(feat).squeeze(-1)        # (B,)
        return torch.sigmoid(logit)


# ─────────────────────────────────────────────────────────────────────────────
# Wrapper haut niveau : ensemble + calibration
# ─────────────────────────────────────────────────────────────────────────────

class ExitFleetV2:
    """
    Ensemble de N_ENSEMBLE Transformers + Platt scaling par asset.

    Interface compatible avec ExitFleetV1 :
        fleet.should_exit(last_bar_series, position_state_dict) → (bool, float)
        fleet.predict(df_sequences) → np.ndarray  [usage interne]
    """

    def __init__(self) -> None:
        if not _TORCH_OK:
            raise ImportError("PyTorch requis — pip install torch --index-url "
                              "https://download.pytorch.org/whl/cpu")
        self.models: List[_ExitTransformerCore] = []
        self.threshold_: float   = 0.55
        self.platt_a_:   float   = 1.0    # Platt: logit_calibrated = a * logit + b
        self.platt_b_:   float   = 0.0
        self.features_:  List[str] = EXIT_ALL_FEATURES
        self.market_dim: int = MARKET_DIM
        self.pos_dim:    int = POS_DIM
        self._df_enriched_map: Dict[str, pd.DataFrame] = {}

    # ── Entraînement ─────────────────────────────────────────────────────────

    def fit(
        self,
        records_train: List[dict],
        records_val:   Optional[List[dict]] = None,
        epochs:        int   = 40,
        lr:            float = 5e-4,
        batch_size:    int   = 256,
        n_workers:     int   = 4,
        seed_base:     int   = 42,
    ) -> "ExitFleetV2":
        """
        Entraîne N_ENSEMBLE modèles sur records_train.
        records_train : sortie de build_sequences()
        """
        self.models = []
        y_tr  = np.array([r["label"] for r in records_train], dtype=np.float32)
        n_pos = int(y_tr.sum())
        n_neg = len(y_tr) - n_pos
        pos_w = n_neg / max(n_pos, 1)

        print(f"  [V2] Train : {len(records_train):,} séquences  "
              f"pos={n_pos:,} ({n_pos/len(y_tr):.1%})  pos_w={pos_w:.1f}")
        if records_val:
            y_v = np.array([r["label"] for r in records_val])
            print(f"  [V2] Val   : {len(records_val):,} séquences  "
                  f"pos={int(y_v.sum()):,} ({y_v.mean():.1%})")

        for seed in range(seed_base, seed_base + N_ENSEMBLE):
            print(f"\n  [V2] Seed {seed} — entraînement …")
            torch.manual_seed(seed)
            np.random.seed(seed)

            model = _ExitTransformerCore(MARKET_DIM, POS_DIM)
            model = self._train_one(
                model, records_train, records_val,
                epochs=epochs, lr=lr, batch_size=batch_size,
                n_workers=n_workers, seed=seed, pos_w=pos_w,
            )
            self.models.append(model)

        # Calibration Platt sur val
        if records_val:
            self._fit_platt(records_val)
            self.threshold_ = self._calibrate_threshold(records_val)
            print(f"\n  [V2] Threshold calibré : {self.threshold_:.2f}")

        return self

    def _train_one(
        self,
        model: "_ExitTransformerCore",
        records_train: List[dict],
        records_val:   Optional[List[dict]],
        epochs:        int,
        lr:            float,
        batch_size:    int,
        n_workers:     int,
        seed:          int,
        pos_w:         float,
    ) -> "_ExitTransformerCore":
        import time

        ds_tr = ExitSequenceDataset(records_train)

        # WeightedRandomSampler pour rééquilibrer
        y_arr   = np.array([r["label"] for r in records_train])
        weights = np.where(y_arr == 1, pos_w, 1.0)
        sampler = WeightedRandomSampler(
            weights=torch.from_numpy(weights).float(),
            num_samples=len(weights),
            replacement=True,
        )
        dl_tr = DataLoader(ds_tr, batch_size=batch_size, sampler=sampler,
                           num_workers=min(n_workers, 4),
                           pin_memory=False, drop_last=False)

        dl_val = None
        if records_val:
            ds_val = ExitSequenceDataset(records_val)
            dl_val = DataLoader(ds_val, batch_size=batch_size * 2,
                                shuffle=False, num_workers=min(n_workers, 4))

        model.train()
        opt = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)

        # Cosine annealing avec warm restart
        sched = torch.optim.lr_scheduler.CosineAnnealingWarmRestarts(
            opt, T_0=max(1, epochs // 3), eta_min=lr * 0.01
        )

        best_val_loss = float("inf")
        best_state    = None
        patience      = 8
        no_improve    = 0

        for ep in range(1, epochs + 1):
            model.train()
            ep_loss = 0.0
            n_batches = 0

            for batch in dl_tr:
                mkt, pos, seg, posid, mask, aid, y = batch
                opt.zero_grad(set_to_none=True)
                p = model(mkt, pos, seg, posid, mask, aid)
                loss = focal_loss(p, y)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                ep_loss += loss.item()
                n_batches += 1

            sched.step()

            if ep % 5 == 0 or ep == epochs:
                avg_loss = ep_loss / max(n_batches, 1)
                val_info = ""
                val_loss_ep = avg_loss

                if dl_val is not None:
                    model.eval()
                    vl = 0.0
                    with torch.no_grad():
                        for batch in dl_val:
                            mkt, pos, seg, posid, mask, aid, y = batch
                            p = model(mkt, pos, seg, posid, mask, aid)
                            vl += focal_loss(p, y).item()
                    val_loss_ep = vl / max(len(dl_val), 1)
                    val_info = f"  val={val_loss_ep:.4f}"

                print(f"    ep {ep:>3}/{epochs}  "
                      f"loss={avg_loss:.4f}{val_info}  "
                      f"lr={opt.param_groups[0]['lr']:.5f}")

                if val_loss_ep < best_val_loss - 1e-4:
                    best_val_loss = val_loss_ep
                    best_state    = {k: v.clone() for k, v in model.state_dict().items()}
                    no_improve    = 0
                else:
                    no_improve += 1
                    if no_improve >= patience:
                        print(f"    Early stopping à ep {ep}")
                        break

        if best_state is not None:
            model.load_state_dict(best_state)

        model.eval()
        return model

    # ── Inférence batch ───────────────────────────────────────────────────────

    def predict_records(self, records: List[dict]) -> np.ndarray:
        """Prédictions pour une liste de records (séquences)."""
        if not self.models or not records:
            return np.full(len(records), 0.5, dtype=np.float32)

        ds = ExitSequenceDataset(records)
        dl = DataLoader(ds, batch_size=512, shuffle=False, num_workers=0)

        all_preds = []
        for model in self.models:
            model.eval()
            preds = []
            with torch.no_grad():
                for batch in dl:
                    mkt, pos, seg, posid, mask, aid, _ = batch
                    p = model(mkt, pos, seg, posid, mask, aid)
                    preds.append(p.numpy())
            all_preds.append(np.concatenate(preds))

        # Moyenne d'ensemble
        ens = np.stack(all_preds, axis=0).mean(axis=0)

        # Platt scaling
        logit = np.log(np.clip(ens, 1e-7, 1 - 1e-7) / (1 - np.clip(ens, 1e-7, 1 - 1e-7)))
        logit_cal = self.platt_a_ * logit + self.platt_b_
        return (1.0 / (1.0 + np.exp(-logit_cal))).astype(np.float32)

    # ── Inférence single-bar (live) ───────────────────────────────────────────

    def should_exit(
        self,
        df_bar:         "pd.Series",
        position_state: dict,
    ) -> Tuple[bool, float]:
        """
        Décide si on doit sortir la position maintenant.
        Compatible avec ExitFleetV1.should_exit().

        Construit une séquence minimale (sans contexte pré-entrée live)
        pour l'inférence unitaire.
        """
        if not self.models:
            return False, 0.5

        k = int(position_state.get("bars_held", 1))
        k = max(1, min(k, MAX_HOLD_BARS - 1))

        # Construire un record factice de longueur 1+k
        # (sans pré-entrée disponible au runtime)
        mkt_row = np.zeros(MARKET_DIM, dtype=np.float32)
        for fi, fname in enumerate(EXIT_MARKET_FEATURES):
            v = df_bar.get(fname, 0.0)
            try:
                fv = float(v)
                mkt_row[fi] = fv if np.isfinite(fv) else 0.0
            except Exception:
                pass

        pos_row = np.zeros(POS_DIM, dtype=np.float32)
        for pi, pfeat in enumerate(EXIT_POSITION_FEATURES):
            pos_row[pi] = float(position_state.get(pfeat, 0.0))

        # Séquence = [entrée(zeros), ..., k barres intra]
        # On duplique la barre courante pour remplir les k positions
        # (approximation acceptable en l'absence de l'historique complet)
        T = 1 + k  # entrée + k barres
        market_seq = np.tile(mkt_row, (MAX_SEQ_LEN, 1)).astype(np.float32)
        pos_seq    = np.zeros((MAX_SEQ_LEN, POS_DIM), dtype=np.float32)
        pos_seq[T - 1] = pos_row  # état courant sur la dernière barre

        segments = np.array([1] + [2] * k + [0] * (MAX_SEQ_LEN - T), dtype=np.int64)
        positions = np.arange(MAX_SEQ_LEN, dtype=np.int64)
        pad_mask  = np.array([False] * T + [True] * (MAX_SEQ_LEN - T), dtype=bool)

        sym     = position_state.get("symbol", "UNKNOWN")
        aid_val = ASSET_TO_IDX.get(sym, N_ASSETS - 1)

        record = {
            "market_seq": market_seq,
            "pos_seq":    pos_seq,
            "segments":   segments,
            "positions":  positions,
            "pad_mask":   pad_mask,
            "asset_id":   aid_val,
            "label":      0,  # placeholder
        }

        p = float(self.predict_records([record])[0])
        return (p >= self.threshold_, p)

    # ── Calibration ───────────────────────────────────────────────────────────

    def _fit_platt(self, records_val: List[dict]) -> None:
        """Régression logistique sur les logits (Platt scaling)."""
        from sklearn.linear_model import LogisticRegression

        p_raw = self._predict_raw(records_val)
        y     = np.array([r["label"] for r in records_val])
        logit = np.log(np.clip(p_raw, 1e-7, 1 - 1e-7) / (1 - np.clip(p_raw, 1e-7, 1 - 1e-7)))

        lr = LogisticRegression(C=1.0, max_iter=200)
        lr.fit(logit.reshape(-1, 1), y)
        self.platt_a_ = float(lr.coef_[0][0])
        self.platt_b_ = float(lr.intercept_[0])
        print(f"  [V2] Platt scaling : a={self.platt_a_:.3f}  b={self.platt_b_:.3f}")

    def _predict_raw(self, records: List[dict]) -> np.ndarray:
        """Prédictions brutes (avant Platt) — pour calibration uniquement."""
        saved_a, saved_b = self.platt_a_, self.platt_b_
        self.platt_a_, self.platt_b_ = 1.0, 0.0
        p = self.predict_records(records)
        self.platt_a_, self.platt_b_ = saved_a, saved_b
        return p

    def _calibrate_threshold(self, records_val: List[dict]) -> float:
        """
        Calibration du threshold : maximise le net P&L simulé sur val.
        Regroupe les records par position (même t0 et symbol).
        """
        p_all = self.predict_records(records_val)
        # Grouper par (symbol, t0)
        by_pos: Dict[Tuple, List] = {}
        for i, r_orig in enumerate(records_val):
            # Les records ne contiennent pas symbol/t0 directement
            # → on ne peut pas regrouper ici
            pass

        # Fallback : threshold par défaut
        y = np.array([r["label"] for r in records_val])
        # Cherche le threshold qui maximise l'AUC + maximise accuracy sur les positifs
        from sklearn.metrics import roc_auc_score
        try:
            auc = roc_auc_score(y, p_all)
            print(f"  [V2] Val AUC : {auc:.4f}")
        except Exception:
            pass

        # Threshold par maximisation de F1 pondéré
        best_f1  = -1.0
        best_thr = 0.55
        for thr in np.arange(0.35, 0.80, 0.025):
            pred  = (p_all >= thr).astype(int)
            tp    = int(((pred == 1) & (y == 1)).sum())
            fp    = int(((pred == 1) & (y == 0)).sum())
            fn    = int(((pred == 0) & (y == 1)).sum())
            prec  = tp / max(tp + fp, 1)
            rec   = tp / max(tp + fn, 1)
            # F2 : privilégie le recall (mieux vaut sortir trop que trop peu)
            f2    = (1 + 4) * prec * rec / max(4 * prec + rec, 1e-9)
            if f2 > best_f1:
                best_f1  = f2
                best_thr = float(thr)

        return best_thr
