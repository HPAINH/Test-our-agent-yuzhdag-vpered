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
    companies = companies.drop_duplicates(subset=[COMPANY_KEY], keep="first")
    if len(companies) != before:
        report.invalid["companies_dropped"] = before - len(companies)

    for col in ("PAY_AMT", "PAY_CNT"):
        if col in payments.columns:
            parsed = payments[col].map(parse_number)
            report.invalid[f"payments_{col}"] = int(parsed.isna().sum())
            payments[col] = parsed.fillna(0)

    return RawData(
        companies=companies.reset_index(drop=True), persons=persons, payments=payments, report=report
    )
