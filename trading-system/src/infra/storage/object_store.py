from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq
from pyarrow.fs import FileSystem, LocalFileSystem, S3FileSystem

from common.logging.setup import get_logger

logger = get_logger(__name__)


def _filesystem_for_uri(uri: str) -> FileSystem:
    if uri.startswith("s3://"):
        return S3FileSystem()
    return LocalFileSystem()


class S3ObjectStore:
    def __init__(self, uri: str):
        self.uri = uri.rstrip("/")
        self.fs = _filesystem_for_uri(uri)

    def list_partitions(self, prefix: str) -> List[str]:
        dataset = ds.dataset(f"{self.uri}/{prefix}" if not prefix.startswith("s3://") else prefix, filesystem=self.fs, format="parquet")
        return [str(fragment.partition_expression) for fragment in dataset.get_fragments()]


class S3ParquetReader:
    def __init__(self, filesystem: Optional[FileSystem] = None) -> None:
        self.filesystem = filesystem

    def read(self, prefix: str, filters: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
        fs = self.filesystem or _filesystem_for_uri(prefix)
        dataset = ds.dataset(prefix, format="parquet", filesystem=fs)
        filter_expr = None
        if filters:
            for key, value in filters.items():
                expr = ds.field(key) == value
                filter_expr = expr if filter_expr is None else filter_expr & expr
        table = dataset.to_table(filter=filter_expr)
        return table.to_pandas()


class S3ParquetWriter:
    def __init__(self, filesystem: Optional[FileSystem] = None) -> None:
        self.filesystem = filesystem

    def write(self, df: pd.DataFrame, prefix: str, partition_cols: Optional[Iterable[str]] = None) -> None:
        if df.empty:
            return
        fs = self.filesystem or _filesystem_for_uri(prefix)
        root = prefix.replace("s3://", "") if isinstance(fs, LocalFileSystem) else prefix
        table = pa.Table.from_pandas(df)
        tmp_path = str(root) + "_tmp"
        pq.write_to_dataset(table, root_path=tmp_path, partition_cols=list(partition_cols or []), filesystem=fs)
        # atomic rename best-effort
        if isinstance(fs, LocalFileSystem):
            Path(tmp_path).rename(root)
        logger.info({"msg": "wrote parquet dataset", "prefix": prefix, "rows": len(df)})



def write_clean_events(df: pd.DataFrame, path: str, partition_cols: Iterable[str]) -> None:
    writer = S3ParquetWriter()
    writer.write(df, path, partition_cols=partition_cols)


def write_quality_flags(df: pd.DataFrame, path: str, partition_cols: Iterable[str]) -> None:
    writer = S3ParquetWriter()
    writer.write(df, path, partition_cols=partition_cols)



def upload_file(local_path: str, remote_prefix: str) -> None:
    fs = _filesystem_for_uri(remote_prefix)
    target = f"{remote_prefix.rstrip('/')}/{Path(local_path).name}"
    with open(local_path, "rb") as src:
        with fs.open_output_stream(target) as dst:
            dst.write(src.read())


def download_file(remote_path: str, local_path: str) -> None:
    fs = _filesystem_for_uri(remote_path)
    Path(local_path).parent.mkdir(parents=True, exist_ok=True)
    with fs.open_input_file(remote_path) as src:
        with open(local_path, "wb") as dst:
            dst.write(src.read())
