from pathlib import Path

from app.config import ROOT
from app.profile.builder import build_profiles
from app.profile.loader import load_raw, parse_bool, parse_number


def profiles():
    return build_profiles(load_raw(ROOT / "data_synthetic"))["profiles"]


def factor(p, code):
    return next(f for f in p["factors"] if f["factor"] == code)


def test_every_weight_has_drivers():
    for p in profiles().values():
        for f in p["factors"]:
            assert f["drivers"], f
            assert 0 <= f["weight"] <= 1


def test_loan_makes_key_rate_a_risk_and_deposit_an_opportunity():
    ps = profiles()
    a = factor(ps["U_DEMO_A"], "key_rate")
    assert a["polarity"] == "risk" and "CL_PRODUCT_LOA_MARK=1" in a["drivers"]
    b = factor(ps["U_DEMO_B"], "key_rate")
    assert "CL_PRODUCT_DEP_MARK=1" in b["drivers"] and b["polarity"] == "mixed"


def test_foreign_trade_country_maps_to_currency():
    assert factor(profiles()["U_DEMO_A"], "fx_cny")["weight"] >= 0.6


def test_counterparties_and_concentration():
    a = profiles()["U_DEMO_A"]
    top_in = [c for c in a["counterparties"] if c["direction"] == "in"][0]
    assert top_in["company_id"] == "U_G17_TRADE" and top_in["same_group"]
    assert any(c["company_id"] == "U_G17_COAL" for c in a["concentration"])
    assert any("counterparty=" in d for d in factor(a, "cmd_metals")["drivers"])


def test_group_and_large_flag():
    a = profiles()["U_DEMO_A"]
    assert a["is_large"] and a["group"]["member_count"] == 4


def test_loader_tolerates_noisy_values(tmp_path: Path):
    (tmp_path / "companies.csv").write_text(
        "SYNTHETIC_U;MAINOKVED_CODE;CL_PRODUCT_LOA_MARK;REVENUE_COMPANY\n"
        "X1;47.11;Да;16 500 000 000\nX1;47.11;да;1\nX2;;нет;\n;24.10;1;5\n",
        encoding="utf-8",
    )
    raw = load_raw(tmp_path)
    assert list(raw.companies["SYNTHETIC_U"]) == ["X1", "X2"]
    ps = build_profiles(raw)["profiles"]
    assert ps["X1"]["is_large"] and ps["X1"]["flags"]["CL_PRODUCT_LOA_MARK"] is True
    assert ps["X2"]["scale"]["revenue_rub"] is None and "REVENUE_COMPANY" in ps["X2"]["data_quality"]["missing"]
    assert parse_bool("нет") is False and parse_bool("??") is None
    assert parse_number("1,5") == 1.5
