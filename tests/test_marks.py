"""
tests/test_marks.py
─────────────────────────────────────────────────────────────────────────────
Régression pour le bug root-cause de la divergence P1_EQUAL_RISK vs
P1_CONTROL (phase CLOSE THE EXECUTION LOOP, item P0.1) :

_from_derivatives_raw prenait "les 4 derniers fichiers" du glob trié en
supposant 1 fichier ~= 1 jour. Le vrai collecteur écrit ~450-500 fichiers
(1 ligne chacun) PAR JOUR PAR SYMBOLE. Donc "les 4 derniers fichiers" ne
couvrait que les ~20 dernières minutes réelles, peu importe as_of : tout
as_of plus vieux que ça retournait silencieusement None -> fallback REST
bookTicker LIVE (qui ignore as_of pour le choix du prix -- il retourne le
prix "maintenant", pas le prix historique). Deux appels a as_of identique
mais espacés de quelques secondes/minutes en wall-clock (ex: step()
P1_EQUAL_RISK puis step() P1_CONTROL dans le même run) pouvaient alors
recevoir des prix REST différents pour le "même" as_of -- non-déterminisme
qui a propagé jusqu'à des quantités, PnL non-réalisé et PnL réalisé
différents entre deux portefeuilles avec une config et des intents
strictement identiques.

Ces tests répliquent le VRAI pattern d'écriture du collecteur (many
single-row files per date= partition) pour reproduire le bug, puis
vérifient le fix.
"""
from __future__ import annotations

import importlib

import pandas as pd
import pytest


def _write_oi_file(base, date_str: str, seq: int, ts_ms: int, mark_price: float,
                   open_interest: float = 1_000_000.0) -> None:
    d = base / f"date={date_str}"
    d.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame({"timestamp": [ts_ms], "mark_price": [mark_price],
                       "open_interest": [open_interest]})
    df.to_parquet(d / f"part-{seq:06d}.parquet")


def _ms(ts: str) -> int:
    return int(pd.Timestamp(ts).value // 1_000_000)


@pytest.fixture()
def marks_module(tmp_path, monkeypatch):
    import src.institutional.live_alpha_lab.marks as marks
    importlib.reload(marks)
    monkeypatch.setattr(marks, "DERIVATIVES_RAW", tmp_path / "derivatives_raw")
    marks._rest_cache.clear()
    yield marks
    marks._rest_cache.clear()


def _oi_base(marks_module, symbol: str):
    return (marks_module.DERIVATIVES_RAW / "exchange=binance" / "market=usdm" /
            "stream=open_interest" / f"symbol={symbol}")


def test_finds_historical_mark_when_hundreds_of_recent_files_exist_after_it(marks_module):
    """Réplique exactement le bug réel : beaucoup de fichiers récents (today),
    et on interroge un as_of plus ancien qui existe dans une partition
    antérieure. L'ancienne logique "last 4 files" aurait pioché uniquement
    dans les fichiers les plus récents (today) et jamais vu ce prix."""
    base = _oi_base(marks_module, "JUPUSDT")
    old_as_of = pd.Timestamp("2026-09-01T00:51:46.101525+00:00")
    _write_oi_file(base, "2026-09-01", 0, _ms("2026-09-01T00:51:24.090"), 0.22475)
    # 300 fichiers "aujourd'hui" (simule le collecteur ~5min pendant la journée),
    # tous POSTERIEURS à old_as_of -- comme dans le run réel qui a divergé.
    for i in range(1, 301):
        ts = pd.Timestamp("2026-09-01T00:51:24.090") + pd.Timedelta(minutes=i)
        _write_oi_file(base, "2026-09-01", i, _ms(ts.isoformat()), 0.2 + i * 0.0001)

    q = marks_module._from_derivatives_raw("JUPUSDT", old_as_of)
    assert q is not None, "doit trouver le mark historique, pas retourner None"
    assert q.mark_source == "DERIVATIVES_RAW_MARK"
    assert q.price == pytest.approx(0.22475)


def test_get_mark_deterministic_across_repeated_calls_for_same_historical_as_of(marks_module):
    base = _oi_base(marks_module, "TIAUSDT")
    as_of = pd.Timestamp("2026-09-01T00:51:46+00:00")
    _write_oi_file(base, "2026-09-01", 0, _ms("2026-09-01T00:40:00"), 0.34)
    for i in range(1, 250):
        ts = pd.Timestamp("2026-09-01T00:40:00") + pd.Timedelta(minutes=i)
        _write_oi_file(base, "2026-09-01", i, _ms(ts.isoformat()), 0.34 + i * 0.00001)

    results = [marks_module.get_mark("TIAUSDT", as_of) for _ in range(5)]
    assert all(r == results[0] for r in results), "get_mark doit être pur en (instrument, as_of)"
    assert results[0].mark_source == "DERIVATIVES_RAW_MARK"


def test_never_reads_future_partition(marks_module):
    """as_of tombe le 08-31 ; une partition 09-01 (future relative à as_of)
    existe déjà (écriture en cours) -- ne doit jamais l'utiliser."""
    base = _oi_base(marks_module, "BTCUSDT")
    as_of = pd.Timestamp("2026-08-31T23:50:00+00:00")
    _write_oi_file(base, "2026-08-31", 0, _ms("2026-08-31T23:45:00"), 60000.0)
    _write_oi_file(base, "2026-09-01", 0, _ms("2026-09-01T00:05:00"), 61000.0)  # futur relatif à as_of

    q = marks_module._from_derivatives_raw("BTCUSDT", as_of)
    assert q is not None
    assert q.price == pytest.approx(60000.0)
    assert q.mark_timestamp <= as_of


def test_early_in_day_falls_back_to_previous_day_partition(marks_module):
    """as_of tôt dans sa journée (avant le premier poll du jour) : le dernier
    mark <= as_of est encore dans la partition de la veille."""
    base = _oi_base(marks_module, "ETHUSDT")
    as_of = pd.Timestamp("2026-09-01T00:02:00+00:00")
    _write_oi_file(base, "2026-08-31", 0, _ms("2026-08-31T23:58:00"), 3000.0)
    _write_oi_file(base, "2026-09-01", 0, _ms("2026-09-01T00:05:00"), 3010.0)  # après as_of

    q = marks_module._from_derivatives_raw("ETHUSDT", as_of)
    assert q is not None
    assert q.price == pytest.approx(3000.0)


def test_no_data_at_all_returns_none_not_hallucinated_price(marks_module):
    q = marks_module._from_derivatives_raw("NOSUCHSYMBOLUSDT", pd.Timestamp("2026-09-01T00:00:00+00:00"))
    assert q is None


def test_get_mark_falls_back_to_rest_only_when_genuinely_no_historical_data(marks_module, monkeypatch):
    calls = {}

    def fake_rest(symbol, as_of):
        calls["hit"] = (symbol, as_of)
        return marks_module.MarkQuote(
            instrument=symbol, price=1.23, mark_source="REST_BOOKTICKER_MID",
            mark_timestamp=as_of, mark_age_ms=0.0,
        )

    monkeypatch.setattr(marks_module, "_from_rest_bookticker", fake_rest)
    as_of = pd.Timestamp("2026-09-01T00:00:00+00:00")
    q = marks_module.get_mark("NEWLISTEDUSDT", as_of)
    assert calls.get("hit") == ("NEWLISTEDUSDT", as_of)
    assert q.mark_source == "REST_BOOKTICKER_MID"


# ── P1 (phase OPERATIONAL HARDENING) : REST_BOOKTICKER_MID ne doit JAMAIS
# répondre "maintenant" à une question historique -- régression directe du
# bug root-cause P1_EQUAL_RISK/P1_CONTROL (commit ed17708) ─────────────────

def test_rest_bookticker_refuses_historical_as_of_never_answers_with_live_price(marks_module, monkeypatch):
    """Un as_of largement dans le passé ne doit JAMAIS déclencher un appel
    REST réel (qui répondrait avec le prix ACTUEL, pas historique)."""
    called = {"n": 0}

    def fake_urlopen(*a, **k):
        called["n"] += 1
        raise AssertionError("REST ne doit jamais être appelé pour un as_of historique")

    monkeypatch.setattr(marks_module.urllib.request, "urlopen", fake_urlopen)
    old_as_of = pd.Timestamp("2026-01-01T00:00:00+00:00")   # bien avant "maintenant"
    q = marks_module._from_rest_bookticker("SOMESYMBOLUSDT", old_as_of)
    assert q is None
    assert called["n"] == 0


def test_rest_bookticker_answers_for_as_of_close_to_now(marks_module, monkeypatch):
    """as_of proche de maintenant (dans la tolérance de causalité) doit
    toujours pouvoir répondre -- le refus est spécifique à l'historique,
    pas un refus général."""
    def fake_urlopen(req, timeout=5):
        class _Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"bidPrice": "100.0", "askPrice": "100.2"}'
        return _Resp()

    monkeypatch.setattr(marks_module.urllib.request, "urlopen", fake_urlopen)
    near_now = pd.Timestamp.now(tz="UTC") - pd.Timedelta(seconds=5)
    q = marks_module._from_rest_bookticker("SOMESYMBOLUSDT", near_now)
    assert q is not None
    assert q.mark_source == "REST_BOOKTICKER_MID"
    assert q.price == pytest.approx(100.1)


def test_get_mark_returns_none_not_a_hallucinated_live_price_for_old_as_of_non_frozen50_symbol(marks_module):
    """Bout-en-bout : un symbole hors frozen-50 (pas de derivatives_raw)
    interrogé à un as_of ancien doit retourner None -- jamais silencieusement
    le prix REST courant maquillé en réponse historique."""
    old_as_of = pd.Timestamp("2026-01-01T00:00:00+00:00")
    q = marks_module.get_mark("SOMESYMBOLNOTINFROZEN50USDT", old_as_of)
    assert q is None
