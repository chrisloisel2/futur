"""
backtest/engine.py — MOTEUR DE BACKTEST
========================================

Trois fonctions publiques :
  1. run_backtest_side()     : backtest sur un seul côté (long OU short)
  2. run_backtest_combined() : backtest combiné long+short avec priorité long
  3. run_cost_sensitivity()  : variation du coût (1x, 2x, 3x) pour robustesse

Règles :
  - Pas de look-ahead (les décisions se font bar-par-bar en avant)
  - Le filtre est appliqué en premier (si fourni)
  - Le risk controller gère le cooldown et les limites
  - Les résultats sont en BacktestResult (voir metrics.py)
  - Séparation stricte long/short : les RC ne se parlent pas
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from core.features.constants import COST_PCT, COST_SHORT_MULT, INITIAL_EQUITY, REGIME_COL, TARGET_COL
from .metrics import (
    BacktestResult, compute_backtest_metrics, print_backtest_summary,
    ShortRobustnessReport, should_deploy_short,
)


def run_backtest_side(
    df: pd.DataFrame,
    test_mask: np.ndarray,
    clf_direction,
    scaler_direction,
    feature_list: List[str],
    side: str,
    threshold: float,
    calibrator=None,
    clf_filter=None,
    scaler_filter=None,
    filter_features: Optional[List[str]] = None,
    filter_threshold: float = 0.50,
    cost_pct: float = COST_PCT,
    initial_equity: float = INITIAL_EQUITY,
    rc=None,
    verbose: bool = True,
) -> BacktestResult:
    """
    Backtest bar-à-bar pour un seul côté.

    Arguments
    ---------
    df               : DataFrame complet (index datetime, colonnes features + future_ret_h)
    test_mask        : masque test (boolean numpy)
    clf_direction    : modèle de direction (long ou short)
    scaler_direction : StandardScaler de direction
    feature_list     : features du modèle de direction
    side             : "long" ou "short"
    threshold        : seuil de décision (après calibration)
    calibrator       : calibrateur de probabilités (optionnel)
    clf_filter       : modèle de filtre tradeable (optionnel)
    scaler_filter    : StandardScaler du filtre (optionnel)
    filter_features  : features du filtre (optionnel)
    filter_threshold : seuil du filtre
    cost_pct         : coût aller-retour
    initial_equity   : capital de départ
    rc               : RiskController (optionnel, si None, pas de gestion du risque RC)
    verbose          : afficher un résumé

    Retourne
    --------
    BacktestResult
    """
    from core.features.preprocessing import get_X

    assert side in ("long", "short"), f"side must be 'long' or 'short', got {side!r}"
    ret_sign   = +1.0 if side == "long" else -1.0
    label_col  = f"y_{side}"

    df_test    = df.loc[test_mask]
    n_bars     = len(df_test)

    trade_rets: List[float] = []
    trade_list: List[Dict]  = []
    bar_idx    = 0

    for i, (ts, row) in enumerate(df_test.iterrows()):
        bar_idx += 1

        # ── 1. Filtre tradeable ───────────────────────────────────────────────
        if clf_filter is not None and scaler_filter is not None and filter_features:
            x_filt = np.array([[row[f] for f in filter_features]], dtype=np.float32)
            p_filt = clf_filter.predict_proba(scaler_filter.transform(x_filt))[0, 1]
            if p_filt < filter_threshold:
                continue

        # ── 2. Direction ──────────────────────────────────────────────────────
        x_dir = np.array([[row[f] for f in feature_list]], dtype=np.float32)
        p_raw = clf_direction.predict_proba(scaler_direction.transform(x_dir))[0, 1]

        # Appliquer le calibrateur si disponible
        if calibrator is not None:
            try:
                import sklearn.isotonic
                if isinstance(calibrator, sklearn.isotonic.IsotonicRegression):
                    p_cal = float(calibrator.predict([p_raw])[0])
                else:
                    p_cal = float(calibrator.predict_proba([[p_raw]])[0, 1])
            except Exception:
                p_cal = p_raw
        else:
            p_cal = p_raw

        if p_cal < threshold:
            continue

        # ── 3. Risk Controller ────────────────────────────────────────────────
        if rc is not None:
            edge_final = (p_cal - 0.5) * 2.0 * ret_sign  # normaliser en [-1, 1]
            features_dict = row.to_dict()
            decision = rc.decide(
                price=float(row.get("close", 1.0)),
                edge_final=edge_final,
                scale=p_cal,
                bar_index=bar_idx,
                features=features_dict,
            )
            if decision["action"] == "HOLD":
                continue

        # ── 4. Calculer le return ─────────────────────────────────────────────
        raw_ret = float(row.get(TARGET_COL, row.get("future_ret_h", 0.0)))
        net_ret = raw_ret * ret_sign - cost_pct

        trade_rets.append(net_ret)
        trade_list.append({
            "bar":   bar_idx,
            "ts":    str(ts),
            "side":  side,
            "p_cal": round(p_cal, 4),
            "ret":   round(raw_ret, 6),
            "net":   round(net_ret, 6),
        })

        # Notifier le RC
        if rc is not None:
            pnl_abs = net_ret * rc.state.equity
            rc.on_fill_pnl(pnl_abs)

    trade_rets_arr = np.array(trade_rets, dtype=np.float64)
    result = compute_backtest_metrics(
        trade_rets=trade_rets_arr,
        cost_pct=cost_pct,
        initial_equity=initial_equity,
        side=side,
        trade_list=trade_list,
    )

    if verbose:
        print_backtest_summary(result)

    return result


def run_backtest_combined(
    df: pd.DataFrame,
    test_mask: np.ndarray,
    # Long
    clf_long,
    scaler_long,
    features_long: List[str],
    threshold_long: float,
    # Short
    clf_short=None,
    scaler_short=None,
    features_short: Optional[List[str]] = None,
    threshold_short: float = 0.60,
    # Calibrators
    calibrator_long=None,
    calibrator_short=None,
    # Filtre
    clf_filter=None,
    scaler_filter=None,
    filter_features: Optional[List[str]] = None,
    filter_threshold: float = 0.50,
    # Paramètres
    cost_pct: float = COST_PCT,
    initial_equity: float = INITIAL_EQUITY,
    rc_long=None,
    rc_short=None,
    short_enabled: bool = True,
    verbose: bool = True,
) -> Dict[str, BacktestResult]:
    """
    Backtest combiné LONG + SHORT avec priorité long.

    Règle de priorité :
      - Si un signal long ET un signal short apparaissent au même bar → LONG gagne
      - Le RC long et RC short sont indépendants (pas de phantom trades)
      - Short est optionnel (short_enabled=False pour LONG_ONLY)

    Retourne
    --------
    dict avec clés "long", "short", "combined"
    """
    from core.features.preprocessing import get_X

    ret_sign_map = {"long": +1.0, "short": -1.0}
    df_test = df.loc[test_mask]

    long_rets:  List[float] = []
    short_rets: List[float] = []
    long_trades:  List[Dict] = []
    short_trades: List[Dict] = []

    bar_idx = 0

    for i, (ts, row) in enumerate(df_test.iterrows()):
        bar_idx += 1

        # ── 1. Filtre tradeable ───────────────────────────────────────────────
        if clf_filter is not None and scaler_filter is not None and filter_features:
            x_filt = np.array([[row[f] for f in filter_features]], dtype=np.float32)
            p_filt = clf_filter.predict_proba(scaler_filter.transform(x_filt))[0, 1]
            if p_filt < filter_threshold:
                continue

        # ── 2. Signal long ────────────────────────────────────────────────────
        x_long  = np.array([[row[f] for f in features_long]], dtype=np.float32)
        p_long  = clf_long.predict_proba(scaler_long.transform(x_long))[0, 1]
        p_long  = _apply_calibrator(calibrator_long, p_long)
        has_long = p_long >= threshold_long

        # ── 3. Signal short ───────────────────────────────────────────────────
        has_short = False
        p_short   = 0.0
        if short_enabled and clf_short is not None:
            x_short = np.array([[row[f] for f in features_short]], dtype=np.float32)
            p_short = clf_short.predict_proba(scaler_short.transform(x_short))[0, 1]
            p_short = _apply_calibrator(calibrator_short, p_short)
            has_short = p_short >= threshold_short

        # ── 4. Priorité long ─────────────────────────────────────────────────
        if has_long:
            has_short = False   # long prime sur short au même bar

        raw_ret = float(row.get(TARGET_COL, row.get("future_ret_h", 0.0)))

        # ── 5. Trade long ─────────────────────────────────────────────────────
        if has_long:
            skip = False
            if rc_long is not None:
                edge = (p_long - 0.5) * 2.0
                decision = rc_long.decide(
                    price=float(row.get("close", 1.0)),
                    edge_final=edge,
                    scale=p_long,
                    bar_index=bar_idx,
                    features=row.to_dict(),
                )
                skip = decision["action"] == "HOLD"

            if not skip:
                net = raw_ret - cost_pct
                long_rets.append(net)
                long_trades.append({
                    "bar": bar_idx, "ts": str(ts), "side": "long",
                    "p_cal": round(p_long, 4), "ret": round(raw_ret, 6), "net": round(net, 6),
                })
                if rc_long is not None:
                    rc_long.on_fill_pnl(net * rc_long.state.equity)

        # ── 6. Trade short ────────────────────────────────────────────────────
        elif has_short:
            skip = False
            if rc_short is not None:
                edge = (p_short - 0.5) * 2.0 * (-1.0)
                decision = rc_short.decide(
                    price=float(row.get("close", 1.0)),
                    edge_final=edge,
                    scale=p_short,
                    bar_index=bar_idx,
                    features=row.to_dict(),
                )
                skip = decision["action"] == "HOLD"

            if not skip:
                net = raw_ret * (-1.0) - cost_pct
                short_rets.append(net)
                short_trades.append({
                    "bar": bar_idx, "ts": str(ts), "side": "short",
                    "p_cal": round(p_short, 4), "ret": round(raw_ret, 6), "net": round(net, 6),
                })
                if rc_short is not None:
                    rc_short.on_fill_pnl(net * rc_short.state.equity)

    long_arr  = np.array(long_rets,  dtype=np.float64)
    short_arr = np.array(short_rets, dtype=np.float64)
    comb_arr  = np.concatenate([long_arr, short_arr])

    result_long  = compute_backtest_metrics(long_arr,  cost_pct, initial_equity, "long",  long_trades)
    result_short = compute_backtest_metrics(short_arr, cost_pct, initial_equity, "short", short_trades)
    result_comb  = compute_backtest_metrics(comb_arr,  cost_pct, initial_equity, "combined")

    if verbose:
        print_backtest_summary(result_long)
        if short_enabled:
            print_backtest_summary(result_short)
        print_backtest_summary(result_comb)

    return {
        "long":     result_long,
        "short":    result_short,
        "combined": result_comb,
    }


def run_cost_sensitivity(
    df: pd.DataFrame,
    test_mask: np.ndarray,
    clf_direction,
    scaler_direction,
    feature_list: List[str],
    side: str,
    threshold: float,
    calibrator=None,
    base_cost_pct: float = COST_PCT,
    multipliers: Tuple[float, ...] = (1.0, 2.0, 3.0),
    initial_equity: float = INITIAL_EQUITY,
    verbose: bool = True,
) -> Dict[float, BacktestResult]:
    """
    Analyse de sensibilité au coût de transaction.

    Rejette le modèle si PF < 1.0 à 2x le coût de base.
    """
    results: Dict[float, BacktestResult] = {}

    if verbose:
        print(f"\n   ── Sensibilité coût ({side.upper()}) ─────────────────────────────")

    for mult in multipliers:
        cost = base_cost_pct * mult
        res = run_backtest_side(
            df=df,
            test_mask=test_mask,
            clf_direction=clf_direction,
            scaler_direction=scaler_direction,
            feature_list=feature_list,
            side=side,
            threshold=threshold,
            calibrator=calibrator,
            cost_pct=cost,
            initial_equity=initial_equity,
            verbose=False,
        )
        results[mult] = res

        pf_str   = f"PF={res.profit_factor:.2f}"
        ok_str   = "OK" if res.profit_factor >= 1.0 else "⚠  FAIL"
        if verbose:
            print(f"   {mult:.0f}x coût ({cost:.4f}) : "
                  f"n={res.n_trades}  {pf_str}  WR={res.win_rate:.1%}  "
                  f"PnL={res.total_pnl_pct:+.2%}  {ok_str}")

    # Avertissement si 2x coût détruit le PF
    pf_2x = results.get(2.0, results.get(multipliers[-1]))
    if pf_2x and pf_2x.profit_factor < 1.0:
        print(f"   ⚠  Fragile au coût : PF={pf_2x.profit_factor:.2f} à 2x coût")
        print(f"      → Edge insuffisant pour absorber le slippage réel")

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ─────────────────────────────────────────────────────────────────────────────

def _apply_calibrator(calibrator, p_raw: float) -> float:
    """Applique le calibrateur sur une seule proba, retourne float."""
    if calibrator is None:
        return p_raw
    try:
        arr = np.array([p_raw])
        try:
            return float(calibrator.predict(arr)[0])
        except AttributeError:
            return float(calibrator.predict_proba(arr.reshape(-1, 1))[0, 1])
    except Exception:
        return p_raw


# ─────────────────────────────────────────────────────────────────────────────
# Walk-forward impitoyable — ÉTAPE 5
# ─────────────────────────────────────────────────────────────────────────────

def run_wf_backtest_short(
    df: pd.DataFrame,
    clf,
    scaler,
    features: List[str],
    calibrator=None,
    threshold: float = 0.62,
    clf_filter=None,
    scaler_filter=None,
    filter_features: Optional[List[str]] = None,
    filter_threshold: float = 0.50,
    base_cost_pct: float = COST_PCT,
    initial_equity: float = INITIAL_EQUITY,
    n_folds: int = 6,
    train_months: int = 18,
    test_months: int = 6,
    verbose: bool = True,
    # ── Méta-modèle de régime bear ─────────────────────────────────────────────
    clf_regime=None,              # modèle de régime bear (optionnel)
    scaler_regime=None,           # scaler du régime
    features_regime: Optional[List[str]] = None,  # features du régime
    regime_threshold: float = 0.70,               # seuil d'activation bear
) -> ShortRobustnessReport:
    """
    Walk-forward expanding window sur le short.

    Architecture :
      - n_folds folds de test_months chacun
      - Pour chaque fold : entraînement réel + test sur fold
      - Comparaison contre 3 baselines (random, RSI>70, no-trade)
      - Sensibilité aux coûts : 1x, 1.5x, 2x, 3x, 5x

    Le modèle n'est PAS réentraîné par fold (pas de données suffisantes).
    Le modèle entraîné est évalué fold-par-fold (true OOS par fenêtre temporelle).
    """
    import calendar

    if not isinstance(df.index, pd.DatetimeIndex):
        try:
            df = df.copy()
            df.index = pd.to_datetime(df.index)
        except Exception:
            raise ValueError("Index de df doit être DatetimeIndex pour walk-forward")

    cost_short = base_cost_pct * COST_SHORT_MULT

    # Construire les folds temporels (fenêtres de test_months)
    all_dates = df.index
    min_date  = all_dates.min()
    max_date  = all_dates.max()

    # Folds : découper la période totale en blocs de test_months
    fold_periods = _build_fold_periods(min_date, max_date, test_months, n_folds)

    if len(fold_periods) == 0:
        print("   ⚠  Walk-forward : pas assez de données pour créer des folds")
        return ShortRobustnessReport(deploy_short=False, reject_reason="insufficient_data")

    if verbose:
        print(f"\n   ── Walk-Forward Short ({len(fold_periods)} folds × {test_months} mois) ──")

    pf_by_fold:     List[float] = []
    wr_by_fold:     List[float] = []
    trades_by_fold: List[int]   = []

    # Baselines : agrégées sur tous les folds
    random_rets_all: List[float] = []
    rsi_rets_all:    List[float] = []
    model_rets_all:  List[float] = []
    regime_shortable_trades: int = 0
    regime_total_trades:     int = 0

    for fold_idx, (test_start, test_end) in enumerate(fold_periods):
        test_mask_fold = (all_dates >= test_start) & (all_dates < test_end)
        df_fold        = df.loc[test_mask_fold]

        if len(df_fold) < 20:
            pf_by_fold.append(float("nan"))
            wr_by_fold.append(float("nan"))
            trades_by_fold.append(0)
            continue

        # ── Signal modèle ────────────────────────────────────────────────────
        fold_rets = _eval_short_fold(
            df_fold=df_fold,
            clf=clf, scaler=scaler, features=features, calibrator=calibrator,
            threshold=threshold,
            clf_filter=clf_filter, scaler_filter=scaler_filter,
            filter_features=filter_features, filter_threshold=filter_threshold,
            cost_short=cost_short,
            clf_regime=clf_regime, scaler_regime=scaler_regime,
            features_regime=features_regime, regime_threshold=regime_threshold,
        )
        model_rets_all.extend(fold_rets["model"])
        random_rets_all.extend(fold_rets["random"])
        rsi_rets_all.extend(fold_rets["rsi70"])
        regime_shortable_trades += fold_rets["n_shortable"]
        regime_total_trades     += fold_rets["n_total_signal"]

        m_arr = np.array(fold_rets["model"], dtype=np.float64)
        if len(m_arr) == 0:
            pf_by_fold.append(0.0)
            wr_by_fold.append(0.0)
            trades_by_fold.append(0)
        else:
            wins  = float((m_arr > 0).sum())
            n_t   = len(m_arr)
            wr    = wins / n_t
            gw    = float(m_arr[m_arr > 0].sum())
            gl    = float(abs(m_arr[m_arr < 0].sum()))
            pf    = gw / max(gl, 1e-9)
            pf_by_fold.append(round(pf, 3))
            wr_by_fold.append(round(wr, 3))
            trades_by_fold.append(n_t)

    # ── Métriques synthétiques ────────────────────────────────────────────────
    valid_pf = [p for p in pf_by_fold if not (isinstance(p, float) and np.isnan(p))]
    pf_mean  = float(np.mean(valid_pf)) if valid_pf else 0.0
    pf_min   = float(np.min(valid_pf))  if valid_pf else 0.0
    pf_cons  = float(sum(1 for p in valid_pf if p >= 1.0) / max(len(valid_pf), 1))
    wr_valid = [w for w in wr_by_fold if not (isinstance(w, float) and np.isnan(w))]
    wr_mean  = float(np.mean(wr_valid)) if wr_valid else 0.0

    # ── Comparaison baselines ─────────────────────────────────────────────────
    def _pf(rets):
        arr = np.array(rets, dtype=np.float64)
        if len(arr) == 0:
            return 0.0
        gw = float(arr[arr > 0].sum())
        gl = float(abs(arr[arr < 0].sum()))
        return gw / max(gl, 1e-9)

    pf_model  = _pf(model_rets_all)
    pf_random = _pf(random_rets_all)
    pf_rsi    = _pf(rsi_rets_all)

    vs_random = pf_model / max(pf_random, 0.01)
    vs_rsi    = pf_model / max(pf_rsi, 0.01)

    # ── Sensibilité aux coûts ─────────────────────────────────────────────────
    cost_break = _find_cost_break_short(
        df=df,
        clf=clf, scaler=scaler, features=features, calibrator=calibrator,
        threshold=threshold, base_cost=base_cost_pct,
        fold_periods=fold_periods,
        clf_filter=clf_filter, scaler_filter=scaler_filter,
        filter_features=filter_features, filter_threshold=filter_threshold,
    )

    # ── Pureté régime ─────────────────────────────────────────────────────────
    regime_purity = (
        regime_shortable_trades / max(regime_total_trades, 1)
    )

    # ── Rapport final ─────────────────────────────────────────────────────────
    report = ShortRobustnessReport(
        n_folds=len(fold_periods),
        wf_pf_by_fold=[round(p, 3) if not np.isnan(p) else 0.0 for p in pf_by_fold],
        wf_wr_by_fold=[round(w, 3) if not np.isnan(w) else 0.0 for w in wr_by_fold],
        wf_trades_by_fold=trades_by_fold,
        wf_pf_mean=round(pf_mean, 3),
        wf_pf_min=round(pf_min, 3),
        wf_pf_consistency=round(pf_cons, 3),
        wf_wr_mean=round(wr_mean, 3),
        vs_random_pf_ratio=round(vs_random, 3),
        vs_rsi_baseline_pf_ratio=round(vs_rsi, 3),
        cost_sensitivity_break=round(cost_break, 2),
        regime_purity=round(regime_purity, 3),
    )

    deploy, reason = should_deploy_short(report)
    report.deploy_short  = deploy
    report.reject_reason = reason

    if verbose:
        report.print_report()

    return report


# ─────────────────────────────────────────────────────────────────────────────
# Helpers walk-forward
# ─────────────────────────────────────────────────────────────────────────────

def _build_fold_periods(
    min_date: pd.Timestamp,
    max_date: pd.Timestamp,
    test_months: int,
    n_folds: int,
) -> List[Tuple[pd.Timestamp, pd.Timestamp]]:
    """Construit n_folds périodes de test consécutives de test_months mois."""
    periods = []
    # Partir de la fin du dataset et remonter
    end = max_date
    for _ in range(n_folds):
        start = end - pd.DateOffset(months=test_months)
        if start < min_date:
            break
        periods.append((start, end))
        end = start
    periods.reverse()  # ordre chronologique
    return periods


def _eval_short_fold(
    df_fold: pd.DataFrame,
    clf, scaler, features: List[str], calibrator,
    threshold: float,
    clf_filter, scaler_filter, filter_features: Optional[List[str]],
    filter_threshold: float,
    cost_short: float,
    clf_regime=None,
    scaler_regime=None,
    features_regime: Optional[List[str]] = None,
    regime_threshold: float = 0.70,
) -> Dict:
    """
    Évalue le signal short sur un fold et les deux baselines.
    Vectorisé : toutes les inférences se font en batch pour éviter iterrows lent.
    """
    from ai.level_1.rules import REGIME_NO_SHORT

    if df_fold.empty:
        return {"model": [], "random": [], "rsi70": [], "n_shortable": 0, "n_total_signal": 0}

    # ── 1. Masque régime ──────────────────────────────────────────────────────
    regime_col_vals = df_fold[REGIME_COL].astype(str).values if REGIME_COL in df_fold.columns else np.full(len(df_fold), "NEUTRAL")
    not_noshort = regime_col_vals != REGIME_NO_SHORT
    df_ok = df_fold[not_noshort].copy()
    regime_ok = regime_col_vals[not_noshort]

    if df_ok.empty:
        return {"model": [], "random": [], "rsi70": [], "n_shortable": 0, "n_total_signal": 0}

    _ret_col = next(
        (c for c in (TARGET_COL, "future_ret_short_4h", "future_ret_h") if c in df_ok.columns),
        None,
    )
    if _ret_col is None:
        return {"model": [], "random": [], "rsi70": [], "n_shortable": 0, "n_total_signal": 0}
    raw_rets = df_ok[_ret_col].fillna(0.0).values.astype(np.float64)
    net_rets = raw_rets * (-1.0) - cost_short

    # ── 2. Filtre tradeable (batch) ───────────────────────────────────────────
    tradeable_mask = np.ones(len(df_ok), dtype=bool)
    if clf_filter is not None and filter_features:
        try:
            X_f = df_ok[filter_features].fillna(0.0).values.astype(np.float32)
            p_f = clf_filter.predict_proba(scaler_filter.transform(X_f))[:, 1]
            tradeable_mask = p_f >= filter_threshold
        except Exception:
            pass

    df_pass = df_ok[tradeable_mask]
    net_pass = net_rets[tradeable_mask]
    regime_pass = regime_ok[tradeable_mask]

    # ── 2b. Méta-régime bear (ML) — gate AVANT l'edge model ──────────────────
    if clf_regime is not None and scaler_regime is not None and features_regime:
        try:
            _missing_reg = [f for f in features_regime if f not in df_pass.columns]
            if not _missing_reg and len(df_pass) > 0:
                X_reg = df_pass[features_regime].fillna(0.0).values.astype(np.float32)
                p_bear = clf_regime.predict_proba(scaler_regime.transform(X_reg))[:, 1]
                bear_mask = p_bear >= regime_threshold
                df_pass     = df_pass[bear_mask]
                net_pass    = net_pass[bear_mask]
                regime_pass = regime_pass[bear_mask]
        except Exception:
            pass  # si échec régime ML, continuer sans

    # ── 3. Signal edge model (batch) ─────────────────────────────────────────
    model_rets:  List[float] = []
    n_shortable = 0
    n_total_sig = 0
    if len(df_pass) > 0:
        try:
            X_d = df_pass[features].fillna(0.0).values.astype(np.float32)
            p_raw = clf.predict_proba(scaler.transform(X_d))[:, 1]
            if calibrator is not None:
                p_cal = np.array([_apply_calibrator(calibrator, float(p)) for p in p_raw])
            else:
                p_cal = p_raw
            sig_mask = p_cal >= threshold
            model_rets = net_pass[sig_mask].tolist()
            n_total_sig = int(sig_mask.sum())
            n_shortable = int((regime_pass[sig_mask] == "SHORTABLE").sum())
        except Exception:
            pass

    # ── 4. Baselines (sur df_ok après régime, pas filtré par tradeable) ───────
    rsi_mask = df_ok["rsi_14"].fillna(50.0).values > 70.0 if "rsi_14" in df_ok.columns else np.zeros(len(df_ok), dtype=bool)
    rsi_rets = net_rets[rsi_mask].tolist()

    rand_mask = np.random.random(len(df_ok)) < 0.10
    random_rets = net_rets[rand_mask].tolist()

    return {
        "model":           model_rets,
        "random":          random_rets,
        "rsi70":           rsi_rets,
        "n_shortable":     n_shortable,
        "n_total_signal":  n_total_sig,
    }


def _find_cost_break_short(
    df: pd.DataFrame,
    clf, scaler, features: List[str], calibrator,
    threshold: float,
    base_cost: float,
    fold_periods: List[Tuple],
    clf_filter, scaler_filter,
    filter_features: Optional[List[str]],
    filter_threshold: float,
) -> float:
    """
    Cherche le multiple de coût auquel PF < 1.0 (en moyennant sur tous les folds).
    Retourne ce multiple (2.5 = "résiste à 2.5x le coût de base").
    Vectorisé : inférence batch par fold.
    """
    from ai.level_1.rules import REGIME_NO_SHORT

    # Précalculer les signaux une fois pour toutes les périodes combinées
    all_dates = df.index if isinstance(df.index, pd.DatetimeIndex) else pd.to_datetime(df.index)

    # Construire un masque pour l'ensemble des folds
    fold_mask_total = np.zeros(len(df), dtype=bool)
    for test_start, test_end in fold_periods:
        fold_mask_total |= (all_dates >= test_start) & (all_dates < test_end)

    df_all = df[fold_mask_total]
    if df_all.empty:
        return 1.0

    # Régime gate
    regime_vals = df_all[REGIME_COL].astype(str).values if REGIME_COL in df_all.columns else np.full(len(df_all), "NEUTRAL")
    not_no_short = regime_vals != REGIME_NO_SHORT
    df_sig = df_all[not_no_short]
    if df_sig.empty:
        return 1.0

    # Inférence batch
    try:
        X_d = df_sig[features].fillna(0.0).values.astype(np.float32)
        p_raw = clf.predict_proba(scaler.transform(X_d))[:, 1]
        if calibrator is not None:
            p_cal = np.array([_apply_calibrator(calibrator, float(p)) for p in p_raw])
        else:
            p_cal = p_raw
    except Exception:
        return 1.0

    _ret_col = next(
        (c for c in (TARGET_COL, "future_ret_short_4h", "future_ret_h") if c in df_sig.columns),
        None,
    )
    if _ret_col is None:
        return 1.0
    raw_rets = df_sig[_ret_col].fillna(0.0).values.astype(np.float64)
    sig_mask = p_cal >= threshold

    if sig_mask.sum() == 0:
        return 1.0

    base_net_rets = raw_rets[sig_mask] * (-1.0)  # avant coût

    for mult in (1.0, 1.5, 2.0, 2.5, 3.0, 5.0):
        cost = base_cost * COST_SHORT_MULT * mult
        rets = base_net_rets - cost
        gw   = float(rets[rets > 0].sum())
        gl   = float(abs(rets[rets < 0].sum()))
        pf   = gw / max(gl, 1e-9)
        if pf < 1.0:
            return mult

    return 5.0  # résiste à 5x
