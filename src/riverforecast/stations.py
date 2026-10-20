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


def normalise(name: str) -> str:
    """Case-, accent- and spacing-insensitive form of a printed name."""
    decomposed = unicodedata.normalize("NFKD", name)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(plain.casefold().split())


_CODES = {(normalise(river), normalise(station)): code for river, station, code in STATIONS}


def code_for(river: str, station: str) -> str | None:
    """CONAGUA code of a table row, or None for a row this table does not list."""
    return _CODES.get((normalise(river), normalise(station)))
