"""
ai/level_2/transformer.py — TRANSFORMER TEMPOREL POUR LA PRÉDICTION 4h
=======================================================================

Architecture : Transformer Encoder avec token [CLS] pour classification binaire.

Pourquoi Transformer > HistGBT pour ce problème :
  HistGBT voit une barre comme un vecteur de features indépendant.
  Le Transformer voit une séquence de 64 barres et apprend par attention
  quels moments passés (breakouts, volumes, patterns) annoncent le move 4h.
  Il capture les séquences temporelles que les arbres ne peuvent pas modéliser :
  "prix qui consolide 12h puis accelere" ou "golden cross suivi d'un pull-back".

Architecture détaillée :
  Input  (B, T, F)
    → Projection linéaire F → d_model
    → + Embedding positionnel learnable (T positions)
    → + Token [CLS] prepend (B, T+1, d_model)
    → 4× TransformerEncoderLayer (pre-norm, 8 têtes, ff=4×d_model)
    → LayerNorm finale
    → Classification head sur le token [CLS]
    → sigmoid → P(y_long=1)

Hyperparamètres :
  seq_len  = 64   barres (64h = ~2.7 jours de contexte)
  d_model  = 128  dimensions de l'espace latent
  n_heads  = 8    têtes d'attention (d_head = 16)
  n_layers = 4    couches encoder
  dropout  = 0.15 (régularisation modérée — peu de labels positifs)
  ff_mult  = 4    feedforward dimension = 512

Training :
  Loss   : BCE pondérée (scale_pos_weight pour 1.7% de positifs)
  Optim  : AdamW (lr=3e-4, weight_decay=1e-3)
  Sched  : Cosine annealing avec warmup linéaire (5 époques)
  Batch  : 512
  Epochs : max 60, early stop sur AUC val (patience=10)
  Grad   : clip_norm=1.0
  Label smoothing : 0.05 (robustesse au bruit dans les labels)
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import torch.nn.functional as F
from sklearn.metrics import roc_auc_score
from sklearn.preprocessing import StandardScaler
from torch.utils.data import DataLoader, Dataset


# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TransformerConfig:
    """
    Hyperparamètres calibrés pour ~1 000-2 000 exemples positifs.

    Règle : ≤ 50 paramètres par exemple positif.
    Avec ~1 266 positifs → budget ≤ 63 000 params.
    Modèle compact : d=64, 2 couches, 4 têtes = ~50 k params.

    seq_len=24 : 1 journée de contexte (24h).
    Résultats empiriques : au-delà de 24h, le bruit augmente plus vite
    que le signal pour nos features 1h sur cette quantité de données.
    """
    seq_len:    int   = 24      # 1 journée de contexte
    d_model:    int   = 48      # dimension latente — ratio ≤50 params/label
    n_heads:    int   = 4       # têtes d'attention (d_head=12)
    n_layers:   int   = 2       # couches encoder
    dropout:    float = 0.30    # dropout fort — régularisation accrue (était 0.15)
    ff_mult:    int   = 4       # feedforward = 256

    lr:          float = 2e-4   # légèrement plus faible pour stabilité
    weight_decay:float = 5e-3   # plus fort (5× vs 1×) — lutte contre surapprentissage
    batch_size:  int   = 256    # réduit pour plus de mises à jour par époque
    max_epochs:  int   = 80     # plus d'époques — modèle plus simple converge plus lentement
    warmup_epochs:int  = 8      # warmup plus long
    patience:    int   = 15     # plus de patience — évite d'arrêter trop tôt
    grad_clip:   float = 1.0
    label_smooth:float = 0.08   # plus de lissage — labels 4h sont bruités
    min_pos_weight: float = 5.0
    max_pos_weight: float = 80.0

    device: str = "cuda" if torch.cuda.is_available() else "cpu"


# ─────────────────────────────────────────────────────────────────────────────
# Dataset : fenêtres glissantes lazy
# ─────────────────────────────────────────────────────────────────────────────

class SequenceDataset(Dataset):
    """
    Dataset lazy : construit les fenêtres à la demande sans matérialiser en mémoire.

    Pour N barres × F features et seq_len=64 :
      Taille = O(N×F) au lieu de O((N-seq_len)×seq_len×F)
      Pour N=50k, F=64, seq_len=64 : 12 MB vs 770 MB
    """

    def __init__(
        self,
        X: np.ndarray,       # (N, F) features normalisées
        y: np.ndarray,       # (N,) labels {0, 1, -1}
        indices: np.ndarray, # indices valides (y >= 0, assez de contexte)
        seq_len: int = 64,
    ):
        self.X       = torch.from_numpy(X.astype(np.float32))
        self.y       = torch.from_numpy(y.astype(np.float32))
        self.indices = indices
        self.seq_len = seq_len

    def __len__(self) -> int:
        return len(self.indices)

    def __getitem__(self, item: int) -> Tuple[torch.Tensor, torch.Tensor]:
        i   = int(self.indices[item])
        seq = self.X[i - self.seq_len + 1 : i + 1]   # (seq_len, F)
        lbl = self.y[i]
        return seq, lbl


# ─────────────────────────────────────────────────────────────────────────────
# Modèle
# ─────────────────────────────────────────────────────────────────────────────

class TradingTransformer(nn.Module):
    """
    Transformer Encoder avec token [CLS] pour classification binaire.

    Le token [CLS] est prepend à la séquence et agrège l'information de tous
    les past bars via l'attention. La classification head opère sur ce token.

    Architecture pre-norm (norm_first=True) : plus stable pour petits datasets,
    gradients moins susceptibles d'exploser ou disparaître.
    """

    def __init__(self, n_features: int, cfg: TransformerConfig):
        super().__init__()
        self.cfg     = cfg
        self.seq_len = cfg.seq_len
        d            = cfg.d_model

        # ── Projection d'entrée ───────────────────────────────────────────────
        self.input_proj = nn.Sequential(
            nn.Linear(n_features, d),
            nn.LayerNorm(d),
        )

        # ── Token [CLS] + embeddings positionnels ─────────────────────────────
        # [CLS] = learnable vecteur prepend à chaque séquence
        self.cls_token  = nn.Parameter(torch.randn(1, 1, d) * 0.02)
        # Positions 0 = [CLS], 1..seq_len = barres historiques
        self.pos_embed  = nn.Embedding(cfg.seq_len + 1, d)
        nn.init.trunc_normal_(self.pos_embed.weight, std=0.02)

        # ── Encodeur Transformer ──────────────────────────────────────────────
        encoder_layer = nn.TransformerEncoderLayer(
            d_model         = d,
            nhead           = cfg.n_heads,
            dim_feedforward = d * cfg.ff_mult,
            dropout         = cfg.dropout,
            batch_first     = True,
            norm_first      = True,     # pre-norm : plus stable
            activation      = "gelu",   # GELU > ReLU pour la finance
        )
        self.encoder = nn.TransformerEncoder(encoder_layer, num_layers=cfg.n_layers)
        self.norm    = nn.LayerNorm(d)

        # ── Tête de classification ─────────────────────────────────────────────
        self.head = nn.Sequential(
            nn.Dropout(cfg.dropout),
            nn.Linear(d, d // 2),
            nn.GELU(),
            nn.Dropout(cfg.dropout / 2),
            nn.Linear(d // 2, 1),
        )

        self._init_weights()

    def _init_weights(self) -> None:
        for m in self.modules():
            if isinstance(m, nn.Linear):
                nn.init.trunc_normal_(m.weight, std=0.02)
                if m.bias is not None:
                    nn.init.zeros_(m.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        x : (B, T, F) — batch de séquences
        → logit (B,) non-sigmoid (pour BCEWithLogitsLoss)
        """
        B, T, _ = x.shape

        # Projection + embedding positionnel
        h   = self.input_proj(x)                          # (B, T, d)
        cls = self.cls_token.expand(B, -1, -1)            # (B, 1, d)
        h   = torch.cat([cls, h], dim=1)                  # (B, T+1, d)

        pos = torch.arange(T + 1, device=x.device)
        h   = h + self.pos_embed(pos).unsqueeze(0)        # (B, T+1, d)

        # Encodeur + extraction du token [CLS]
        h   = self.encoder(h)                             # (B, T+1, d)
        h   = self.norm(h[:, 0, :])                       # (B, d)  — token CLS

        return self.head(h).squeeze(-1)                   # (B,)

    @torch.no_grad()
    def predict_proba(self, x: torch.Tensor) -> np.ndarray:
        """Retourne P(y=1) en numpy, par batch."""
        self.eval()
        probs = []
        bsz   = 1024
        for i in range(0, len(x), bsz):
            logits = self(x[i:i+bsz])
            probs.append(torch.sigmoid(logits).cpu().numpy())
        return np.concatenate(probs)


# ─────────────────────────────────────────────────────────────────────────────
# Training loop
# ─────────────────────────────────────────────────────────────────────────────

def _build_loader(
    X_norm: np.ndarray,
    y: np.ndarray,
    mask: np.ndarray,
    seq_len: int,
    batch_size: int,
    shuffle: bool,
    asset_ids: Optional[np.ndarray] = None,
) -> DataLoader:
    """
    Construit un DataLoader à partir d'un masque booléen.

    asset_ids : si fourni (int par barre), les séquences qui croisent la frontière
    entre deux actifs sont exclues — garantit qu'une séquence ne mélange pas BTC et ETH.
    """
    indices   = np.where(mask)[0]
    valid_idx = indices[indices >= seq_len - 1]
    valid_y   = y[valid_idx]
    gray_ok   = valid_y >= 0
    candidate = valid_idx[gray_ok]

    if asset_ids is not None:
        # Garder uniquement les indices où toute la fenêtre appartient au même actif
        def same_asset(i: int) -> bool:
            window = asset_ids[i - seq_len + 1 : i + 1]
            return bool(np.all(window == window[-1]))
        candidate = np.array([i for i in candidate if same_asset(i)])

    ds = SequenceDataset(X_norm, y, candidate, seq_len)
    return DataLoader(
        ds, batch_size=batch_size, shuffle=shuffle,
        pin_memory=True, num_workers=0,
    )


def train_transformer(
    df: pd.DataFrame,
    train_mask: np.ndarray,
    val_mask: np.ndarray,
    features: List[str],
    cfg: Optional[TransformerConfig] = None,
    verbose: bool = True,
    asset_ids: Optional[np.ndarray] = None,
) -> Tuple["TradingTransformer", StandardScaler, Dict]:
    """
    Entraîne le Transformer sur les données de train, sélectionne le meilleur
    checkpoint sur l'AUC validation, retourne (modèle, scaler, métriques).

    Arguments
    ---------
    df         : DataFrame combiné multi-actif avec y_long, features
                 (index continu RangeIndex ou DatetimeIndex)
    train_mask : masque booléen train
    val_mask   : masque booléen val
    features   : liste des features (FEATURES_LONG)
    cfg        : TransformerConfig (défaut si None)
    verbose    : afficher la progression
    asset_ids  : optionnel, array int (0=BTC, 1=ETH, 2=SOL) — exclut les
                 séquences qui croisent une frontière d'actif

    Retourne
    --------
    model      : TradingTransformer entraîné (mode eval())
    scaler     : StandardScaler ajusté sur train
    metrics    : dict avec best_auc, best_epoch, etc.
    """
    cfg    = cfg or TransformerConfig()
    device = torch.device(cfg.device)

    # ── Normalisation sur train uniquement ────────────────────────────────────
    avail   = [f for f in features if f in df.columns]
    X_raw   = df[avail].values.astype(np.float64)
    X_raw   = np.nan_to_num(X_raw, nan=0.0, posinf=0.0, neginf=0.0)

    scaler  = StandardScaler()
    scaler.fit(X_raw[train_mask])
    X_norm  = scaler.transform(X_raw).astype(np.float32)
    X_norm  = np.clip(X_norm, -10.0, 10.0)

    y = df["y_long"].values.astype(np.float32)

    # ── DataLoaders (asset_ids pour éviter les séquences cross-actif) ────────────
    train_loader = _build_loader(X_norm, y, train_mask, cfg.seq_len, cfg.batch_size,
                                  shuffle=True,  asset_ids=asset_ids)
    val_loader   = _build_loader(X_norm, y, val_mask,   cfg.seq_len, cfg.batch_size,
                                  shuffle=False, asset_ids=None)    # val = BTC seul

    n_pos  = float((y[train_mask] == 1).sum())
    n_neg  = float((y[train_mask] == 0).sum())
    spw    = float(np.clip(n_neg / max(n_pos, 1), cfg.min_pos_weight, cfg.max_pos_weight))

    # ── Modèle ────────────────────────────────────────────────────────────────
    model = TradingTransformer(n_features=len(avail), cfg=cfg).to(device)
    total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)

    # ── Optimiseur + scheduler ────────────────────────────────────────────────
    optimizer = torch.optim.AdamW(
        model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay
    )

    def lr_lambda(epoch: int) -> float:
        if epoch < cfg.warmup_epochs:
            return (epoch + 1) / cfg.warmup_epochs
        progress = (epoch - cfg.warmup_epochs) / max(cfg.max_epochs - cfg.warmup_epochs, 1)
        return 0.5 * (1 + math.cos(math.pi * progress))

    scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, lr_lambda)
    criterion = nn.BCEWithLogitsLoss(pos_weight=torch.tensor(spw, device=device))

    if verbose:
        print(f"\n{'='*70}")
        print(f"TRANSFORMER — {total_params:,} paramètres  |  device={cfg.device}")
        print(f"  seq_len={cfg.seq_len}  d={cfg.d_model}  heads={cfg.n_heads}  "
              f"layers={cfg.n_layers}  spw={spw:.1f}")
        print(f"  train_batches={len(train_loader)}  val_batches={len(val_loader)}")
        print(f"{'='*70}")

    best_auc   = 0.0
    best_state = None
    patience_c = 0
    history: List[Dict] = []

    for epoch in range(cfg.max_epochs):
        t0 = time.time()

        # ── Train ─────────────────────────────────────────────────────────────
        model.train()
        train_loss = 0.0
        n_batches  = 0

        for xb, yb in train_loader:
            xb, yb = xb.to(device), yb.to(device)
            # Label smoothing
            yb_smooth = yb * (1 - cfg.label_smooth) + 0.5 * cfg.label_smooth
            optimizer.zero_grad(set_to_none=True)
            logits = model(xb)
            loss   = criterion(logits, yb_smooth)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), cfg.grad_clip)
            optimizer.step()
            train_loss += loss.item()
            n_batches  += 1

        scheduler.step()
        train_loss /= max(n_batches, 1)

        # ── Validation ────────────────────────────────────────────────────────
        model.eval()
        val_logits: List[np.ndarray] = []
        val_labels: List[np.ndarray] = []

        with torch.no_grad():
            for xb, yb in val_loader:
                xb = xb.to(device)
                val_logits.append(torch.sigmoid(model(xb)).cpu().numpy())
                val_labels.append(yb.numpy())

        val_prob = np.concatenate(val_logits)
        val_y    = np.concatenate(val_labels)

        if val_y.sum() < 2 or (val_y == 0).sum() < 2:
            val_auc = 0.5
        else:
            val_auc = float(roc_auc_score(val_y, val_prob))

        dt = time.time() - t0
        history.append({"epoch": epoch, "loss": train_loss, "val_auc": val_auc})

        if verbose and (epoch % 5 == 0 or epoch < 3):
            lr_now = optimizer.param_groups[0]["lr"]
            print(f"  epoch {epoch:3d}  loss={train_loss:.4f}  "
                  f"val_auc={val_auc:.4f}  lr={lr_now:.2e}  t={dt:.1f}s")

        if val_auc > best_auc:
            best_auc   = val_auc
            best_epoch = epoch
            best_state = {k: v.cpu().clone() for k, v in model.state_dict().items()}
            patience_c = 0
        else:
            patience_c += 1
            if patience_c >= cfg.patience:
                if verbose:
                    print(f"  Early stop à l'époque {epoch} (patience={cfg.patience})")
                break

    # Charger le meilleur état
    if best_state is not None:
        model.load_state_dict(best_state)
    model.eval()

    if verbose:
        print(f"\n  Best AUC={best_auc:.4f}  @epoch={best_epoch}")

    metrics = {
        "best_auc":   round(best_auc, 4),
        "best_epoch": best_epoch,
        "n_features": len(avail),
        "n_params":   total_params,
        "spw":        round(spw, 2),
        "history":    history[-5:],     # dernières 5 époques pour le rapport
    }

    return model, scaler, metrics


# ─────────────────────────────────────────────────────────────────────────────
# Inférence batch-efficace
# ─────────────────────────────────────────────────────────────────────────────

def predict_transformer(
    model: TradingTransformer,
    scaler: StandardScaler,
    df: pd.DataFrame,
    mask: np.ndarray,
    features: List[str],
    batch_size: int = 1024,
) -> np.ndarray:
    """
    Calcule P(y_long=1) sur le subset df[mask] via le Transformer.
    Retourne un array de longueur mask.sum().
    """
    cfg    = model.cfg
    device = torch.device(cfg.device)

    avail  = [f for f in features if f in df.columns]
    X_raw  = df[avail].values.astype(np.float64)
    X_raw  = np.nan_to_num(X_raw, nan=0.0, posinf=0.0, neginf=0.0)
    X_norm = np.clip(scaler.transform(X_raw).astype(np.float32), -10.0, 10.0)

    indices     = np.where(mask)[0]
    valid_idx   = indices[indices >= cfg.seq_len - 1]
    n_total_mask = mask.sum()

    probs = np.full(n_total_mask, 0.5, dtype=np.float32)   # défaut = 0.5 pour barres sans contexte

    model.eval()
    with torch.no_grad():
        for batch_start in range(0, len(valid_idx), batch_size):
            batch_idx = valid_idx[batch_start : batch_start + batch_size]
            # Construire les séquences pour ce batch
            seqs = np.stack([
                X_norm[i - cfg.seq_len + 1 : i + 1]
                for i in batch_idx
            ])                                        # (B, T, F)
            seqs_t = torch.from_numpy(seqs).to(device)
            logits = model(seqs_t)
            p      = torch.sigmoid(logits).cpu().numpy()

            # Positions dans le mask
            for j, global_i in enumerate(batch_idx):
                # Position de global_i dans mask (offset dans le tableau de résultats)
                pos_in_mask = int(np.searchsorted(indices, global_i))
                if pos_in_mask < n_total_mask:
                    probs[pos_in_mask] = p[j]

    return probs
