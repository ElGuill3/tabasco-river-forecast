import datetime as dt
import itertools

import pytest

from riverforecast import cems
from riverforecast import cems_reforecast as rf


def test_plan_covers_exact_v4_constraint_dates_once():
    rows = rf.load_constraints()
    expected = set()
    for row in rows:
        if row["system_version"] == ["version_4_0"]:
            expected.update(
                "-".join(d) for d in itertools.product(row["hyear"], row["hmonth"], row["hday"])
            )
    jobs = rf.plan_requests(rows)
    actual = [day for _, request in jobs for day in cems.request_dates(request)]
    assert set(actual) == expected
    assert len(actual) == len(expected)
    assert min(actual) == "2003-03-27"
    assert max(actual) == "2023-11-25"
    for _, request in jobs:
        assert len(cems.request_dates(request)) * 33 <= 900
        assert request["area"] == [18.3, -93.6, 17.1, -90.9]
        rf.validate_request(request, rows)


def test_validation_rejects_unavailable_date_and_missing_product():
    rows = rf.load_constraints()
    _, request = rf.plan_requests(rows)[0]
    request = dict(request, hyear=["1999"])
    with pytest.raises(cems.CemsError, match="constraints"):
        rf.validate_request(request, rows)
    request = rf.single_request(dt.date(2003, 4, 3), rows)
    assert cems.request_dates(request) == ["2003-04-03"]
    with pytest.raises(cems.CemsError):
        rf.validate_request(dict(request, product_type=["control_reforecast"]), rows)
