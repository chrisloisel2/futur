from __future__ import annotations

from typing import Any, Iterable, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.dataset as ds
import pyarrow.parquet as pq


def enforce_schema(df: pd.DataFrame, schema: pa.schema) -> pa.Table:
    table = pa.Table.from_pandas(df, preserve_index=False)
    return table.cast(schema)


def write_dataset(df: pd.DataFrame, path: str, partition_cols: Optional[Iterable[str]] = None, schema: Optional[pa.schema] = None) -> None:
    table = pa.Table.from_pandas(df)
    if schema is not None:
        table = table.cast(schema)
    pq.write_to_dataset(table, root_path=path, partition_cols=list(partition_cols or []))


def read_dataset(path: str, filters: Optional[Any] = None) -> pd.DataFrame:
    dataset = ds.dataset(path, format="parquet")
    table = dataset.to_table(filter=filters)
    return table.to_pandas()
