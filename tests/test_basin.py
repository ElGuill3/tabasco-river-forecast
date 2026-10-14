import pytest

from riverforecast.basin import BasinError, upstream_ids


def test_collects_every_subbasin_that_drains_to_the_outlet():
    # 1 is the outlet; 2 and 3 drain into 1; 4 drains into 2; 5 drains elsewhere.
    links = {1: 0, 2: 1, 3: 1, 4: 2, 5: 9}

    assert upstream_ids(links, outlet=1) == {1, 2, 3, 4}


def test_a_headwater_outlet_is_its_own_basin():
    assert upstream_ids({1: 0, 2: 1}, outlet=2) == {2}


def test_unknown_outlet_is_rejected():
    with pytest.raises(BasinError, match="outlet 7 is not in the sub-basin table"):
        upstream_ids({1: 0}, outlet=7)
