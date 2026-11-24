import pyarrow as pa
import pytest

from ce_ingest.errors import SasReadError
from ce_ingest.sas_reader import read_sas


def test_read_sas_types_and_meta(sas_fixture):
    table, meta, missing = read_sas(sas_fixture)
    assert table.num_rows == 11935
    assert table.schema.field("NEWID").type == pa.string()
    assert table.schema.field("AMOUNT").type == pa.float64()
    by_name = {m.name: m for m in meta}
    assert by_name["UCC"].sas_type == "character"
    assert by_name["UCC"].format == "$6."
    assert by_name["AMOUNT"].sas_type == "numeric"
    assert set(missing) == set(table.column_names)


def test_read_sas_keeps_leading_zero(sas_fixture):
    table, _, _ = read_sas(sas_fixture)
    assert table["NEWID"][0].as_py().startswith("0")


def test_read_sas_unreadable(make_src):
    src = make_src("diary25/dtbd251")
    src.path.write_bytes(b"not a sas file")
    with pytest.raises(SasReadError, match="dtbd251"):
        read_sas(src)
