"""Genera figuras del informe sin red, entrenamiento ni cambios en los datos.

Uso: timeout 120 uv run python -B experiments/17_report_figures.py
Solo escribe docs/report/figures/. Pillow ya es dependencia del proyecto.
Las coordenadas son celdas CEMS, no ubicaciones topográficas de las estaciones.
El hidrograma es M1 del experimento 09, no el modelo con nivel publicado después.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import date
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from riverforecast.stations import NAMO, PUBLISHED, STATIONS

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/report/figures"
RESULTS = ROOT / "docs/results"
INK = "#243746"
GRID = "#dce3e8"
COLORS = ("#176b96", "#c17b19", "#6754a2")
FONT = "DejaVuSans.ttf"
SOURCES = ("15-station-ceiling.json", "16-station-models.json", "09-independent.json")


def font(size=23):
    return ImageFont.truetype(FONT, size)


def text(draw, xy, value, size=23, fill=INK, anchor=None):
    draw.text(xy, str(value), font=font(size), fill=fill, anchor=anchor)


def canvas(title, subtitle, height=1050):
    image = Image.new("RGB", (1800, height), "white")
    draw = ImageDraw.Draw(image)
    text(draw, (90, 36), title, 34)
    text(draw, (90, 88), subtitle, 22)
    return image, draw


def save(image, name):
    path = OUT / name
    image.save(path, dpi=(150, 150))
    print(path.relative_to(ROOT))


def load(name):
    return json.loads((RESULTS / name).read_text(encoding="utf-8"))


def station_map(result):
    image, draw = canvas(
        "Estaciones evaluadas: celdas fluviales de GloFAS",
        "Coordenadas CEMS del experimento 16; no son las coordenadas exactas de los aforos.",
    )
    cells = result["extraction"]["stations"]
    points = [v["cems_cell"] for v in cells.values()]
    lat_min = min(p[0] for p in points) - 0.12
    lat_max = max(p[0] for p in points) + 0.12
    lon_min = min(p[1] for p in points) - 0.16
    lon_max = max(p[1] for p in points) + 0.16
    left, top, right, bottom = 160, 200, 1150, 850

    def xy(lat, lon):
        return (
            left + (lon - lon_min) / (lon_max - lon_min) * (right - left),
            bottom - (lat - lat_min) / (lat_max - lat_min) * (bottom - top),
        )

    for i in range(6):
        lat = lat_min + i * (lat_max - lat_min) / 5
        lon = lon_min + i * (lon_max - lon_min) / 5
        _, y = xy(lat, lon_min)
        x, _ = xy(lat_min, lon)
        draw.line((left, y, right, y), fill=GRID)
        draw.line((x, top, x, bottom), fill=GRID)
        text(draw, (left - 15, y), f"{lat:.2f}", anchor="rm")
        text(draw, (x, bottom + 15), f"{lon:.2f}", anchor="mt")
    text(draw, (left, 151), "Latitud (° N)", 24)
    text(draw, ((left + right) / 2, 925), "Longitud (° E; oeste negativo)", anchor="mt")
    # Shared cells are labelled together, rather than jittering their coordinates.
    grouped = {}
    for code, entry in cells.items():
        grouped.setdefault(tuple(entry["cems_cell"]), []).append(code)
    for (lat, lon), codes in grouped.items():
        x, y = xy(lat, lon)
        color = COLORS[0] if any(c in PUBLISHED for c in codes) else "#777777"
        draw.ellipse((x - 8, y - 8, x + 8, y + 8), fill=color)
        text(draw, (x + 12, y - 16), "/".join(codes), 20, color)
    text(draw, (1220, 172), "Catálogo IPCET", 26)
    for i, (_, name, code) in enumerate(s for s in STATIONS if s[2] in cells):
        color = COLORS[0] if code in PUBLISHED else INK
        text(draw, (1220, 222 + i * 43), f"{code}: {name}", 21, color)
    text(draw, (1220, 790), "Azul: celda con publicación", 21, COLORS[0])
    text(draw, (1220, 825), "Gris: no publicado", 21, "#777777")
    text(
        draw,
        (90, 995),
        "Fuente: 16-station-models.json. Sin mapa base ni límites administrativos.",
        21,
    )
    save(image, "01-estaciones.png")


def skill_chart(result, number, name, subtitle):
    rows = result["rows"]
    codes = list(dict.fromkeys(r["station"] for r in rows))
    assert len(rows) == len(codes) * 3
    image, draw = canvas(
        f"Experimento {number}: habilidad con nivel frente a persistencia", subtitle, 1300
    )
    left, right, top, bottom = 220, 1600, 210, 1115
    low = min(-0.1, math.floor(min(r["l1_skill_vs_persistence"] for r in rows) * 5) / 5)
    high = 0.6

    def x(value):
        return left + (value - low) / (high - low) * (right - left)

    for i in range(round((high - low) / 0.2) + 1):
        value = low + i * 0.2
        px = x(value)
        draw.line((px, top, px, bottom), fill=GRID, width=2)
        text(draw, (px, bottom + 15), f"{value:.1f}", anchor="mt")
    draw.line((x(0), top, x(0), bottom), fill=INK, width=3)
    for i, code in enumerate(codes):
        y = top + (i + 0.5) * (bottom - top) / len(codes)
        text(draw, (left - 25, y), code, 25, anchor="rm")
        for j, lead in enumerate((1, 3, 7)):
            row = next(r for r in rows if r["station"] == code and r["lead_days"] == lead)
            value = row["l1_skill_vs_persistence"]
            py = y + (j - 1) * 20
            draw.rectangle(
                (min(x(0), x(value)), py - 7, max(x(0), x(value)), py + 7), fill=COLORS[j]
            )
            text(draw, (max(x(0), x(value)) + 10, py), f"{value:.2f}", 18, anchor="lm")
    for j, lead in enumerate((1, 3, 7)):
        draw.rectangle((920 + j * 220, 150, 950 + j * 220, 166), fill=COLORS[j])
        text(draw, (962 + j * 220, 144), f"{lead} día(s)", 22)
    text(draw, (left, 155), "Estación", 24)
    text(draw, (900, 1190), "Habilidad = 1 − MAE del modelo / MAE de persistencia", anchor="mt")
    text(
        draw,
        (90, 1250),
        "Positivo: mejora; negativo: empeora. Cada comparación usa las mismas filas.",
        21,
    )
    save(image, name)


def hydrograph(result):
    rows = [r for r in result["levels"]["1"]["rows"] if r["date"].startswith("2026-10")]
    rows.sort(key=lambda r: r["date"])
    assert rows and all(r["q10"] <= r["q50"] <= r["q90"] for r in rows)
    image, draw = canvas(
        "Boca del Cerro: ejemplo de pronósticos M1 a un día",
        "Verificación con niveles citados en prensa; experimento 09, sin reajuste del modelo.",
    )
    left, right, top, bottom = 160, 1660, 220, 820
    dates = [date.fromisoformat(r["date"]) for r in rows]
    low = math.floor(min(r["q10"] for r in rows))
    high = math.ceil(max(NAMO["BDCTB"], max(r["q90"] for r in rows)))

    def xy(day, value):
        return (
            left + (day - dates[0]).days / (dates[-1] - dates[0]).days * (right - left),
            bottom - (value - low) / (high - low) * (bottom - top),
        )

    for value in range(low, high + 1):
        _, y = xy(dates[0], value)
        draw.line((left, y, right, y), fill=GRID)
        text(draw, (left - 20, y), value, anchor="rm")
    band = [xy(d, r["q10"]) for d, r in zip(dates, rows, strict=True)]
    band += [xy(d, r["q90"]) for d, r in reversed(list(zip(dates, rows, strict=True)))]
    draw.polygon(band, fill="#d8eaf5")
    draw.line([xy(d, r["q50"]) for d, r in zip(dates, rows, strict=True)], fill=COLORS[0], width=4)
    for day, row in zip(dates, rows, strict=True):
        x, y = xy(day, row["quoted_m"])
        draw.ellipse((x - 7, y - 7, x + 7, y + 7), fill="#b54832")
        text(draw, (x, bottom + 15), day.strftime("%d/%m"), 21, anchor="mt")
    _, namo_y = xy(dates[0], NAMO["BDCTB"])
    draw.line((left, namo_y, right, namo_y), fill="#666666", width=2)
    text(draw, (right, namo_y - 12), "NAMO 19.21 m", 20, anchor="rb")
    text(draw, (left, 165), "Nivel en la escala de la estación (m)", 24)
    text(draw, (900, 890), "Fecha de validez (octubre de 2026)", anchor="mt")
    text(draw, (160, 950), "Azul: q50 y banda q10–q90 | Puntos rojos: niveles citados", 23)
    text(
        draw,
        (90, 995),
        "Fuente: 09-independent.json, levels/1. "
        "Líneas entre fechas disponibles; no se imputan días.",
        21,
    )
    save(image, "04-hidrograma-bdctb.png")


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    ceiling, operational, independent = (load(name) for name in SOURCES)
    OUT.mkdir(parents=True, exist_ok=True)
    station_map(operational)
    skill_chart(
        ceiling,
        15,
        "02-habilidad-techo.png",
        "Desarrollo 1998–2016: reanálisis como pronóstico perfecto "
        "y nivel siempre disponible. No es aceptación.",
    )
    skill_chart(
        operational,
        16,
        "03-habilidad-operativa.png",
        "Pronósticos CEMS reales; nivel de emisión siempre disponible. BDCTB es solo referencia.",
    )
    hydrograph(independent)
    manifest = {
        "dpi": 150,
        "inputs_sha256": {
            f"docs/results/{name}": hashlib.sha256((RESULTS / name).read_bytes()).hexdigest()
            for name in SOURCES
        },
        "map_coordinates": "CEMS cell labels from experiment 16, not gauge coordinates",
        "hydrograph": "Experiment 09 M1, levels/1, October 2026 press-quoted dates only",
        "figures": sorted(p.name for p in OUT.glob("*.png")),
    }
    (OUT / "procedencia.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
