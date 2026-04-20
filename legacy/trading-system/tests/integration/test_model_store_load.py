import pickle
from pathlib import Path

from infra.storage.model_store import ModelStore


def test_model_store_load_local(tmp_path):
    model_dir = tmp_path / "artifacts/models/edge/model_a/latest"
    model_dir.mkdir(parents=True)
    dummy = {"model": "x"}
    (model_dir / "model.pkl").write_bytes(pickle.dumps(dummy))
    store = ModelStore(str(tmp_path))
    model = store.load_latest("edge", "model_a")
    assert model == dummy
