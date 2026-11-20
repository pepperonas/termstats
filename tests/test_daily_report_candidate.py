"""The daily mail's verdict on a wished-for machine (examples/daily-report.example.py)."""
import importlib.machinery
import importlib.util
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("daily_report", str(ROOT / "examples" / "daily-report.example.py"))
dr = importlib.util.module_from_spec(importlib.util.spec_from_loader("daily_report", _loader))
_loader.exec_module(dr)
CATALOGUE = json.loads((ROOT / "catalog" / "hardware.json").read_text(encoding="utf-8"))


def report(ram=(25.3, 29.9, 41.4), ssd=(1078, 1293, 1724), multi=(15289, 19459, 28041), gpu=(59735, 79647, 119470), ready=False):
    best = {"device": "macbook-pro-14", "vendor": "Apple", "name": 'MacBook Pro 14" (Liquid Retina XDR)',
            "chip": "Apple M5 Pro (15 CPU / 16 GPU)", "ram_gb": 48, "ssd_gb": 2048, "price_eur": 4109}
    tiers = [{"tier": name, "need": {"ram_gb": ram[i], "ssd_gb": ssd[i], "multi": multi[i], "gpu": gpu[i]},
              "laptop": best if i == 2 else None} for i, name in enumerate(("just enough", "fits", "with reserve"))]
    return {"ready": ready, "tiers": tiers, "history": {"days": 5},
            "machine": {"chip": "Apple M1 Pro", "ncpu": 10, "multi": 13899, "short": ["memory"]}}


def wish(**over):
    return dr.configure(CATALOGUE, {"device": "macbook-pro-16", "chip": "apple-m5-pro-18c", "ram_gb": 64, "ssd_gb": 2048, **over})


def test_the_price_is_base_plus_both_options():
    spec = wish()
    assert spec["price_eur"] == 3399 + 1100 + 550
    assert spec["multi"] == CATALOGUE["chips"]["apple-m5-pro-18c"]["multi"]


def test_an_unknown_configuration_is_no_guess():
    assert wish(ram_gb=32) is None
    assert dr.configure(CATALOGUE, {"device": "macbook-pro-16", "chip": "apple-m6-12c", "ram_gb": 24, "ssd_gb": 1024}) is None
    assert dr.configure(CATALOGUE, {"device": "nope", "chip": "apple-m5-pro-18c", "ram_gb": 64, "ssd_gb": 2048}) is None


@pytest.mark.parametrize("have, expected", [(30, 100.0), (41, 100.0), (15, 50.0), (82, 75.0)])
def test_dimension_score(have, expected):
    assert dr.dimension_score(have, 30, 41 if have != 82 else 41) == pytest.approx(expected, abs=0.5)


def test_more_than_needed_costs_less_than_too_little():
    assert dr.dimension_score(80, 30, 40) > dr.dimension_score(20, 30, 40)   # twice the reserve beats two thirds of the need
    assert dr.dimension_score(1000, 30, 40) >= 100 * (1 - dr.OVERSIZE_PENALTY)


def test_a_roomy_but_dearer_machine_is_worth_it_only_for_its_extras():
    a = dr.assess(wish(), report())
    assert a["verdict"] == "ja, mit Aufpreis"
    assert a["extra"] == 5049 - 4109
    assert 85 <= a["percent"] < 100              # 64 GB is far beyond the need: a mild discount
    assert a["cheaper"]["ram_gb"] == 48 and a["cheaper"]["price_eur"] == 4609


def test_a_machine_short_of_the_need_is_a_no():
    a = dr.assess(wish(ram_gb=24), report())
    assert a["verdict"] == "nein" and "Arbeitsspeicher" in a["worth"]
    assert a["percent"] < 90


def test_meeting_only_just_enough_is_not_recommended():
    a = dr.assess(wish(ram_gb=24), report(ram=(20, 26, 40)))
    assert a["verdict"] == "eher nicht"


def test_the_cheapest_roomy_machine_itself_is_a_plain_yes():
    spec = dr.configure(CATALOGUE, {"device": "macbook-pro-14", "chip": "apple-m5-pro-15c", "ram_gb": 48, "ssd_gb": 2048})
    a = dr.assess(spec, report())
    assert a["verdict"] == "ja" and a["percent"] > 90


def test_the_text_names_the_extras_and_the_cheaper_step():
    spec = wish()
    spec["now_gpu"] = dr.current_gpu(CATALOGUE, {"chip": "Apple M1 Pro", "ncpu": 10})
    a = dr.assess(spec, report())
    a["extras"] = dr.extras(CATALOGUE, spec, a["best"])
    text = dr.candidate_plain(spec, a)
    assert "Übereinstimmung" in text and "lohnt sich: ja, mit Aufpreis" in text
    assert "16,2 statt 14,2 Zoll" in text and "64 statt 48 GB" in text
    assert "48 GB und 2 TB" in text and "440 € weniger" in text
    assert "Vorläufig" in text
    assert "% des jetzigen Chips" in text.split("Grafik:")[1].splitlines()[0]


def test_no_file_no_section(tmp_path, monkeypatch):
    monkeypatch.setenv("TERMSTATS_REPORT_CANDIDATE", str(tmp_path / "missing.json"))
    assert dr.candidate(report()) is None
    (tmp_path / "bad.json").write_text('{"device": "x"}')
    monkeypatch.setenv("TERMSTATS_REPORT_CANDIDATE", str(tmp_path / "bad.json"))
    assert dr.candidate(report()) is None
