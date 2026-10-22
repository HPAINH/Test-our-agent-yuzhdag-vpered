import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

COMPANY_KEY = "SYNTHETIC_U"
PERSON_KEY = "SYNTHETIC_F"
BOOL_COLUMNS = [
    "NEGATIVEMARK",
    "CL_PRODUCT_RKO_MARK",
    "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK",
    "CL_PRODUCT_LEA_MARK",
    "CL_PRODUCT_DEP_MARK",
    "CL_PRODUCT_ZPP_MARK",
    "FTS_FLAG",
    "FACTORING_MARK",
]
REQUIRED_COMPANY = [COMPANY_KEY, "MAINOKVED_CODE"]
TRUE_VALUES = {"1", "1.0", "true", "t", "yes", "y", "да", "д", "есть", "+"}
FALSE_VALUES = {"0", "0.0", "false", "f", "no", "n", "нет", "н", "-", ""}


@dataclass
class LoadReport:
    files: dict = field(default_factory=dict)
    rows: dict = field(default_factory=dict)
    missing_columns: dict = field(default_factory=dict)
    nulls: dict = field(default_factory=dict)
    invalid: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class RawData:
    companies: pd.DataFrame
    persons: pd.DataFrame
    payments: pd.DataFrame
    report: LoadReport


def read_csv_any(path: Path) -> pd.DataFrame:
    last: Exception | None = None
    for enc in ("utf-8-sig", "cp1251"):
        try:
            return pd.read_csv(
                path, sep=None, engine="python", dtype=str, encoding=enc, keep_default_na=False
            )
        except Exception as exc:
            last = exc
    raise ValueError(f"cannot read {path}: {last}")


def parse_bool(value) -> bool | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in TRUE_VALUES:
        return True
    if s in FALSE_VALUES:
        return False if s else None
    return None


def parse_number(value) -> float | None:
    if value is None:
        return None
    s = re.sub(r"[\s ]", "", str(value)).replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _classify(df: pd.DataFrame) -> str | None:
    cols = {c.strip().upper() for c in df.columns}
    if {"PAYER", "BENEFICIARY"} <= cols:
        return "payments"
    if PERSON_KEY in cols and COMPANY_KEY in cols:
        return "persons"
    if COMPANY_KEY in cols and "MAINOKVED_CODE" in cols:
        return "companies"
    return None


def dedupe_companies(companies: pd.DataFrame, report: LoadReport) -> pd.DataFrame:
    counts = companies[COMPANY_KEY].value_counts()
    dup_ids = counts[counts > 1].index
    if not len(dup_ids):
        return companies
    keep, exact, artifacts, conflicts = [], [], [], []
    for cid in dup_ids:
        rows = companies[companies[COMPANY_KEY] == cid]
        values = rows.drop(columns=[COMPANY_KEY]).astype(str).apply(lambda c: c.str.strip())
        filled = (values != "").mean(axis=1)
        if len(values.drop_duplicates()) == 1:
            exact.append(cid)
            keep.append(rows.index[0])
        elif filled.max() < 0.5:
            artifacts.append(cid)
        else:
            conflicts.append(cid)
            keep.append(filled.idxmax())
    unique = companies[~companies[COMPANY_KEY].isin(dup_ids)]
    report.invalid["duplicate_ids"] = {
        "exact": exact,
        "anonymization_artifacts": artifacts,
        "conflicting_kept_most_complete": conflicts,
    }
    if artifacts:
        report.warnings.append(
            f"технические идентификаторы с пустыми строками исключены: {', '.join(map(str, artifacts))}"
        )
    return pd.concat([unique, companies.loc[keep]]).sort_index()


def load_raw(data_dir: Path) -> RawData:
    report = LoadReport()
    found: dict[str, pd.DataFrame] = {}
    for path in sorted(data_dir.glob("*.csv")):
        try:
            df = read_csv_any(path)
        except ValueError as exc:
            report.warnings.append(str(exc))
            continue
        df.columns = [c.strip().upper() for c in df.columns]
        kind = _classify(df)
        if kind is None:
            report.warnings.append(f"{path.name}: unknown file layout, skipped")
            continue
        if kind in found:
            found[kind] = pd.concat([found[kind], df], ignore_index=True)
        else:
            found[kind] = df
        report.files.setdefault(kind, []).append(path.name)

    companies = found.get("companies", pd.DataFrame(columns=REQUIRED_COMPANY))
    persons = found.get("persons", pd.DataFrame(columns=[PERSON_KEY, COMPANY_KEY]))
    payments = found.get("payments", pd.DataFrame(columns=["PAYER", "BENEFICIARY", "PAY_AMT", "PAY_CNT"]))

    for name, df in (("companies", companies), ("persons", persons), ("payments", payments)):
        report.rows[name] = int(len(df))
        report.nulls[name] = {c: int((df[c].astype(str).str.strip() == "").sum()) for c in df.columns}

    report.missing_columns["companies"] = [
        c for c in REQUIRED_COMPANY + BOOL_COLUMNS if c not in companies.columns
    ]
    before = len(companies)
    companies = companies[companies.get(COMPANY_KEY, pd.Series(dtype=str)).astype(str).str.strip() != ""]
    companies = dedupe_companies(companies, report)
    if len(companies) != before:
        report.invalid["companies_dropped"] = before - len(companies)

    for col in ("PAY_AMT", "PAY_CNT"):
        if col in payments.columns:
            parsed = payments[col].map(parse_number)
            report.invalid[f"payments_{col}"] = int(parsed.isna().sum())
            payments[col] = parsed.fillna(0)
    if {"PAYER", "BENEFICIARY"} <= set(payments.columns) and len(payments):
        own = payments["PAYER"].astype(str).str.strip() == payments["BENEFICIARY"].astype(str).str.strip()
        total = float(payments["PAY_AMT"].sum()) if "PAY_AMT" in payments.columns else 0.0
        own_amt = float(payments.loc[own, "PAY_AMT"].sum()) if "PAY_AMT" in payments.columns else 0.0
        report.invalid["self_transfers"] = {
            "rows": int(own.sum()),
            "amount_share": round(own_amt / total, 4) if total else 0.0,
        }
        payments = payments[~own].reset_index(drop=True)
    if len(payments) and len(companies):
        known = set(companies[COMPANY_KEY].astype(str))
        unknown = (~payments["PAYER"].isin(known)) | (~payments["BENEFICIARY"].isin(known))
        report.invalid["payments_with_unknown_counterparty"] = int(unknown.sum())

    return RawData(
        companies=companies.reset_index(drop=True), persons=persons, payments=payments, report=report
    )
