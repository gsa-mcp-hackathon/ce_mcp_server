"""The `ce-meta` command (`ce_meta.build.cli`)."""

from ce_meta.build.cli import main


def _run(stored, dictionary, *extra) -> int:
    return main(
        [
            "build",
            "--data",
            str(stored.out_dir),
            "--dictionary",
            str(dictionary),
            *extra,
        ]
    )


def test_build_writes_the_four_reference_tables(stored, dictionary, capsys):
    assert _run(stored, dictionary) == 0
    ref = stored.out_dir / "ref"
    assert sorted(p.name for p in ref.iterdir()) == [
        "codes.parquet",
        "diary_variables.parquet",
        "geo_crosswalk.parquet",
        "variables.parquet",
    ]
    out = capsys.readouterr().out
    assert "built  ref/variables: 6 rows" in out
    assert "check  4 records compared, 2 left out" in out


def test_the_crosswalk_step_needs_the_codes(stored, dictionary, capsys):
    assert _run(stored, dictionary, "--step", "crosswalk") == 1
    assert "ref/codes is not built" in capsys.readouterr().err
    assert _run(stored, dictionary, "--step", "dictionary") == 0
    assert _run(stored, dictionary, "--step", "crosswalk") == 0


def test_a_missing_dictionary_is_an_error(stored, tmp_path, capsys):
    assert _run(stored, tmp_path / "nothing.xlsx") == 1
    assert "no dictionary" in capsys.readouterr().err
