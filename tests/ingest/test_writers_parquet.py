from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from ce_ingest.config import IngestSettings
from ce_ingest.derive import add_partition_columns
from ce_ingest.errors import WriteError
from ce_ingest.writers import ParquetWriter, get_writer
from ce_ingest.writers.parquet import PART_FILE, duckdb_source


@pytest.fixture
def writer(out_dir: Path) -> ParquetWriter:
    return ParquetWriter(out_dir)


def _part(make_src, rel, cost):
    src = make_src(rel)
    t = pa.table({"newid": ["05666064"], "cost": pa.array([cost], pa.float64())})
    return src, add_partition_columns(t, src)


def _files(path: Path) -> list[Path]:
    return sorted(p for p in path.rglob("*") if p.is_file())


def test_write_partition_layout(writer, make_src):
    src, t = _part(make_src, "intrvw25/mtbi251x", 1.0)
    writer.write_partition(src, t)
    target = writer.out_dir / "intrvw/mtbi/survey_year=2025/quarter_code=1x"
    assert _files(target) == [target / PART_FILE]
    stored = pq.read_table(target / PART_FILE)
    assert stored.column_names == ["newid", "cost", "quarter"]  # partition cols in path


def test_rewrite_is_idempotent(writer, make_src):
    src, first = _part(make_src, "intrvw25/mtbi252", 1.0)
    _, second = _part(make_src, "intrvw25/mtbi252", 2.0)
    writer.write_partition(src, first)
    writer.write_partition(src, second)
    files = _files(writer.out_dir / "intrvw")
    assert len(files) == 1
    assert pq.read_table(files[0])["cost"].to_pylist() == [2.0]
    assert _files(writer.out_dir / "_staging") == []


def test_failed_staging_write_keeps_old_partition(writer, make_src, monkeypatch):
    src, first = _part(make_src, "intrvw25/mtbi252", 1.0)
    writer.write_partition(src, first)

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pq, "write_table", boom)
    _, second = _part(make_src, "intrvw25/mtbi252", 2.0)
    with pytest.raises(WriteError, match="staging"):
        writer.write_partition(src, second)
    monkeypatch.undo()
    stored = pq.read_table(writer.partition_dir(src) / PART_FILE)
    assert stored["cost"].to_pylist() == [1.0]


def test_failed_swap_restores_old_partition(writer, make_src, monkeypatch):
    src, first = _part(make_src, "intrvw25/mtbi252", 1.0)
    writer.write_partition(src, first)

    real_rename = Path.rename

    def rename(self, target):
        if Path(target) == writer.partition_dir(src) and self.name != "old":
            raise OSError("rename failed")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", rename)
    _, second = _part(make_src, "intrvw25/mtbi252", 2.0)
    with pytest.raises(WriteError, match="swap"):
        writer.write_partition(src, second)
    monkeypatch.undo()
    stored = pq.read_table(writer.partition_dir(src) / PART_FILE)
    assert stored["cost"].to_pylist() == [1.0]


def test_read_schema(writer, make_src):
    assert writer.read_schema("intrvw", "mtbi") is None
    src_a, a = _part(make_src, "intrvw25/mtbi251x", 1.0)
    src_b = make_src("intrvw25/mtbi252")
    b = add_partition_columns(pa.table({"newid": ["1"], "ucc": ["010120"]}), src_b)
    writer.write_partition(src_a, a)
    writer.write_partition(src_b, b)

    schema = writer.read_schema("intrvw", "mtbi")
    assert set(schema.names) == {
        "newid",
        "cost",
        "quarter",
        "ucc",
        "survey_year",
        "quarter_code",
    }
    assert schema.field("survey_year").type == pa.int32()
    assert schema.field("quarter_code").type == pa.string()
    assert "ucc" not in writer.read_schema("intrvw", "mtbi", exclude=src_b).names


def test_duckdb_reads_table_with_partition_columns(writer, make_src):
    for rel in ["diary25/dtbd252", "diary25/dtbd253"]:  # no 1x: types must hold
        src, t = _part(make_src, rel, 1.0)
        writer.write_partition(src, t)
    table_dir = writer.table_dir("diary", "dtbd")
    rel = duckdb.sql(f"select * from {duckdb_source(table_dir)} order by quarter_code")
    rows = rel.fetchall()
    types = dict(zip(rel.columns, map(str, rel.types)))
    assert types["survey_year"] == "INTEGER"
    assert types["quarter_code"] == "VARCHAR"
    assert [r[rel.columns.index("quarter_code")] for r in rows] == ["2", "3"]


def test_meta_append_and_replace(writer):
    rows = pa.table({"table": ["fmli", "fmli"], "qc": ["1x", "2"], "n": [1, 2]})
    writer.append_rows("t", rows)
    writer.append_rows("t", pa.table({"table": ["memi"], "qc": ["1x"], "n": [3]}))
    assert pq.read_table(writer.meta_path("t")).num_rows == 3

    writer.replace_rows(
        "t",
        pa.table({"table": ["fmli"], "qc": ["1x"], "n": [9]}),
        where={"table": "fmli", "qc": "1x"},
    )
    out = pq.read_table(writer.meta_path("t")).sort_by(
        [("table", "ascending"), ("qc", "ascending")]
    )
    assert out.to_pylist() == [
        {"table": "fmli", "qc": "1x", "n": 9},
        {"table": "fmli", "qc": "2", "n": 2},
        {"table": "memi", "qc": "1x", "n": 3},
    ]


def test_get_writer(tmp_path):
    w = get_writer(IngestSettings(out_dir=tmp_path, backend="parquet"))
    assert isinstance(w, ParquetWriter)
    with pytest.raises(NotImplementedError):
        get_writer(IngestSettings(out_dir=tmp_path, backend="delta"))


def test_ref_tables_live_beside_meta_tables(writer):
    rows = pa.table({"variable_name": ["region"], "table": ["fmli"]})
    assert writer.read_rows("variables", "ref") is None
    writer.replace_table("ref", "variables", rows)
    assert writer.meta_path("variables", "ref").parent.name == "ref"
    assert writer.read_rows("variables", "ref").to_pylist() == rows.to_pylist()
    assert writer.read_rows("variables") is None  # the meta area is untouched

    writer.append_rows("variables", rows, area="ref")
    assert writer.read_rows("variables", "ref").num_rows == 2
    writer.replace_rows(
        "variables", rows, where={"variable_name": "region"}, area="ref"
    )
    assert writer.read_rows("variables", "ref").num_rows == 1


def test_replace_table_swaps_the_whole_file_and_survives_a_failure(writer, monkeypatch):
    first = pa.table({"n": [1, 2, 3]})
    writer.replace_table("ref", "codes", first)
    writer.replace_table("ref", "codes", pa.table({"n": [9]}))
    assert writer.read_rows("codes", "ref").to_pylist() == [{"n": 9}]

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(pq, "write_table", fail)
    with pytest.raises(Exception, match="ref/codes: write failed"):
        writer.replace_table("ref", "codes", first)
    monkeypatch.undo()
    assert writer.read_rows("codes", "ref").to_pylist() == [{"n": 9}]
    assert [p.name for p in writer.meta_path("codes", "ref").parent.iterdir()] == [
        "codes.parquet"
    ]
