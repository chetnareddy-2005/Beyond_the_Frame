from __future__ import annotations
import pytest
from backend import operator_locations

def test_location_requires_opt_in_and_stores_approximate_position(tmp_path):
    result = operator_locations.save_location(tmp_path, 7, "sector", "S2", {"enabled": True, "latitude": 17.385123, "longitude": 78.486765})
    assert result["sharing"] is True
    assert result["latitude"] == 17.385
    assert result["longitude"] == 78.487
    assert operator_locations.get_location(tmp_path, 7)["sharing"] is True
    rows = operator_locations.list_locations(tmp_path)
    assert len(rows) == 1 and rows[0]["sector_number"] == "S2"
    assert operator_locations.save_location(tmp_path, 7, "sector", "S2", {"enabled": False}) == {"sharing": False}
    assert operator_locations.get_location(tmp_path, 7) == {"sharing": False}
    assert operator_locations.list_locations(tmp_path) == []

def test_location_rejects_bad_coordinates_and_unknown_roles(tmp_path):
    with pytest.raises(ValueError):
        operator_locations.save_location(tmp_path, 7, "sector", "S1", {"enabled": True, "latitude": 999, "longitude": 0})
    with pytest.raises(ValueError):
        operator_locations.save_location(tmp_path, 7, "observer", None, {"enabled": True, "latitude": 0, "longitude": 0})
