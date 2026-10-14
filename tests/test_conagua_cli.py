from riverforecast import conagua, conagua_cli
from tests.test_conagua import HIDRO


def test_download_saves_a_valid_file_as_utf8(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(conagua, "download_station", lambda kind, code: HIDRO)

    code = conagua_cli.main(["--root", str(tmp_path), "download", "hidros", "BDCTB"])

    saved = tmp_path / "hidros" / "BDCTB.csv"
    assert code == 0
    assert saved.read_bytes() == HIDRO.encode("utf-8")
    assert capsys.readouterr().out == "hidros/BDCTB: 3 days, 1995-01-01 to 1995-01-04\n"
    assert list(saved.parent.iterdir()) == [saved]


def test_invalid_download_keeps_the_previous_file(tmp_path, monkeypatch, capsys):
    saved = tmp_path / "hidros" / "BDCTB.csv"
    saved.parent.mkdir(parents=True)
    saved.write_text(HIDRO, encoding="utf-8")
    monkeypatch.setattr(conagua, "download_station", lambda kind, code: "not a station file")

    code = conagua_cli.main(["--root", str(tmp_path), "download", "hidros", "BDCTB"])

    assert code == 1
    assert saved.read_text(encoding="utf-8") == HIDRO
    assert list(saved.parent.iterdir()) == [saved]
    assert capsys.readouterr().err == (
        "hidros/BDCTB: rejected download: no 'Fecha' column header\n"
    )


def test_report_prints_coverage_and_longest_gaps(tmp_path, capsys):
    saved = tmp_path / "hidros" / "BDCTB.csv"
    saved.parent.mkdir(parents=True)
    saved.write_text(HIDRO, encoding="utf-8")

    code = conagua_cli.main(
        [
            "--root",
            str(tmp_path),
            "report",
            "hidros",
            "BDCTB",
            "--column",
            "level_m",
            "--start",
            "1995-01-01",
            "--end",
            "1995-01-05",
        ]
    )

    assert code == 0
    assert capsys.readouterr().out == (
        "hidros/BDCTB level_m 1995-01-01..1995-01-05: 2 of 5 days (40.0%)\n"
        "  gap 1995-01-02..1995-01-03 (2 days)\n"
        "  gap 1995-01-05..1995-01-05 (1 day)\n"
    )


def test_download_failure_is_reported_without_a_traceback(tmp_path, monkeypatch, capsys):
    def blocked(kind, code):
        raise conagua.DownloadError("HTTP 403")

    monkeypatch.setattr(conagua, "download_station", blocked)

    code = conagua_cli.main(["--root", str(tmp_path), "download", "hidros", "BDCTB"])

    assert code == 1
    assert not (tmp_path / "hidros").exists()
    assert capsys.readouterr().err == (
        "hidros/BDCTB: download failed: HTTP 403; the server may be rate limiting, "
        "retry later or save the file from a browser and use 'import'\n"
    )


def test_import_validates_and_stores_a_local_file(tmp_path, capsys):
    source = tmp_path / "manual.csv"
    source.write_bytes(HIDRO.encode("latin-1"))
    root = tmp_path / "raw"

    code = conagua_cli.main(["--root", str(root), "import", "hidros", "BDCTB", str(source)])

    assert code == 0
    assert (root / "hidros" / "BDCTB.csv").read_bytes() == HIDRO.encode("utf-8")
    assert capsys.readouterr().out == "hidros/BDCTB: 3 days, 1995-01-01 to 1995-01-04\n"


def test_import_rejects_an_invalid_file(tmp_path, capsys):
    source = tmp_path / "manual.csv"
    source.write_text("<html>blocked</html>", encoding="utf-8")
    root = tmp_path / "raw"

    code = conagua_cli.main(["--root", str(root), "import", "hidros", "BDCTB", str(source)])

    assert code == 1
    assert not root.exists()
    assert capsys.readouterr().err == "hidros/BDCTB: rejected file: no 'Fecha' column header\n"


def test_report_on_a_missing_station_explains_how_to_get_it(tmp_path, capsys):
    code = conagua_cli.main(
        ["--root", str(tmp_path), "report", "hidros", "BDCTB", "--column", "level_m"]
    )

    assert code == 1
    assert capsys.readouterr().err == (
        f"hidros/BDCTB: no file at {tmp_path / 'hidros' / 'BDCTB.csv'}; "
        "run 'download' or 'import' first\n"
    )
