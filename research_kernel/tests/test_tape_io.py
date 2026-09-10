"""tape_io must read a live partition: an unterminated gzip member, and concatenated
members after a rotation. A reader that returns nothing on a live file makes every
downstream test pass on an empty tape — which is exactly the failure to forbid."""
import gzip
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from data_lake.collectors.tape_io import iter_lines, read_tape  # noqa: E402


def _member(lines, terminated=True):
    raw = io.BytesIO()
    g = gzip.GzipFile(fileobj=raw, mode="wb")
    for l in lines:
        g.write((json.dumps(l) + "\n").encode())
    if terminated:
        g.close()
    else:
        g.flush()                       # Z_SYNC_FLUSH : pas de trailer, comme le collecteur en vol
    return raw.getvalue()


def test_reads_unterminated_member(tmp_path):
    p = tmp_path / "events-11.jsonl.gz"
    p.write_bytes(_member([{"i": 1}, {"i": 2}, {"i": 3}], terminated=False))
    assert [json.loads(x)["i"] for x in iter_lines(p)] == [1, 2, 3]


def test_reads_concatenated_members_then_live_tail(tmp_path):
    p = tmp_path / "events-12.jsonl.gz"
    p.write_bytes(_member([{"i": 1}]) + _member([{"i": 2}]) + _member([{"i": 3}], terminated=False))
    assert [json.loads(x)["i"] for x in iter_lines(p)] == [1, 2, 3]


def test_never_returns_a_cut_line(tmp_path):
    p = tmp_path / "events-13.jsonl.gz"
    raw = io.BytesIO(); g = gzip.GzipFile(fileobj=raw, mode="wb"); g.write(b'{"i": 1}\n{"i": 2'); g.flush()
    p.write_bytes(raw.getvalue())
    assert list(iter_lines(p)) == ['{"i": 1}']


def test_live_tape_is_not_read_as_empty():
    part = ROOT / "data_lake" / "events" / "liquidations"
    if not any(part.rglob("events-*.jsonl.gz")):
        return
    assert read_tape(part, limit=5), "partitions exist but the reader returned nothing"
