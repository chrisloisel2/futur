"""
src/institutional/data/news_collector/lexicon.py
─────────────────────────────────────────────────────────────────────────────
Lexique de sentiment crypto (pur Python, sans dépendance — disque contraint) +
tagging texte → symboles de l'univers. Score = somme pondérée des termes /
√(tokens), borné [-1, 1], avec négation locale (fenêtre 3 mots).
"""
from __future__ import annotations

import math
import re
from typing import Dict, List, Tuple

# ── sentiment pondéré (crypto-spécifique) ────────────────────────────────────
POS = {
    "surge": 2, "surges": 2, "soar": 2, "soars": 2, "rally": 2, "rallies": 2,
    "bullish": 2, "breakout": 2, "record": 1, "ath": 2, "all-time": 2,
    "adoption": 2, "partnership": 2, "partners": 1, "upgrade": 1, "approval": 3,
    "approved": 3, "etf": 1, "institutional": 2, "inflows": 2, "inflow": 2,
    "accumulate": 1, "accumulation": 1, "gains": 1, "jumps": 2, "jump": 1,
    "soaring": 2, "boom": 2, "outperform": 2, "milestone": 1, "launch": 1,
    "integrat": 1, "buyback": 2, "halving": 1, "moon": 2, "green": 1,
    "recover": 1, "recovery": 1, "rebound": 2, "optimism": 2, "confidence": 1,
    "backing": 1, "surged": 2, "climbs": 1, "climb": 1, "rise": 1, "rises": 1,
    "support": 1, "greenlight": 3, "unlock": 1, "staking": 1, "yield": 1,
}
NEG = {
    "crash": 3, "crashes": 3, "plunge": 3, "plunges": 3, "plummet": 3,
    "bearish": 2, "hack": 3, "hacked": 3, "exploit": 3, "exploited": 3,
    "dump": 2, "dumps": 2, "selloff": 2, "sell-off": 2, "lawsuit": 2, "sue": 2,
    "sec": 1, "ban": 2, "banned": 2, "liquidation": 2, "liquidated": 3,
    "collapse": 3, "collapses": 3, "rug": 3, "rugpull": 3, "delist": 3,
    "delisted": 3, "fud": 1, "fraud": 3, "scam": 3, "outflows": 2, "outflow": 2,
    "downturn": 2, "slump": 2, "tumble": 2, "tumbles": 2, "warning": 1,
    "warn": 1, "risk": 1, "fear": 1, "panic": 3, "capitulation": 3, "red": 1,
    "drop": 1, "drops": 1, "falls": 1, "fall": 1, "decline": 1, "loss": 1,
    "losses": 1, "bankruptcy": 3, "bankrupt": 3, "halt": 2, "halted": 2,
    "investigation": 2, "probe": 2, "arrest": 3, "charges": 2, "default": 3,
    "breach": 3, "stolen": 3, "freeze": 2, "frozen": 2, "concern": 1,
}
NEGATORS = {"no", "not", "never", "without", "denies", "denied", "avoid", "fails", "fail"}

# ── tagging symbole ← mots-clés (univers 50) ─────────────────────────────────
_SYMBOL_KEYWORDS: Dict[str, List[str]] = {
    "BTCUSDT": ["bitcoin", "btc"], "ETHUSDT": ["ethereum", "eth", "ether"],
    "SOLUSDT": ["solana", "sol"], "BNBUSDT": ["binance coin", "bnb"],
    "XRPUSDT": ["ripple", "xrp"], "DOGEUSDT": ["dogecoin", "doge"],
    "ADAUSDT": ["cardano", "ada"], "AVAXUSDT": ["avalanche", "avax"],
    "LINKUSDT": ["chainlink", "link"], "LTCUSDT": ["litecoin", "ltc"],
    "BCHUSDT": ["bitcoin cash", "bch"], "DOTUSDT": ["polkadot"],
    "NEARUSDT": ["near protocol"], "APTUSDT": ["aptos", "apt"],
    "SUIUSDT": ["sui network", "sui"], "ARBUSDT": ["arbitrum", "arb"],
    "OPUSDT": ["optimism"], "INJUSDT": ["injective", "inj"],
    "ATOMUSDT": ["cosmos", "atom"], "FILUSDT": ["filecoin", "fil"],
    "TRXUSDT": ["tron", "trx"], "ETCUSDT": ["ethereum classic", "etc"],
    "UNIUSDT": ["uniswap", "uni"], "AAVEUSDT": ["aave"],
    "MKRUSDT": ["maker dao", "makerdao"], "FETUSDT": ["fetch.ai", "fetch ai"],
    "TAOUSDT": ["bittensor", "tao"], "SEIUSDT": ["sei network"],
    "TIAUSDT": ["celestia", "tia"], "WIFUSDT": ["dogwifhat", "wif"],
    "PEPEUSDT": ["pepe"], "ORDIUSDT": ["ordinals", "ordi"],
    "STXUSDT": ["stacks"], "IMXUSDT": ["immutable"],
    "GRTUSDT": ["the graph"], "RUNEUSDT": ["thorchain", "rune"],
    "JUPUSDT": ["jupiter"], "PYTHUSDT": ["pyth network"],
    "ENAUSDT": ["ethena", "ena"], "PENDLEUSDT": ["pendle"],
    "LDOUSDT": ["lido"], "WLDUSDT": ["worldcoin"],
    "ALGOUSDT": ["algorand", "algo"], "ICPUSDT": ["internet computer"],
    "HBARUSDT": ["hedera", "hbar"], "VETUSDT": ["vechain", "vet"],
    "SANDUSDT": ["the sandbox"], "MANAUSDT": ["decentraland", "mana"],
    "ARUSDT": ["arweave"],
}
# mots-clés multi-mots d'abord (matching plus spécifique)
_KW_SORTED: List[Tuple[str, str]] = sorted(
    [(kw, sym) for sym, kws in _SYMBOL_KEYWORDS.items() for kw in kws],
    key=lambda x: -len(x[0]))

_WORD = re.compile(r"[a-z0-9\.\-]+")


def tag_symbols(text: str) -> List[str]:
    """Symboles cités (mot entier ; 'sol' ne matche pas 'solar')."""
    t = " " + text.lower() + " "
    found = []
    for kw, sym in _KW_SORTED:
        if sym in found:
            continue
        pat = r"[^a-z0-9]" + re.escape(kw) + r"[^a-z0-9]"
        if re.search(pat, t):
            found.append(sym)
    return found


def score_sentiment(text: str) -> float:
    toks = _WORD.findall(text.lower())
    if not toks:
        return 0.0
    s = 0.0
    for i, w in enumerate(toks):
        val = POS.get(w, 0) - NEG.get(w, 0)
        # préfixe partiel (ex. "integrat" → "integration")
        if val == 0:
            for k, v in POS.items():
                if len(k) >= 5 and w.startswith(k):
                    val = v; break
            if val == 0:
                for k, v in NEG.items():
                    if len(k) >= 5 and w.startswith(k):
                        val = -v; break
        if val != 0:
            if any(toks[j] in NEGATORS for j in range(max(0, i - 3), i)):
                val = -val * 0.7
            s += val
    return max(-1.0, min(1.0, s / math.sqrt(len(toks))))
