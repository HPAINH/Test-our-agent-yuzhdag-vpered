from collections import defaultdict

import pandas as pd

from app.config import load_factor_rules, load_yaml
from app.profile.loader import BOOL_COLUMNS, COMPANY_KEY, PERSON_KEY, RawData, parse_bool, parse_number

PRODUCT_KEYS = {
    "CL_PRODUCT_RKO_MARK": "rko",
    "CL_PRODUCT_GAR_MARK": "guarantee",
    "CL_PRODUCT_LOA_MARK": "loan",
    "CL_PRODUCT_LEA_MARK": "leasing",
    "CL_PRODUCT_DEP_MARK": "deposit",
    "CL_PRODUCT_ZPP_MARK": "payroll",
    "FACTORING_MARK": "factoring",
}


def _clean(v) -> str:
    return "" if v is None else str(v).strip()


def industry_for(okved: str, industries: dict) -> dict:
    code = _clean(okved)
    for prefix in sorted(industries, key=len, reverse=True):
        if code.startswith(prefix):
            return {"prefix": prefix, **industries[prefix]}
    return {"prefix": code[:2], "sector": "other", "name": "Прочие отрасли", "factors": {}}


def workers_lower(grad: str) -> int | None:
    g = _clean(grad).replace(" ", "")
    if not g:
        return None
    digits = "".join(ch if ch.isdigit() else " " for ch in g).split()
    return int(digits[0]) if digits else None


REVENUE_PLACEHOLDER_MAX = 1000


def client_status(value: str) -> str:
    v = _clean(value).lower()
    if not v:
        return "unknown"
    if "бывш" in v or "закры" in v or "close" in v:
        return "closed"
    if "потенц" in v or "prospect" in v:
        return "prospect"
    if "действ" in v or v == "client":
        return "client"
    return "unknown"


def normalize_company(row: dict, rules: dict) -> dict:
    flags = {col: parse_bool(row.get(col)) for col in BOOL_COLUMNS}
    revenue = parse_number(row.get("REVENUE_COMPANY"))
    revenue_placeholder = revenue is not None and revenue <= REVENUE_PLACEHOLDER_MAX
    if revenue_placeholder:
        revenue = None
    industry = industry_for(row.get("MAINOKVED_CODE", ""), rules.get("industries", {}))
    workers = _clean(row.get("AMOUNT_WORKERS_FNS_GRADATION")) or _clean(
        row.get("AMOUNT_WORKERS_SPARK_GRADATION")
    )
    missing = [k for k, v in row.items() if _clean(v) == "" and k not in ("FTS_COUNTRY_NAME", "GROUP_ID")]
    if revenue_placeholder:
        missing.append("REVENUE_COMPANY")
    return {
        "company_id": _clean(row.get(COMPANY_KEY)),
        "industry": {
            "okved": _clean(row.get("MAINOKVED_CODE")),
            "name": _clean(row.get("MAINOKVED_NAME")) or industry.get("name"),
            "sector": industry.get("sector"),
            "sector_name": industry.get("name"),
            "prefix": industry.get("prefix"),
        },
        "org": {
            "type": _clean(row.get("ORG_TYPE")),
            "ownership": _clean(row.get("OKFS_NAME")),
            "age": _clean(row.get("COMPANY_AGE_YEARS_GRADATION")),
            "stage": _clean(row.get("ECO_MARK_LABEL")),
            "client_type": _clean(row.get("CL_TYPE")),
            "client_status": client_status(row.get("CL_TYPE")),
            "negative_mark": flags.get("NEGATIVEMARK"),
        },
        "scale": {
            "revenue_rub": revenue,
            "workers": workers or None,
            "workers_lower": workers_lower(workers),
        },
        "geo": {"region": _clean(row.get("REGION")), "city": _clean(row.get("CITY"))},
        "products": {PRODUCT_KEYS[k]: flags.get(k) for k in PRODUCT_KEYS},
        "flags": flags,
        "foreign_trade": {
            "flag": bool(flags.get("FTS_FLAG")),
            "country": _clean(row.get("FTS_COUNTRY_NAME")) or None,
        },
        "group_id": _clean(row.get("GROUP_ID")) or None,
        "data_quality": {
            "missing": missing,
            "revenue_placeholder": revenue_placeholder,
        },
    }


def _add(acc: dict, factor: str, weight: float, polarity: str, driver: str) -> None:
    item = acc.setdefault(factor, {"weight": 0.0, "polarities": set(), "drivers": []})
    item["weight"] += weight
    item["polarities"].add(polarity)
    if driver not in item["drivers"]:
        item["drivers"].append(driver)


def _polarity(pols: set) -> str:
    if pols == {"risk"}:
        return "risk"
    if pols == {"opportunity"}:
        return "opportunity"
    return "mixed"


def factor_vector(profile: dict, counterparties: list[dict], rules: dict, all_profiles: dict) -> list[dict]:
    acc: dict = {}
    for factor, (w, pol) in rules.get("base", {}).items():
        _add(acc, factor, w, pol, "base")
    for factor, (w, pol) in (
        (rules.get("industries", {}).get(profile["industry"]["prefix"], {}) or {}).get("factors", {}).items()
    ):
        _add(acc, factor, w, pol, f"MAINOKVED_CODE={profile['industry']['okved']}")
    flags = profile["flags"]
    for rule in rules.get("feature_rules", []):
        if flags.get(rule["feature"]) is rule["value"]:
            _add(acc, rule["factor"], rule["add"], rule["polarity"], f"{rule['feature']}=1")
    wr = rules.get("workers_rules", {})
    wl = profile["scale"].get("workers_lower")
    if wl is not None and wl >= wr.get("min_large", 1000):
        _add(acc, "labor", wr.get("labor_add", 0.2), "risk", f"WORKERS={profile['scale']['workers']}")
        _add(acc, "inflation", wr.get("inflation_add", 0.1), "risk", f"WORKERS={profile['scale']['workers']}")
    if profile["foreign_trade"]["flag"]:
        cur_map = rules.get("fts_currency", {})
        country = profile["foreign_trade"]["country"] or ""
        factor = cur_map.get(country, cur_map.get("default", "fx_usd"))
        _add(
            acc,
            factor,
            rules.get("fts_currency_weight", 0.6),
            "mixed",
            f"FTS_COUNTRY_NAME={country or 'н/д'}",
        )
    cp_rules = rules.get("counterparty", {})
    for cp in counterparties:
        if cp["share"] < cp_rules.get("min_share", 0.15):
            continue
        cp_profile = all_profiles.get(cp["company_id"])
        if not cp_profile:
            continue
        ind = rules.get("industries", {}).get(cp_profile["industry"]["prefix"], {})
        for factor, (w, pol) in (ind.get("factors") or {}).items():
            _add(
                acc,
                factor,
                w * cp["share"] * cp_rules.get("factor_scale", 0.5),
                pol,
                f"counterparty={cp['company_id']}",
            )
    out = []
    for factor, item in acc.items():
        out.append(
            {
                "factor": factor,
                "weight": round(min(1.0, item["weight"]), 3),
                "polarity": _polarity(item["polarities"]),
                "drivers": item["drivers"],
            }
        )
    return sorted(out, key=lambda x: -x["weight"])


def build_graph(raw: RawData, ids: set[str], rules: dict) -> tuple[dict, list[dict]]:
    cp_rules = rules.get("counterparty", {})
    top_n = cp_rules.get("top_n", 10)
    pay = raw.payments
    by_company: dict[str, list[dict]] = defaultdict(list)
    links: list[dict] = []
    if len(pay):
        for direction, own, other in (("in", "BENEFICIARY", "PAYER"), ("out", "PAYER", "BENEFICIARY")):
            grouped = (
                pay.groupby([own, other])
                .agg(amount=("PAY_AMT", "sum"), count=("PAY_CNT", "sum"))
                .reset_index()
            )
            totals = grouped.groupby(own)["amount"].sum().to_dict()
            for row in grouped.itertuples(index=False):
                company, cp, amount, count = getattr(row, own), getattr(row, other), row.amount, row.count
                total = totals.get(company) or 0
                share = float(amount) / total if total else 0.0
                by_company[company].append(
                    {
                        "company_id": cp,
                        "direction": direction,
                        "amount": float(amount),
                        "count": int(count),
                        "share": round(share, 4),
                    }
                )
                if direction == "out":
                    links.append(
                        {
                            "src": company,
                            "dst": cp,
                            "kind": "payment",
                            "amount": float(amount),
                            "count": int(count),
                        }
                    )
    for company in by_company:
        by_company[company] = sorted(by_company[company], key=lambda x: -x["amount"])
    persons = raw.persons
    if len(persons) and PERSON_KEY in persons.columns:
        for person, grp in persons.groupby(PERSON_KEY):
            companies = sorted(set(grp[COMPANY_KEY].astype(str)))
            for i, a in enumerate(companies):
                for b in companies[i + 1 :]:
                    links.append(
                        {"src": a, "dst": b, "kind": "person", "amount": 0, "count": 1, "via": person}
                    )
    result = {}
    for cid in ids:
        items = by_company.get(cid, [])
        ins = [x for x in items if x["direction"] == "in"][:top_n]
        outs = [x for x in items if x["direction"] == "out"][:top_n]
        result[cid] = {"in": ins, "out": outs}
    return result, links


def build_profiles(raw: RawData) -> dict:
    rules = load_factor_rules()
    large = rules.get("large_business_revenue_rub", 15e9)
    rows = raw.companies.to_dict(orient="records")
    profiles = {}
    for row in rows:
        p = normalize_company(row, rules)
        p["raw"] = row
        profiles[p["company_id"]] = p

    by_okved: dict[str, list] = defaultdict(list)
    for p in profiles.values():
        if p["scale"]["revenue_rub"]:
            by_okved[p["industry"]["okved"]].append((p["scale"]["revenue_rub"], p["company_id"]))
    for items in by_okved.values():
        items.sort(reverse=True)
        total = sum(r for r, _ in items)
        for rank, (rev, cid) in enumerate(items, start=1):
            profiles[cid]["scale"]["rank_in_okved"] = rank
            profiles[cid]["scale"]["peers_in_okved"] = len(items)
            profiles[cid]["scale"]["share_in_okved_dataset"] = round(rev / total, 4) if total else None

    groups: dict[str, list] = defaultdict(list)
    for p in profiles.values():
        if p["group_id"]:
            groups[p["group_id"]].append(p["company_id"])

    graph, links = build_graph(raw, set(profiles), rules)
    implicit: dict[str, set] = defaultdict(set)
    for link in links:
        if link["kind"] == "person":
            implicit[link["src"]].add((link["dst"], link["via"]))
            implicit[link["dst"]].add((link["src"], link["via"]))

    threshold = rules.get("counterparty", {}).get("concentration_threshold", 0.3)
    for cid, p in profiles.items():
        members = groups.get(p["group_id"], []) if p["group_id"] else []
        p["group"] = {
            "group_id": p["group_id"],
            "members": members,
            "member_count": len(members),
            "revenue_rub": sum(profiles[m]["scale"]["revenue_rub"] or 0 for m in members)
            if members
            else None,
            "sectors": sorted({profiles[m]["industry"]["sector_name"] for m in members}) if members else [],
            "member_industries": [
                {
                    "company_id": m,
                    "okved": profiles[m]["industry"]["okved"],
                    "okved_name": profiles[m]["industry"]["name"],
                }
                for m in members
                if m != cid
            ],
        }
        own_rev = p["scale"]["revenue_rub"]
        group_rev = p["group"]["revenue_rub"]
        basis_rev, basis = (group_rev, "group") if group_rev else (own_rev, "company")
        p["is_large"] = bool(basis_rev is not None and basis_rev > large)
        p["scale"]["large_basis"] = basis
        p["scale"]["large_basis_revenue_rub"] = basis_rev
        same_group = set(members)
        p["implicit_links"] = [
            {"company_id": other, "via_person": via}
            for other, via in sorted(implicit.get(cid, set()))
            if other not in same_group and other in profiles
        ]
        cps = []
        for direction in ("in", "out"):
            for cp in graph[cid][direction]:
                other = profiles.get(cp["company_id"])
                cps.append(
                    {
                        **cp,
                        "okved": other["industry"]["okved"] if other else None,
                        "okved_name": other["industry"]["name"] if other else None,
                        "sector": other["industry"]["sector"] if other else None,
                        "region": other["geo"]["region"] if other else None,
                        "same_group": cp["company_id"] in same_group,
                        "known": other is not None,
                    }
                )
        p["counterparties"] = cps
        conc = [c for c in cps if c["share"] >= threshold]
        p["concentration"] = [
            {"company_id": c["company_id"], "direction": c["direction"], "share": c["share"]} for c in conc
        ]
    for p in profiles.values():
        p["factors"] = factor_vector(p, p["counterparties"], rules, profiles)
    return {"profiles": profiles, "links": links, "report": raw.report.as_dict()}


def role_catalog() -> dict:
    return load_yaml("roles.yaml")["roles"]


def factor_names() -> dict:
    return {k: v["name"] for k, v in load_factor_rules().get("catalog", {}).items()}


def profile_frame(profiles: dict) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"company_id": cid, "okved": p["industry"]["okved"], "revenue": p["scale"]["revenue_rub"]}
            for cid, p in profiles.items()
        ]
    )
