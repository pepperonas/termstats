"""The hardware catalogue (0.10.0).

`termstats recommend` names a computer and a price. Both come from catalog/hardware.json,
which a person maintains and a script turns into Rust tables. These tests keep the file
honest - every entry has a source, every price a plausible size, every chip the scores the
comparison needs - and make sure the generated tables are the file as it is now.
"""

import json
import pathlib
import re
import sys
from datetime import date

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CATALOG = json.loads((ROOT / "catalog" / "hardware.json").read_text(encoding="utf-8"))
CHIPS = CATALOG["chips"]
DEVICES = CATALOG["devices"]
VARIANTS = [(d, v) for d in DEVICES for v in d["variants"]]

sys.path.insert(0, str(ROOT / "tools"))
import gen_native_catalog  # noqa: E402
sys.path.pop(0)


def test_the_generated_tables_are_the_catalogue_as_it_is():
    assert gen_native_catalog.TARGET.read_text(encoding="utf-8") == gen_native_catalog.render(), \
        "run: python tools/gen_native_catalog.py"


def test_the_catalogue_carries_its_date():
    stand = date.fromisoformat(CATALOG["stand"])
    assert date(2026, 11, 1) <= stand <= date.today()
    assert f'pub const STAND: &str = "{CATALOG["stand"]}";' in gen_native_catalog.render()


def test_every_current_mac_family_is_there():
    """The focus is Apple: a family missing here cannot be recommended at all."""
    apple = {d["id"] for d in DEVICES if d["vendor"] == "Apple"}
    assert {"macbook-air-13", "macbook-air-15", "macbook-pro-14", "macbook-pro-16", "mac-mini", "imac-24", "mac-studio"} <= apple
    assert all(d["os"] == "macOS" for d in DEVICES if d["vendor"] == "Apple")


def test_linux_machines_are_there_as_laptops_and_as_desktops():
    linux = [d for d in DEVICES if d["os"] == "Linux"]
    assert len(linux) >= 8
    assert {d["form"] for d in linux} >= {"laptop", "desktop"}


@pytest.mark.parametrize("device", DEVICES, ids=lambda d: d["id"])
def test_a_device_says_what_it_is_and_where_its_numbers_are_from(device):
    assert device["form"] in ("laptop", "desktop", "all-in-one")
    assert device["os"] in ("macOS", "Linux")
    assert not device["name"].startswith(device["vendor"] + " "), "the report prints vendor and name: no doubled vendor"
    assert isinstance(device["fanless"], bool) and isinstance(device["verified"], bool)
    assert re.match(r"https://", device["source"]), "every device names the page its prices came from"
    assert device["variants"], "a device nobody can configure is not a device"
    if device["vendor"] == "Apple":
        assert "apple.com/de" in device["source"] and device["verified"], "Apple prices come from the German Apple store"


def test_ids_are_unique():
    ids = [d["id"] for d in DEVICES]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("pair", VARIANTS, ids=lambda p: f'{p[0]["id"]}/{p[1]["chip"]}')
def test_a_variant_can_be_priced(pair):
    device, variant = pair
    assert variant["chip"] in CHIPS, "the variant names a chip the catalogue knows"
    price = variant["base_price_eur"]
    if price is None or variant["base_ssd_gb"] is None:
        return                      # researched but unpriced: the generator leaves it out
    assert 50 <= price <= 20000, f"an implausible price: {price}"
    assert variant["base_ram_gb"] in {4, 8, 12, 16, 18, 24, 32, 36, 48, 64, 96, 128, 192, 256, 512}
    assert variant["base_ssd_gb"] >= 32
    for options, base in ((variant["ram_options"], variant["base_ram_gb"]), (variant["ssd_options"], variant["base_ssd_gb"])):
        sizes = [o["gb"] for o in options]
        assert sizes == sorted(set(sizes)), "sizes ascend and none is listed twice"
        prices = [o["plus_eur"] for o in options if o["plus_eur"] is not None]
        assert prices == sorted(prices), "a larger size never costs less"
        assert all(gb >= base for gb in sizes), "an option below the base configuration is not an upgrade"
        if options:
            assert options[0] == {"gb": base, "plus_eur": 0}, "the base size is the first step, at no surcharge"


@pytest.mark.parametrize("chip_id", sorted(CHIPS))
def test_a_chip_has_cores_and_plausible_scores(chip_id):
    chip = CHIPS[chip_id]
    assert chip["name"] and re.match(r"https://", chip["source"]), "every score names its source"
    if chip["single"] is None or chip["multi"] is None:
        return                      # no scores: the generator leaves the chip out, see below
    assert (chip["perf_cores"] or 0) + (chip["eff_cores"] or 0) >= 1, "a chip that is compared has cores"
    assert 300 <= chip["single"] <= 6000 and chip["single"] <= chip["multi"] <= 80000
    assert chip["gpu"] is None or 1000 <= chip["gpu"] <= 600000


def test_every_chip_on_sale_can_be_compared():
    """A variant whose chip has no scores cannot be recommended and is silently absent from
    the tables - so the current Macs must not lose more than a stray variant that way."""
    chips, devices, skipped = gen_native_catalog.usable(CATALOG)
    kept = {device["id"] for device, _variants in devices}
    assert {d["id"] for d in DEVICES if d["vendor"] == "Apple"} <= kept, skipped
    apple = sum(len(d["variants"]) for d in DEVICES if d["vendor"] == "Apple")
    assert sum(len(v) for d, v in devices if d["vendor"] == "Apple") >= 0.9 * apple, skipped
    linux = [d for d, _v in devices if d["os"] == "Linux"]
    assert len(linux) >= 8 and {d["form"] for d in linux} >= {"laptop", "desktop"}, "the Linux side survives the filter too"


def test_older_macs_are_known_so_that_a_history_from_them_can_be_scaled():
    """The recommendation scales the measured load by the chip it was measured on."""
    brands = {gen_native_catalog.brand_of(c) for c in CHIPS.values()}
    for brand in ["Apple M1", "Apple M1 Pro", "Apple M1 Max", "Apple M2", "Apple M2 Pro", "Apple M3", "Apple M3 Pro",
                  "Apple M4", "Apple M4 Pro", "Apple M4 Max"]:
        assert brand in brands, brand
    m1_pro = [c for c in CHIPS.values() if gen_native_catalog.brand_of(c) == "Apple M1 Pro"]
    assert {c["perf_cores"] + c["eff_cores"] for c in m1_pro} == {8, 10}, "the core count tells the two M1 Pro apart"


def test_newer_chips_of_a_family_are_not_slower():
    """A typo in a score would turn a recommendation upside down; the ladder catches most."""
    def multi(brand):
        return max(c["multi"] for c in CHIPS.values() if gen_native_catalog.brand_of(c) == brand)
    assert multi("Apple M1") < multi("Apple M2") < multi("Apple M3") < multi("Apple M4")
    assert multi("Apple M1 Pro") < multi("Apple M1 Max") <= multi("Apple M3 Max") < multi("Apple M4 Max")


def test_the_generator_leaves_out_what_cannot_be_recommended():
    data = {"stand": "2026-11-15", "chips": {"a": {"name": "A (4)", "perf_cores": 4, "eff_cores": 0, "single": 1000, "multi": 4000, "gpu": None},
                                             "b": {"name": "B", "perf_cores": 4, "eff_cores": 0, "single": None, "multi": None, "gpu": None}},
            "devices": [{"id": "x", "vendor": "V", "name": "X \"quoted\"", "form": "desktop", "os": "Linux", "fanless": False, "source": "https://e", "verified": True,
                         "variants": [{"chip": "a", "base_price_eur": 500, "base_ram_gb": 16, "base_ssd_gb": 512,
                                       "ram_options": [{"gb": 32, "plus_eur": 100}, {"gb": 64, "plus_eur": None}], "ssd_options": []},
                                      {"chip": "a", "base_price_eur": None, "base_ram_gb": 16, "base_ssd_gb": 512, "ram_options": [], "ssd_options": []},
                                      {"chip": "b", "base_price_eur": 700, "base_ram_gb": 16, "base_ssd_gb": 512, "ram_options": [], "ssd_options": []}]},
                        {"id": "y", "vendor": "V", "name": "Y", "form": "desktop", "os": "Linux", "fanless": False, "source": "https://e", "verified": False,
                         "variants": [{"chip": "b", "base_price_eur": 700, "base_ram_gb": 16, "base_ssd_gb": 512, "ram_options": [], "ssd_options": []}]}]}
    chips, devices, skipped = gen_native_catalog.usable(data)
    assert list(chips) == ["a"] and [d["id"] for d, _v in devices] == ["x"] and len(devices[0][1]) == 1
    assert len(skipped) == 5, skipped
    text = gen_native_catalog.render(data)
    assert 'brand: "A"' in text, "the brand is the name without its bracket"
    assert "ram: &[Step { gb: 16, plus_eur: 0 }, Step { gb: 32, plus_eur: 100 }]" in text, "the base size is added, an unpriced step dropped"
    assert 'name: "X \\"quoted\\""' in text
    assert "gpu: 0 }" in text
