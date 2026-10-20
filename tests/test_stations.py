import pytest

from riverforecast import stations


@pytest.mark.parametrize(
    "river,station,code",
    [
        ("Usumacinta", "Boca del Cerro", "BDCTB"),
        ("Pichucalco", "San Joaquín", "SJOCP"),
        ("PICHUCALCO", "San Joaquin", "SJOCP"),
        ("Carrizal", "Gonzalez", "GONTB"),
        ("Grijalva", "Gaviotas", "GABTB"),
        ("Grijalva", "Porvenir", "EPOTB"),
        ("de la  sierra", "Pueblo  Nuevo", "PNVTB"),
        ("Oxolotan", "OXOLOTÁN", "OXOTB"),
    ],
)
def test_code_for_matches_printed_names(river, station, code):
    assert stations.code_for(river, station) == code


@pytest.mark.parametrize(
    "river,station", [("Grijalva", "Samaria"), ("Usumacinta", "Tenosique"), ("", "")]
)
def test_unknown_rows_have_no_code(river, station):
    assert stations.code_for(river, station) is None


def test_every_code_is_unique():
    codes = [code for _, _, code in stations.STATIONS]
    assert len(codes) == len(set(codes)) == 14
