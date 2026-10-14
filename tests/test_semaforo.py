import datetime as dt
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from riverforecast import semaforo

FIXTURES = Path(__file__).parent / "fixtures"
GREEN, YELLOW, GRAY, BROWN = (0, 175, 40), (253, 232, 32), (161, 161, 161), (142, 81, 45)


def synthetic(states=None, lines=semaforo.GRID_LINES):
    """White 819x1024 image with the table grid and the given (ayer, hoy) colors per row."""
    image = np.full((1024, 819, 3), 255, dtype=np.uint8)
    for y in lines:
        image[y : y + 2, 200:810] = 0
    states = states or {}
    for row in range(1, 15):
        ayer, hoy = states.get(row, (GREEN, GREEN))
        top, bottom = lines[row] + 3, lines[row + 1] - 2
        image[top:bottom, semaforo.AYER_X - 30 : semaforo.AYER_X + 30] = ayer
        image[top:bottom, semaforo.HOY_X - 30 : semaforo.HOY_X + 30] = hoy
    return Image.fromarray(image)


def test_reads_every_river_row():
    image = synthetic({13: (GREEN, YELLOW), 1: (GRAY, GRAY), 4: (BROWN, GREEN)})

    states = semaforo.read_states(image)

    assert len(states) == 14
    assert states[13] == semaforo.RowState(13, "Usumacinta", "normal", "precaucion")
    assert states[1] == semaforo.RowState(1, "Samaria", "bajo", "bajo")
    assert states[4].ayer == "abajo_del_minimo"
    assert states[2].hoy == "normal"


def test_reads_the_published_image_of_2026_10_14():
    states = semaforo.read_states(Image.open(FIXTURES / "semaforo_2026-10-14.jpeg"))

    assert states[13] == semaforo.RowState(13, "Usumacinta", "normal", "precaucion")
    assert states[1].hoy == "bajo"
    assert all(states[row].hoy == "normal" for row in range(2, 13))
    assert states[14].hoy == "normal"


def test_reads_the_published_image_of_2026_07_22():
    states = semaforo.read_states(Image.open(FIXTURES / "semaforo_2026-07-22.jpeg"))

    assert {state.hoy for state in states.values()} <= set(semaforo.LEVELS)
    assert states[13].hoy == "normal"
    assert "abajo_del_minimo" in {state.hoy for state in states.values()}


def test_rejects_an_image_of_another_size():
    with pytest.raises(semaforo.LayoutError, match="image is 800x1024, expected 819x1024"):
        semaforo.read_states(Image.new("RGB", (800, 1024), "white"))


def test_rejects_a_moved_table():
    moved = [y + 6 for y in semaforo.GRID_LINES]

    with pytest.raises(semaforo.LayoutError, match="table grid not found at the expected rows"):
        semaforo.read_states(synthetic(lines=moved))


def test_rejects_a_cell_color_outside_the_legend():
    image = synthetic({13: (GREEN, (30, 60, 220))})

    with pytest.raises(
        semaforo.LayoutError, match="row 13 Usumacinta hoy: color \\(30, 60, 220\\)"
    ):
        semaforo.read_states(image)


def test_image_url_uses_the_day_month_year_name():
    assert semaforo.image_url(dt.date(2026, 10, 14)) == (
        "https://tabasco.gob.mx/sites/default/files/users/useripcettabasco/semaforo141026.jpeg"
    )
