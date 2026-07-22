"""New strategy basket = London, Paris, Tokyo (drop Singapore/Shanghai)."""

from config import CITIES


def test_basket_is_london_paris_tokyo():
    assert {c["name"] for c in CITIES} == {"London", "Paris", "Tokyo"}


def test_tokyo_wired_correctly():
    tokyo = next(c for c in CITIES if c["name"] == "Tokyo")
    assert tokyo["station"] == "RJTT"
    assert tokyo["tz"] == "Asia/Tokyo"
    assert tokyo["region"] == "intl"


def test_all_intl_with_tz():
    assert all(c["region"] == "intl" and c.get("tz") for c in CITIES)
