import csv
import random
from pathlib import Path

import yaml

from app.config import ROOT

OUT = ROOT / "data_synthetic"

COLUMNS = [
    "SYNTHETIC_U",
    "ORG_TYPE",
    "OKFS_NAME",
    "COMPANY_AGE_YEARS_GRADATION",
    "ECO_MARK_LABEL",
    "MAINOKVED_CODE",
    "MAINOKVED_NAME",
    "NEGATIVEMARK",
    "AMOUNT_WORKERS_FNS_GRADATION",
    "AMOUNT_WORKERS_SPARK_GRADATION",
    "CL_TYPE",
    "CL_PRODUCT_RKO_MARK",
    "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK",
    "CL_PRODUCT_LEA_MARK",
    "CL_PRODUCT_DEP_MARK",
    "CL_PRODUCT_ZPP_MARK",
    "FTS_FLAG",
    "FTS_COUNTRY_NAME",
    "FACTORING_MARK",
    "REVENUE_COMPANY",
    "GROUP_ID",
    "CITY",
    "REGION",
]

OKVED = [
    ("24.10", "Производство чугуна, стали и ферросплавов"),
    ("05.10", "Добыча и обогащение угля и антрацита"),
    ("46.72", "Торговля оптовая металлами и металлическими рудами"),
    ("47.11", "Торговля розничная преимущественно пищевыми продуктами в неспециализированных магазинах"),
    ("10.11", "Переработка и консервирование мяса"),
    ("01.11", "Выращивание зерновых культур"),
    ("41.20", "Строительство жилых и нежилых зданий"),
    ("23.51", "Производство цемента"),
    ("49.20", "Деятельность железнодорожного транспорта: грузовые перевозки"),
    ("52.10", "Деятельность по складированию и хранению"),
    ("62.01", "Разработка компьютерного программного обеспечения"),
    ("45.11", "Торговля легковыми автомобилями и грузовыми автомобилями малой грузоподъёмности"),
    ("35.11", "Производство электроэнергии"),
    ("21.20", "Производство лекарственных препаратов и материалов"),
    ("19.20", "Производство нефтепродуктов"),
    ("10.81", "Производство сахара"),
    ("46.21", "Торговля оптовая зерном, необработанным табаком, семенами и кормами"),
    ("68.20", "Аренда и управление собственным или арендованным недвижимым имуществом"),
]
OKVED_NAME = dict(OKVED)

PLACES = [
    ("Москва", "г. Москва"),
    ("Санкт-Петербург", "г. Санкт-Петербург"),
    ("Екатеринбург", "Свердловская область"),
    ("Челябинск", "Челябинская область"),
    ("Новосибирск", "Новосибирская область"),
    ("Кемерово", "Кемеровская область"),
    ("Краснодар", "Краснодарский край"),
    ("Казань", "Республика Татарстан"),
    ("Ростов-на-Дону", "Ростовская область"),
    ("Самара", "Самарская область"),
]
WORKERS = ["100-249", "250-499", "500-999", "1000-4999", "5000+"]
COUNTRIES = ["Китай", "Казахстан", "Турция", "Индия", "ОАЭ", "Беларусь"]


def _row(cid: str, okved: str, revenue_bn: float, place: tuple[str, str], **kw) -> dict:
    row = {
        "SYNTHETIC_U": cid,
        "ORG_TYPE": "Юридическое лицо",
        "OKFS_NAME": "Частная собственность",
        "COMPANY_AGE_YEARS_GRADATION": "Более 10 лет",
        "ECO_MARK_LABEL": "Зрелость",
        "MAINOKVED_CODE": okved,
        "MAINOKVED_NAME": OKVED_NAME[okved],
        "NEGATIVEMARK": 0,
        "AMOUNT_WORKERS_FNS_GRADATION": "1000-4999",
        "AMOUNT_WORKERS_SPARK_GRADATION": "1000-4999",
        "CL_TYPE": "Действующий клиент",
        "CL_PRODUCT_RKO_MARK": 1,
        "CL_PRODUCT_GAR_MARK": 0,
        "CL_PRODUCT_LOA_MARK": 0,
        "CL_PRODUCT_LEA_MARK": 0,
        "CL_PRODUCT_DEP_MARK": 0,
        "CL_PRODUCT_ZPP_MARK": 0,
        "FTS_FLAG": 0,
        "FTS_COUNTRY_NAME": "",
        "FACTORING_MARK": 0,
        "REVENUE_COMPANY": int(revenue_bn * 1e9),
        "GROUP_ID": "",
        "CITY": place[0],
        "REGION": place[1],
    }
    row.update(kw)
    return row


def demo_companies() -> list[dict]:
    chel, msk, ekb = PLACES[3], PLACES[0], PLACES[2]
    return [
        _row(
            "U_DEMO_A",
            "24.10",
            48.2,
            chel,
            GROUP_ID="G_017",
            CL_PRODUCT_LOA_MARK=1,
            CL_PRODUCT_GAR_MARK=1,
            CL_PRODUCT_ZPP_MARK=1,
            FTS_FLAG=1,
            FTS_COUNTRY_NAME="Китай",
        ),
        _row("U_G17_COAL", "05.10", 19.5, PLACES[5], GROUP_ID="G_017", CL_PRODUCT_LOA_MARK=1),
        _row(
            "U_G17_TRADE",
            "46.72",
            27.1,
            msk,
            GROUP_ID="G_017",
            FTS_FLAG=1,
            FTS_COUNTRY_NAME="Китай",
            AMOUNT_WORKERS_FNS_GRADATION="250-499",
            AMOUNT_WORKERS_SPARK_GRADATION="250-499",
        ),
        _row(
            "U_G17_RAIL",
            "49.20",
            6.4,
            chel,
            GROUP_ID="G_017",
            CL_PRODUCT_LEA_MARK=1,
            AMOUNT_WORKERS_FNS_GRADATION="500-999",
            AMOUNT_WORKERS_SPARK_GRADATION="500-999",
        ),
        _row(
            "U_DEMO_B",
            "47.11",
            31.4,
            msk,
            GROUP_ID="G_031",
            CL_PRODUCT_DEP_MARK=1,
            CL_PRODUCT_ZPP_MARK=1,
            AMOUNT_WORKERS_FNS_GRADATION="5000+",
            AMOUNT_WORKERS_SPARK_GRADATION="5000+",
            ECO_MARK_LABEL="Рост",
        ),
        _row("U_G31_MEAT", "10.11", 12.8, PLACES[6], GROUP_ID="G_031", CL_PRODUCT_LOA_MARK=1),
        _row(
            "U_DEMO_C",
            "41.20",
            22.7,
            ekb,
            GROUP_ID="G_042",
            CL_TYPE="Потенциальный клиент",
            CL_PRODUCT_RKO_MARK=0,
            AMOUNT_WORKERS_FNS_GRADATION="500-999",
            AMOUNT_WORKERS_SPARK_GRADATION="",
            ECO_MARK_LABEL="Рост",
            COMPANY_AGE_YEARS_GRADATION="5-10 лет",
        ),
        _row(
            "U_G42_CEMENT",
            "23.51",
            9.3,
            ekb,
            GROUP_ID="G_042",
            CL_TYPE="Потенциальный клиент",
            CL_PRODUCT_RKO_MARK=0,
        ),
        _row(
            "U_G42_RENT",
            "68.20",
            2.1,
            ekb,
            GROUP_ID="G_042",
            CL_TYPE="Потенциальный клиент",
            CL_PRODUCT_RKO_MARK=0,
            AMOUNT_WORKERS_FNS_GRADATION="100-249",
            AMOUNT_WORKERS_SPARK_GRADATION="100-249",
        ),
        _row("U_TRD_01", "46.72", 17.6, PLACES[1], FTS_FLAG=1, FTS_COUNTRY_NAME="Турция"),
        _row("U_SUP_SUGAR", "10.81", 15.9, PLACES[8], CL_PRODUCT_LOA_MARK=1),
        _row(
            "U_SUP_GRAIN",
            "46.21",
            24.4,
            PLACES[6],
            FTS_FLAG=1,
            FTS_COUNTRY_NAME="Турция",
            CL_PRODUCT_LOA_MARK=1,
            FACTORING_MARK=1,
        ),
        _row("U_LOG_01", "52.10", 7.7, msk, CL_PRODUCT_LEA_MARK=1),
    ]


def background_companies(rng: random.Random, n: int = 50) -> list[dict]:
    rows = []
    for i in range(1, n + 1):
        okved, _ = rng.choice(OKVED)
        place = rng.choice(PLACES)
        revenue = round(rng.choice([rng.uniform(3, 15), rng.uniform(15, 120)]), 1)
        fts = rng.random() < 0.3
        workers = rng.choice(WORKERS)
        rows.append(
            _row(
                f"U_{i:04d}",
                okved,
                revenue,
                place,
                CL_TYPE=rng.choice(["Действующий клиент", "Действующий клиент", "Потенциальный клиент"]),
                CL_PRODUCT_LOA_MARK=int(rng.random() < 0.5),
                CL_PRODUCT_GAR_MARK=int(rng.random() < 0.3),
                CL_PRODUCT_LEA_MARK=int(rng.random() < 0.25),
                CL_PRODUCT_DEP_MARK=int(rng.random() < 0.35),
                CL_PRODUCT_ZPP_MARK=int(rng.random() < 0.5),
                FACTORING_MARK=int(rng.random() < 0.2),
                FTS_FLAG=int(fts),
                FTS_COUNTRY_NAME=rng.choice(COUNTRIES) if fts else "",
                AMOUNT_WORKERS_FNS_GRADATION=workers,
                AMOUNT_WORKERS_SPARK_GRADATION=workers if rng.random() < 0.9 else "",
                NEGATIVEMARK=int(rng.random() < 0.05),
                ECO_MARK_LABEL=rng.choice(["Зрелость", "Рост", "Стабильность"]),
                GROUP_ID=f"G_{100 + i // 4}" if rng.random() < 0.4 else "",
                REVENUE_COMPANY=int(revenue * 1e9) if rng.random() < 0.95 else "",
            )
        )
    return rows


def payments(rng: random.Random, companies: list[dict]) -> list[dict]:
    fixed = [
        ("U_G17_TRADE", "U_DEMO_A", 16.4e9, 412),
        ("U_TRD_01", "U_DEMO_A", 9.6e9, 233),
        ("U_0003", "U_DEMO_A", 4.1e9, 120),
        ("U_0011", "U_DEMO_A", 3.3e9, 98),
        ("U_DEMO_A", "U_G17_COAL", 11.8e9, 520),
        ("U_DEMO_A", "U_G17_RAIL", 4.4e9, 310),
        ("U_DEMO_A", "U_LOG_01", 1.9e9, 140),
        ("U_DEMO_A", "U_0007", 2.6e9, 88),
        ("U_DEMO_B", "U_G31_MEAT", 6.2e9, 1210),
        ("U_DEMO_B", "U_SUP_SUGAR", 3.1e9, 640),
        ("U_DEMO_B", "U_SUP_GRAIN", 2.4e9, 380),
        ("U_DEMO_B", "U_LOG_01", 1.7e9, 900),
        ("U_DEMO_B", "U_0015", 1.2e9, 410),
        ("U_DEMO_C", "U_G42_CEMENT", 4.9e9, 260),
        ("U_DEMO_C", "U_0009", 2.2e9, 120),
        ("U_DEMO_C", "U_0030", 1.4e9, 95),
    ]
    rows = [{"BENEFICIARY": b, "PAYER": p, "PAY_AMT": int(a), "PAY_CNT": c} for p, b, a, c in fixed]
    ids = [c["SYNTHETIC_U"] for c in companies if c["SYNTHETIC_U"].startswith("U_0")]
    for _ in range(260):
        p, b = rng.sample(ids, 2)
        rows.append(
            {
                "BENEFICIARY": b,
                "PAYER": p,
                "PAY_AMT": int(rng.uniform(0.01, 2.5) * 1e9),
                "PAY_CNT": rng.randint(1, 400),
            }
        )
    return rows


def persons(rng: random.Random, companies: list[dict]) -> list[dict]:
    rows = [
        {
            "SYNTHETIC_F": "F_0001",
            "CITY_NAME": "Екатеринбург",
            "RC_NAME": "Свердловская область",
            "SYNTHETIC_U": "U_DEMO_C",
        },
        {
            "SYNTHETIC_F": "F_0001",
            "CITY_NAME": "Екатеринбург",
            "RC_NAME": "Свердловская область",
            "SYNTHETIC_U": "U_0009",
        },
        {
            "SYNTHETIC_F": "F_0002",
            "CITY_NAME": "Челябинск",
            "RC_NAME": "Челябинская область",
            "SYNTHETIC_U": "U_DEMO_A",
        },
        {
            "SYNTHETIC_F": "F_0002",
            "CITY_NAME": "Челябинск",
            "RC_NAME": "Челябинская область",
            "SYNTHETIC_U": "U_LOG_01",
        },
    ]
    ids = [c["SYNTHETIC_U"] for c in companies]
    for i in range(3, 160):
        city, region = rng.choice(PLACES)
        for cid in rng.sample(ids, rng.choice([1, 1, 1, 2])):
            rows.append(
                {"SYNTHETIC_F": f"F_{i:04d}", "CITY_NAME": city, "RC_NAME": region, "SYNTHETIC_U": cid}
            )
    return rows


USERS = [
    {
        "login": "cfo_a",
        "display_name": "Ирина Соколова",
        "company_id": "U_DEMO_A",
        "role": "cfo",
        "is_admin": True,
    },
    {"login": "treasury_a", "display_name": "Павел Ким", "company_id": "U_DEMO_A", "role": "treasurer"},
    {
        "login": "accountant_a",
        "display_name": "Елена Громова",
        "company_id": "U_DEMO_A",
        "role": "accountant",
    },
    {"login": "ceo_b", "display_name": "Андрей Лебедев", "company_id": "U_DEMO_B", "role": "ceo"},
    {"login": "cfo_b", "display_name": "Мария Орлова", "company_id": "U_DEMO_B", "role": "cfo"},
    {"login": "cfo_c", "display_name": "Тимур Валиев", "company_id": "U_DEMO_C", "role": "cfo"},
    {"login": "owner_c", "display_name": "Сергей Абрамов", "company_id": "U_DEMO_C", "role": "owner"},
]


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


def generate(out: Path = OUT, seed: int = 2026) -> None:
    rng = random.Random(seed)
    out.mkdir(parents=True, exist_ok=True)
    companies = demo_companies() + background_companies(rng)
    _write_csv(out / "df_Uid_data_hack.csv", companies, COLUMNS)
    _write_csv(
        out / "df_Fid_Uid_link_hack.csv",
        persons(rng, companies),
        ["SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"],
    )
    _write_csv(
        out / "df_payments_hack.csv", payments(rng, companies), ["BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"]
    )
    with open(out / "users.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump({"users": USERS}, f, allow_unicode=True, sort_keys=False)


if __name__ == "__main__":
    generate()
    print(f"synthetic data written to {OUT}")
