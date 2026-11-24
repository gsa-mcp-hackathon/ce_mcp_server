from ce_ingest.cli import main

from .test_pipeline import diary_release  # noqa: F401  (fixture)


def test_files_lists_in_scope(landing_tree, capsys):
    assert main(["files", "--landing", str(landing_tree)]) == 0
    out = capsys.readouterr().out
    assert "fmli251x" in out and "expn_apa" in out and "7 file(s)" in out
    assert "skipped" in out and "rnt24" in out and "fpar2425" not in out


def test_load_exits_1_on_failure(diary_release, out_dir, capsys):  # noqa: F811
    code = main(["load", "--landing", str(diary_release), "--out", str(out_dir)])
    assert code == 1
    out = capsys.readouterr().out
    assert "4 file(s), 1 failed" in out
    assert "failed" in out and "dtbd253" in out


def test_profile_after_load(diary_release, out_dir, capsys):  # noqa: F811
    main(
        ["load", "--landing", str(diary_release), "--out", str(out_dir), "--no-profile"]
    )
    assert not (out_dir / "meta" / "column_profile.parquet").exists()
    capsys.readouterr()
    assert main(["profile", "--out", str(out_dir), "--table", "dtbd"]) == 0
    assert "profiled  diary/dtbd: 9 columns" in capsys.readouterr().out
    assert main(["profile", "--out", str(out_dir), "--table", "nope"]) == 1
