"""announcement_time_extractor / parser: pure text -> timestamps, and nothing invented."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors import announcement_body_parser as P  # noqa: E402
from data_lake.collectors import announcement_time_extractor as X  # noqa: E402


def test_cms_node_tree_flattens_to_text_with_entities_resolved():
    tree = {"node": "root", "child": [
        {"node": "element", "tag": "p", "child": [{"node": "text", "text": "&nbsp;Fellow Binancians,"}]},
        {"node": "element", "tag": "p", "child": [{"node": "text", "text": "Trading opens at 2025-05-02 08:30 (UTC)."}]}]}
    t = P.flatten_cms_body(tree)
    assert "Fellow Binancians," in t and "&nbsp;" not in t and "2025-05-02 08:30" in t
    assert P.flatten_cms_body(json.dumps(tree)) == t                      # accepte l'arbre ou sa chaine JSON
    assert P.flatten_cms_body("") == "" and P.flatten_cms_body("<p>plain <b>html</b></p>") == "plain html"


def test_html_is_stripped_and_blocks_become_lines():
    assert P.clean_html("<div>a</div><div>b</div>") == "a\nb"
    assert "script" not in P.clean_html("<script>var x=1</script><p>ok</p>")
    assert P.clean_html("<p>a&amp;b</p>") == "a&b"


def test_iso_and_textual_dates_both_found_and_assumed_utc():
    d = X.find_datetimes("opens at 2025-05-02 08:30 (UTC) and again on May 3, 2025 09:00 UTC")
    isos = [x[2] for x in d]
    assert "2025-05-02T08:30:00+00:00" in isos and "2025-05-03T09:00:00+00:00" in isos
    off = X.find_datetimes("delisted at 2025-05-02 08:30 (UTC+8)")
    assert off[0][2] == "2025-05-02T00:30:00+00:00"                       # decalage explicite applique
    assert X.find_datetimes("no date here") == []


def test_extract_assigns_a_date_only_to_the_clause_that_names_it():
    txt = ("Binance Futures will launch the ABCUSDT Perpetual Contract.\n"
           "2025-05-02 08:30 (UTC): ABCUSDT Perpetual Contract with up to 20x leverage\n"
           "Spot trading for XYZ/USDT will be delisted at 2025-06-01 03:00 (UTC).")
    e = X.extract(txt)
    assert e["trading_start_ts"] == "2025-05-02T08:30:00+00:00" and e["delisting_ts"] == "2025-06-01T03:00:00+00:00"
    assert e["suspension_ts"] is None                                      # rien n'est invente
    assert "ABCUSDT" in e["symbols"] and "XYZUSDT" in e["symbols"]
    assert e["has_explicit_time"]["trading_start_ts"] is True


def test_a_date_without_a_matching_clause_is_not_used():
    e = X.extract("Published on 2025-05-02. Fellow Binancians, thank you.")
    assert e["trading_start_ts"] is None and e["delisting_ts"] is None and e["n_datetimes_found"] == 1


def test_suspension_and_symbol_filters():
    e = X.extract("Deposits will be suspended at 2025-06-01 02:00 (UTC) for USDT pairs.")
    assert e["suspension_ts"] == "2025-06-01T02:00:00+00:00"
    assert "USDTUSDT" not in e["symbols"]                                   # base == quote : jamais un symbole
    assert X.extract("")["symbols"] == []


def test_ms_to_iso_handles_ms_and_microseconds():
    assert P.ms_to_iso(1746174600000) == "2025-05-02T08:30:00+00:00"
    assert P.ms_to_iso(1746174600000000) == "2025-05-02T08:30:00+00:00"
    assert P.ms_to_iso(None) is None
