import math

import pandas as pd
import pytest

from riverforecast.conagua import StationFileError, decode_sih_bytes, gap_summary, parse_sih_csv

HIDRO = """Comisión Nacional del Agua
Subdirección General Técnica
Estación: Boca del Cerro
Clave: BDCTB
Estado: Tabasco
Municipio: Tenosique

Fecha, Nivel(m), Gasto(m³/s)
1995/01/01,3.10,-
1995/01/02,-,-
1995/01/04,2.91,1203.50
"""

CLIMA = """Comisión Nacional del Agua
Subdirección General Técnica
Estación: Boca del Cerro
Clave: BDCTB
Estado: Tabasco
Municipio: Tenosique

Fecha, Precipitación(mm), Temperatura Ambiente(ºC), Temperatura Máxima(ºC), Temperatura Mínima(ºC), Evaporación(mm)
2024/05/23,0.00,33.80,41.00,26.50,
2024/05/24,12.40,-,-,-,-
"""


def test_parses_hydrometric_file_with_metadata_and_missing_marks():
    station = parse_sih_csv(HIDRO)

    assert station.metadata == {
        "Estación": "Boca del Cerro",
        "Clave": "BDCTB",
        "Estado": "Tabasco",
        "Municipio": "Tenosique",
    }
    assert list(station.data.columns) == ["level_m", "discharge_m3s"]
    assert list(station.data.index) == list(
        pd.to_datetime(["1995-01-01", "1995-01-02", "1995-01-04"])
    )
    assert station.data.loc["1995-01-01", "level_m"] == 3.10
    assert math.isnan(station.data.loc["1995-01-01", "discharge_m3s"])
    assert math.isnan(station.data.loc["1995-01-02", "level_m"])
    assert station.data.loc["1995-01-04", "discharge_m3s"] == 1203.50


def test_parses_climatological_file_and_treats_empty_fields_as_missing():
    station = parse_sih_csv(CLIMA)

    assert list(station.data.columns) == [
        "precip_mm",
        "temp_mean_c",
        "temp_max_c",
        "temp_min_c",
        "evaporation_mm",
    ]
    assert station.data.loc["2024-05-23", "temp_max_c"] == 41.00
    assert math.isnan(station.data.loc["2024-05-23", "evaporation_mm"])
    assert station.data.loc["2024-05-24", "precip_mm"] == 12.40
    assert math.isnan(station.data.loc["2024-05-24", "temp_mean_c"])


@pytest.mark.parametrize(
    ("row", "message"),
    [
        ("1995/01/05,2.80", "line 12: expected 3 fields, got 2"),
        ("1995/13/05,2.80,-", "line 12: invalid date '1995/13/05'"),
        ("1995/01/05,abc,-", "line 12: invalid number 'abc'"),
        ("1995/01/04,2.80,-", "line 12: duplicate date 1995-01-04"),
        ("1995/01/03,2.80,-", "line 12: date 1995-01-03 is not after 1995-01-04"),
    ],
)
def test_rejects_malformed_rows_with_their_line_number(row, message):
    with pytest.raises(StationFileError, match=message):
        parse_sih_csv(HIDRO + row + "\n")


def test_rejects_files_without_a_column_header():
    with pytest.raises(StationFileError, match="no 'Fecha' column header"):
        parse_sih_csv("Clave: BDCTB\n1995/01/01,3.10,-\n")


def test_rejects_unknown_columns():
    text = HIDRO.replace("Gasto(m³/s)", "Caudal(l/s)")
    with pytest.raises(StationFileError, match="unknown column 'Caudal\\(l/s\\)'"):
        parse_sih_csv(text)


HIDRO_KEYED = """Comisión Nacional del Agua
Subdirección General Técnica
Estación: San Joaquín, Chis.
Clave: SJOCP
Estado: Chiapas
Municipio: Pichucalco

Estacion,Fecha,Nivel(m),Gasto en Rio(m3/s)
SJOCP,2002-09-18,19.84,28.0
SJOCP,2004-05-04,23.94,
"""


def test_parses_files_with_a_station_key_column_and_iso_dates():
    station = parse_sih_csv(HIDRO_KEYED)

    assert station.metadata["Clave"] == "SJOCP"
    assert list(station.data.columns) == ["level_m", "discharge_m3s"]
    assert list(station.data.index) == list(pd.to_datetime(["2002-09-18", "2004-05-04"]))
    assert station.data.loc["2002-09-18", "level_m"] == 19.84
    assert station.data.loc["2002-09-18", "discharge_m3s"] == 28.0
    assert math.isnan(station.data.loc["2004-05-04", "discharge_m3s"])


def test_rejects_a_station_key_that_differs_from_the_metadata():
    with pytest.raises(StationFileError, match="line 11: station 'SAACP' is not 'SJOCP'"):
        parse_sih_csv(HIDRO_KEYED + "SAACP,2004-05-05,21.30,96.0\n")


def test_keeps_the_original_date_format_strict():
    with pytest.raises(StationFileError, match="line 12: invalid date '1995-01-05'"):
        parse_sih_csv(HIDRO + "1995-01-05,2.80,-\n")


def test_gap_summary_reports_coverage_and_longest_gaps():
    index = pd.date_range("2000-01-01", "2000-01-10", freq="D")
    values = [1.0, None, None, 1.0, 1.0, None, 1.0, 1.0, 1.0, 1.0]
    series = pd.Series(values, index=index, dtype=float)

    summary = gap_summary(series, "2000-01-01", "2000-01-12")

    assert summary.days == 12
    assert summary.observed == 7
    assert summary.coverage == pytest.approx(7 / 12)
    # Days after the last record count as a gap too.
    assert summary.longest_gaps == [
        (pd.Timestamp("2000-01-02"), pd.Timestamp("2000-01-03"), 2),
        (pd.Timestamp("2000-01-11"), pd.Timestamp("2000-01-12"), 2),
        (pd.Timestamp("2000-01-06"), pd.Timestamp("2000-01-06"), 1),
    ]


@pytest.mark.parametrize("encoding", ["utf-8", "latin-1", "mojibake"])
def test_decodes_every_encoding_conagua_serves(encoding):
    if encoding == "mojibake":
        raw = HIDRO.encode("utf-8").decode("latin-1").encode("utf-8")
    else:
        raw = HIDRO.encode(encoding)

    assert decode_sih_bytes(raw) == HIDRO
