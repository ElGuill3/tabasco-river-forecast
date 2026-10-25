"""CONAGUA codes of the stations in the IPCET daily level table."""

from __future__ import annotations

import unicodedata

# (river, station) as printed by IPCET, in table order.
STATIONS = (
    ("Samaria", "Samaria", "SAMTB"),
    ("Carrizal", "González", "GONTB"),
    ("Oxolotán", "Oxolotán", "OXOTB"),
    ("Tacotalpa", "Tapijulapa", "TAPTB"),
    ("Puyacatengo", "Puyacatengo", "PUYTB"),
    ("Teapa", "Teapa", "TEATB"),
    ("De la Sierra", "Pueblo Nuevo", "PNVTB"),
    ("Pichucalco", "San Joaquín", "SJOCP"),
    ("Grijalva", "Gaviotas", "GABTB"),
    ("Grijalva", "Porvenir", "EPOTB"),
    ("Tulija", "Salto de Agua", "SAACP"),
    ("Puxcatan", "Macuspana", "MACTB"),
    ("Usumacinta", "Boca del Cerro", "BDCTB"),
    ("San Pedro", "San Pedro", "SPTTB"),
)


# L67 publication subset; NAMO is the IPCET station datum, in metres.
PUBLISHED = ("BDCTB", "MACTB", "SAACP", "PNVTB", "TAPTB")
NAMO = dict(zip(PUBLISHED, (19.21, 9.85, 10.99, 7.49, 24.63), strict=True))
# Frozen Open-Meteo coordinates from data/raw/glofas/<CODE>/cell.json (L67).
# Kept with serving configuration so a broken artifact cannot block the fetch.
CELLS = dict(
    zip(
        PUBLISHED,
        (
            (17.425, -91.525),
            (17.725, -92.475),
            (17.575, -92.375),
            (17.975, -92.975),
            (17.525, -92.825),
        ),
        strict=True,
    )
)


def identity(code: str) -> dict:
    return {"code": code, "name": next(s for _, s, c in STATIONS if c == code)}


def normalise(name: str) -> str:
    """Case-, accent- and spacing-insensitive form of a printed name."""
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.casefold().split())


_CODES = {(normalise(river), normalise(station)): code for river, station, code in STATIONS}


def code_for(river: str, station: str) -> str | None:
    """CONAGUA code of a table row, or None for a row this table does not list."""
    return _CODES.get((normalise(river), normalise(station)))
