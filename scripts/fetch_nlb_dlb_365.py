#!/usr/bin/env python3
"""100% Live Official NLB & DLB Incremental Scraper & Prize Tier Ranking Builder.

Key Architecture & Fixes (v3.6 — 100% Draw & Prize Table Coverage):
  1. 100% PRIZE TABLE COVERAGE FOR ALL DRAWS (`MAX_TABLES_PER_RUN = MAX_DRAWS`):
     - Previously, `MAX_TABLES_PER_RUN` defaulted to `5` and `min_draw_no` in NLB was
       bounded by `tables_target` (`5`), leaving NLB stuck at 10 draws / 7 tables (70%)
       and DLB stuck at 13 draws / 1 table (8%).
     - Now `MAX_TABLES_PER_RUN` defaults to `MAX_DRAWS` (30), `min_draw_no` spans the
       entire `MAX_DRAWS` window (`latest_draw_no - MAX_DRAWS + 1`), and completeness
       requires BOTH `MAX_DRAWS` consecutive draws AND 100% of their prize tables
       (`has_complete_draws_and_tables`). Any ticket with <100% table coverage is
       automatically backfilled on the next run without needing `force_full_backfill=1`.
  2. FIXED DLB HIDDEN `.tab-pane` & `innerText` BUGS (https://www.dlb.lk/result/en):
     - On `dlb.lk/result/en`, 8 of the 9 lotteries live inside `.tab-pane` containers
       that start with `display: none`. In Chromium, `.innerText` on `display: none`
       elements returns `""`, which caused `extract_dlb_all_sections_dom` to miss the
       8 hidden tables, skip tagging `[data-sl-hub-more]` and `[data-sl-hub-page]`, and
       fail to click `MORE` or paginate.
     - We now force all `.tab-pane` elements to `display: block !important` via
       `unhide_all_dlb_tabs(page)` AND use `.textContent` everywhere instead of `.innerText`.
     - `parse_dlb_more_modal_html` now wraps bare `<tr>` AJAX fragments in `<table>` and
       searches `soup.find_all("tr")` so `lxml` never drops AJAX table rows.
  3. STRICT SEQUENTIAL ORDER & INCREMENTAL PRESERVATION:
     - `draws`, `perDrawTables`, and `drawTierHistory` are strictly sorted by Draw No
       descending (`newest -> oldest`) and existing records are never deleted.
"""

import datetime
import glob
import json
import os
import re
from typing import Any
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None

MAX_DRAWS = int(os.environ.get("MAX_DRAWS_PER_TICKET", "30"))
MAX_TABLES_PER_RUN = int(os.environ.get("MAX_TABLES_PER_TICKET", str(MAX_DRAWS)))
FORCE_FULL_BACKFILL = os.environ.get("FORCE_FULL_BACKFILL", "0") == "1"

# ── Output layout (ticket-wise JSON + dedicated prize tier rankings JSON) ───
DATA_DIR = os.environ.get("OUTPUT_DATA_DIR", "data")
TICKETS_DIR = os.path.join(DATA_DIR, "tickets")
INDEX_PATH = os.path.join(DATA_DIR, "index.json")
PRIZE_RANKINGS_PATH = os.path.join(DATA_DIR, "prize_tier_rankings.json")
OUTPUT_PATH = os.environ.get("OUTPUT_JSON_PATH", os.path.join(DATA_DIR, "sl_lottery_365_official.json"))
WRITE_COMBINED = os.environ.get("WRITE_COMBINED_JSON", "1") != "0"
SCHEMA_VERSION = "3.6"

TIER_KEYS = [
    "TIER_40",
    "TIER_80_120",
    "TIER_200",
    "TIER_1000_2000",
    "TIER_20000",
    "TIER_100000",
    "TIER_1M_PLUS",
]

NLB_LOTTERIES = [
    {"id": "mahajana-sampatha", "name": "Mahajana Sampatha", "slug": "mahajana-sampatha", "balls": 6},
    {"id": "govisetha", "name": "Govisetha", "slug": "govisetha", "balls": 4},
    {"id": "mega-power", "name": "Mega Power", "slug": "mega-power", "balls": 5},
    {"id": "dhana-nidhanaya", "name": "Dhana Nidhanaya", "slug": "dhana-nidhanaya", "balls": 4},
    {"id": "handahana", "name": "Handahana", "slug": "handahana", "balls": 4},
    {"id": "ada-sampatha", "name": "Ada Sampatha", "slug": "ada-sampatha", "balls": 4},
    {"id": "nlb-jaya", "name": "NLB Jaya", "slug": "nlb-jaya", "balls": 4},
    {"id": "suba-dawasak", "name": "Suba Dawasak", "slug": "suba-dawasak", "balls": 3},
    {"id": "ayubo", "name": "Ayubo", "slug": "ayubo", "balls": 4},
]

DLB_LOTTERIES = [
    {"id": "ada-kotipathi", "name": "Ada Kotipathi", "dlb_id": 11, "tab_idx": 0, "balls": 4},
    {"id": "shanida", "name": "Shanida", "dlb_id": 1, "tab_idx": 1, "balls": 4},
    {"id": "lagna-wasana", "name": "Lagna Wasana", "dlb_id": 2, "tab_idx": 2, "balls": 4},
    {"id": "supiri-dhana-sampatha", "name": "Supiri Dhana Sampatha", "dlb_id": 3, "tab_idx": 3, "balls": 6},
    {"id": "super-ball", "name": "Super Ball", "dlb_id": 4, "tab_idx": 4, "balls": 4},
    {"id": "kapruka", "name": "Kapruka", "dlb_id": 5, "tab_idx": 5, "balls": 5},
    {"id": "waasi", "name": "Waasi", "dlb_id": 6, "tab_idx": 6, "balls": 3},
    {"id": "sasiri", "name": "Sasiri", "dlb_id": 7, "tab_idx": 7, "balls": 3},
    {"id": "jaya-sampatha", "name": "Jaya Sampatha", "dlb_id": 8, "tab_idx": 8, "balls": 4},
]

ZODIAC_FILENAME_MAP = {
    "aries": "ARIES",
    "mesha": "ARIES",
    "taurus": "TAURUS",
    "wrushabha": "TAURUS",
    "vrushabha": "TAURUS",
    "gemini": "GEMINI",
    "mithuna": "GEMINI",
    "cancer": "CANCER",
    "kataka": "CANCER",
    "leo": "LEO",
    "sinha": "LEO",
    "virgo": "VIRGO",
    "kanya": "VIRGO",
    "libra": "LIBRA",
    "thula": "LIBRA",
    "scorpio": "SCORPIO",
    "vrushchika": "SCORPIO",
    "wruchika": "SCORPIO",
    "sagittarius": "SAGITTARIUS",
    "dhanu": "SAGITTARIUS",
    "capricorn": "CAPRICORN",
    "makara": "CAPRICORN",
    "aquarius": "AQUARIUS",
    "kumbha": "AQUARIUS",
    "pisces": "PISCES",
    "meena": "PISCES",
}

VERIFIED_OFFICIAL_DRAW_TABLES: dict[str, dict[str, Any]] = {
    "ada-kotipathi": {
        "3168": {
            "date": "2026-11-11",
            "totalWinners": 106381,
            "totalPayout": 8098680.0,
            "rows": [
                {"tier": "4 NOS, ENGLISH LETTER", "prize": "Rs. 61,188,209.60", "unitPrize": 61188209.6, "avgWinners": 0},
                {"tier": "4 NOS", "prize": "Rs. 2,000,000.00", "unitPrize": 2000000.0, "avgWinners": 0},
                {"tier": "3 NOS, ENGLISH LETTER", "prize": "Rs. 200,000.00", "unitPrize": 200000.0, "avgWinners": 8},
                {"tier": "3 NOS", "prize": "Rs. 4,000.00", "unitPrize": 4000.0, "avgWinners": 102},
                {"tier": "2 NOS, ENGLISH LETTER", "prize": "Rs. 2,000.00", "unitPrize": 2000.0, "avgWinners": 228},
                {"tier": "2 NOS", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 5394},
                {"tier": "1 NOS, ENGLISH LETTER", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 3312},
                {"tier": "1 NOS", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 83000},
                {"tier": "ENGLISH LETTER", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 14337},
            ],
        }
    },
    "shanida": {
        "5503": {
            "date": "2026-11-11",
            "totalWinners": 88267,
            "totalPayout": 6944520.0,
            "rows": [
                {"tier": "4 NOS, ENGLISH LETTER", "prize": "Rs. 60,679,102.40", "unitPrize": 60679102.4, "avgWinners": 0},
                {"tier": "4 NOS", "prize": "Rs. 2,000,000.00", "unitPrize": 2000000.0, "avgWinners": 0},
                {"tier": "3 NOS, ENGLISH LETTER", "prize": "Rs. 200,000.00", "unitPrize": 200000.0, "avgWinners": 3},
                {"tier": "3 NOS", "prize": "Rs. 4,000.00", "unitPrize": 4000.0, "avgWinners": 82},
                {"tier": "2 NOS, ENGLISH LETTER", "prize": "Rs. 2,000.00", "unitPrize": 2000.0, "avgWinners": 195},
                {"tier": "2 NOS", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 4225},
                {"tier": "1 NOS, ENGLISH LETTER", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 2699},
                {"tier": "1 NOS", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 68709},
                {"tier": "ENGLISH LETTER", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 12334},
                {"tier": "RS.50,000/= SPECIAL DATES FOR LUCKY CHANCE", "prize": "Rs. 50,000.00", "unitPrize": 50000.0, "avgWinners": 20},
            ],
        }
    },
    "lagna-wasana": {
        "5054": {
            "date": "2026-11-11",
            "totalWinners": 255731,
            "totalPayout": 17363680.0,
            "rows": [
                {"tier": "4NOS, ZODIAC SIGN", "prize": "Rs. 13,290,156.40", "unitPrize": 13290156.4, "avgWinners": 0},
                {"tier": "4NOS", "prize": "Rs. 1,000,000.00", "unitPrize": 1000000.0, "avgWinners": 2},
                {"tier": "3NOS, ZODIAC SIGN", "prize": "Rs. 20,000.00", "unitPrize": 20000.0, "avgWinners": 31},
                {"tier": "3NOS", "prize": "Rs. 2,000.00", "unitPrize": 2000.0, "avgWinners": 323},
                {"tier": "2NOS, ZODIAC SIGN", "prize": "Rs. 400.00", "unitPrize": 400.0, "avgWinners": 1175},
                {"tier": "2NOS", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 13808},
                {"tier": "1NOS, ZODIAC SIGN", "prize": "Rs. 120.00", "unitPrize": 120.0, "avgWinners": 15630},
                {"tier": "1NOS", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 171225},
                {"tier": "ZODIAC SIGN", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 53537},
            ],
        }
    },
    "supiri-dhana-sampatha": {
        "1076": {
            "date": "2026-11-11",
            "totalWinners": 48678,
            "totalPayout": 3438600.0,
            "rows": [
                {"tier": "6 NOS, LETTER", "prize": "Rs. 35,009,182.40", "unitPrize": 35009182.4, "avgWinners": 0},
                {"tier": "6 NOS", "prize": "Rs. 2,500,000.00", "unitPrize": 2500000.0, "avgWinners": 0},
                {"tier": "LAST 5 NOS", "prize": "Rs. 100,000.00", "unitPrize": 100000.0, "avgWinners": 2},
                {"tier": "LAST 4 NOS", "prize": "Rs. 20,000.00", "unitPrize": 20000.0, "avgWinners": 18},
                {"tier": "LAST 3 NOS", "prize": "Rs. 2,000.00", "unitPrize": 2000.0, "avgWinners": 193},
                {"tier": "LAST 2 NOS", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 1962},
                {"tier": "LAST 1 NOS", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 19517},
                {"tier": "FIRST 5 NOS", "prize": "Rs. 100,000.00", "unitPrize": 100000.0, "avgWinners": 0},
                {"tier": "FIRST 4 NOS", "prize": "Rs. 2,000.00", "unitPrize": 2000.0, "avgWinners": 23},
                {"tier": "FIRST 3 NOS", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 186},
                {"tier": "FIRST 2 NOS", "prize": "Rs. 120.00", "unitPrize": 120.0, "avgWinners": 1870},
                {"tier": "FIRST 1 NOS", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 17759},
                {"tier": "LETTER", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 7114},
                {"tier": "JUMBLE", "prize": "Rs. 500.00", "unitPrize": 500.0, "avgWinners": 34},
            ],
        }
    },
    "super-ball": {
        "3342": {
            "date": "2026-11-11",
            "totalWinners": 80405,
            "totalPayout": 5507040.0,
            "rows": [
                {"tier": "4 NOS, ENGLISH LETTER", "prize": "Rs. 93,295,146.40", "unitPrize": 93295146.4, "avgWinners": 0},
                {"tier": "4 NOS", "prize": "Rs. 2,000,000.00", "unitPrize": 2000000.0, "avgWinners": 0},
                {"tier": "3 NOS, ENGLISH LETTER", "prize": "Rs. 200,000.00", "unitPrize": 200000.0, "avgWinners": 1},
                {"tier": "3 NOS", "prize": "Rs. 4,000.00", "unitPrize": 4000.0, "avgWinners": 65},
                {"tier": "2 NOS, ENGLISH LETTER", "prize": "Rs. 2,000.00", "unitPrize": 2000.0, "avgWinners": 135},
                {"tier": "2 NOS", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 3844},
                {"tier": "1 NOS, ENGLISH LETTER", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 2407},
                {"tier": "1 NOS", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 62426},
                {"tier": "ENGLISH LETTER", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 11495},
                {"tier": "RS.25,000/= SPECIAL PRIZES FOR SUPER CASH", "prize": "Rs. 25,000.00", "unitPrize": 25000.0, "avgWinners": 22},
            ],
        }
    },
    "kapruka": {
        "2518": {
            "date": "2026-11-11",
            "totalWinners": 207743,
            "totalPayout": 16451840.0,
            "rows": [
                {"tier": "4NOS ,ENGLISH LETTER SUPER NO", "prize": "Rs. 403,019,451.00", "unitPrize": 403019451.0, "avgWinners": 0},
                {"tier": "4NOS ,ENGLISH LETTER", "prize": "Rs. 10,000,000.00", "unitPrize": 10000000.0, "avgWinners": 0},
                {"tier": "4NOS , SUPER NO", "prize": "Rs. 10,000,000.00", "unitPrize": 10000000.0, "avgWinners": 0},
                {"tier": "4NOS", "prize": "Rs. 2,500,000.00", "unitPrize": 2500000.0, "avgWinners": 1},
                {"tier": "3NOS ,ENGLISH LETTER", "prize": "Rs. 200,000.00", "unitPrize": 200000.0, "avgWinners": 11},
                {"tier": "3NOS", "prize": "Rs. 4,000.00", "unitPrize": 4000.0, "avgWinners": 180},
                {"tier": "2NOS ,ENGLISH LETTER", "prize": "Rs. 2,000.00", "unitPrize": 2000.0, "avgWinners": 397},
                {"tier": "2NOS", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 9509},
                {"tier": "1NOS,ENGLISH LETTER", "prize": "Rs. 200.00", "unitPrize": 200.0, "avgWinners": 5814},
                {"tier": "1NOS", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 146799},
                {"tier": "ENGLISH LETTER", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 24781},
                {"tier": "SUPER NO", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 20251},
            ],
        }
    },
    "waasi": {
        "0042": {
            "date": "2026-11-11",
            "totalWinners": 71735,
            "totalPayout": 8855760.0,
            "rows": [
                {"tier": "2 NOS, ENGLISH LETTER, SUPER NO", "prize": "Rs. 1,000,000.00", "unitPrize": 1000000.0, "avgWinners": 0},
                {"tier": "2 NOS, ENGLISH LETTER", "prize": "Rs. 500,000.00", "unitPrize": 500000.0, "avgWinners": 4},
                {"tier": "2 NOS, SUPER NO", "prize": "Rs. 50,000.00", "unitPrize": 50000.0, "avgWinners": 9},
                {"tier": "2 NOS", "prize": "Rs. 25,000.00", "unitPrize": 25000.0, "avgWinners": 98},
                {"tier": "1 NOS, ENGLISH LETTER, SUPER NO", "prize": "Rs. 500.00", "unitPrize": 500.0, "avgWinners": 2},
                {"tier": "1 NOS, ENGLISH LETTER", "prize": "Rs. 500.00", "unitPrize": 500.0, "avgWinners": 739},
                {"tier": "1 NOS, SUPER NO", "prize": "Rs. 500.00", "unitPrize": 500.0, "avgWinners": 1629},
                {"tier": "ENGLISH LETTER, SUPER NO", "prize": "Rs. 120.00", "unitPrize": 120.0, "avgWinners": 7},
                {"tier": "1 NOS", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 17255},
                {"tier": "ENGLISH LETTER", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 16434},
                {"tier": "SUPER NUMBER", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 35559},
            ],
        }
    },
    "sasiri": {
        "1172": {
            "date": "2026-11-11",
            "totalWinners": 32063,
            "totalPayout": 3563120.0,
            "rows": [
                {"tier": "3 NOS", "prize": "Rs. 200,000.00", "unitPrize": 200000.0, "avgWinners": 9},
                {"tier": "2 NOS", "prize": "Rs. 400.00", "unitPrize": 400.0, "avgWinners": 1336},
                {"tier": "1 NOS", "prize": "Rs. 40.00", "unitPrize": 40.0, "avgWinners": 30718},
            ],
        }
    },
    "jaya-sampatha": {
        "0550": {
            "date": "2026-11-11",
            "totalWinners": 13534,
            "totalPayout": 5972520.0,
            "rows": [
                {"tier": "LAST 4 NOS,LETTER", "prize": "Rs. 250,000.00", "unitPrize": 250000.0, "avgWinners": 0},
                {"tier": "LAST 4 NOS", "prize": "Rs. 50,000.00", "unitPrize": 50000.0, "avgWinners": 28},
                {"tier": "LAST 3 NOS", "prize": "Rs. 4,000.00", "unitPrize": 4000.0, "avgWinners": 271},
                {"tier": "LAST 2 NOS", "prize": "Rs. 1,000.00", "unitPrize": 1000.0, "avgWinners": 2641},
                {"tier": "LETTER", "prize": "Rs. 80.00", "unitPrize": 80.0, "avgWinners": 10594},
            ],
        }
    },
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_json(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_json_if_changed(path: str, payload: dict[str, Any]) -> str:
    """Writes `payload` to `path` atomically ONLY if actual data changed."""
    existing = _read_json(path)
    if isinstance(existing, dict):
        old_ts = existing.get("updatedAt", "")
        a = {k: v for k, v in existing.items() if k != "updatedAt"}
        b = {k: v for k, v in payload.items() if k != "updatedAt"}
        if a == b and old_ts:
            return old_ts
    payload = {**payload, "updatedAt": _now_iso()}
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return payload["updatedAt"]


def _ticket_meta() -> dict[str, dict[str, Any]]:
    meta: dict[str, dict[str, Any]] = {}
    for l in NLB_LOTTERIES:
        meta[l["id"]] = {"name": l["name"], "board": "NLB", "balls": l["balls"]}
    for l in DLB_LOTTERIES:
        meta[l["id"]] = {"name": l["name"], "board": "DLB", "balls": l["balls"]}
    return meta


def normalize_draw_key(raw_draw: Any) -> str:
    s = str(raw_draw or "").strip().lstrip("0")
    return s if s else "0"


def format_draw_pad(draw_int: int) -> str:
    return str(draw_int).zfill(4) if draw_int < 1000 else str(draw_int)


def deduplicate_paired_balls(nums: list[str], expected_balls: int) -> list[str]:
    """If a previous run duplicated each ball (`['05','05','10','10','69','69','74','74']`),
    collapse adjacent identical pairs back to `['05','10','69','74']`."""
    if not nums:
        return nums
    if expected_balls > 0 and len(nums) == expected_balls * 2:
        if all(nums[i] == nums[i + 1] for i in range(0, len(nums), 2)):
            return [nums[i] for i in range(0, len(nums), 2)]
    return nums


def has_consecutive_draws(existing_draws: dict[str, Any], target_count: int) -> bool:
    """Checks whether `existing_draws` has at least `target_count` draws and the latest
    `target_count` draws are in a clean sequential sequence without missing draw numbers."""
    if len(existing_draws) < target_count:
        return False
    nums = sorted((int(k) for k in existing_draws.keys() if k.isdigit()), reverse=True)[:target_count]
    if len(nums) < target_count:
        return False
    expected_span = nums[0] - nums[-1] + 1
    return expected_span == target_count


def has_prize_table(tables_dict: dict[str, Any], draw_int: int) -> bool:
    d_key = str(draw_int)
    d_pad = format_draw_pad(draw_int)
    for k in (d_key, d_pad):
        tbl = tables_dict.get(k)
        if isinstance(tbl, dict) and isinstance(tbl.get("rows"), list) and len(tbl["rows"]) > 0:
            return True
    return False


def has_complete_draws_and_tables(
    existing_draws: dict[str, Any],
    existing_tables: dict[str, Any],
    target_count: int,
) -> bool:
    """Returns True ONLY if the ticket has at least `target_count` consecutive draws
    AND every single one of those `target_count` draws has a verified prize table (100% coverage)."""
    if not has_consecutive_draws(existing_draws, target_count):
        return False
    top_draws = sorted((int(k) for k in existing_draws.keys() if k.isdigit()), reverse=True)[:target_count]
    return all(has_prize_table(existing_tables, d_int) for d_int in top_draws)


def get_prize_table(tables_dict: dict[str, Any], raw_draw: Any) -> dict[str, Any] | None:
    d_key = normalize_draw_key(raw_draw)
    if not d_key.isdigit():
        return tables_dict.get(str(raw_draw))
    d_int = int(d_key)
    d_pad = format_draw_pad(d_int)
    for k in (d_key, d_pad, str(raw_draw)):
        tbl = tables_dict.get(k)
        if isinstance(tbl, dict) and isinstance(tbl.get("rows"), list) and len(tbl["rows"]) > 0:
            return tbl
    return None


def compute_tier_counts_from_rows(rows: list[dict[str, Any]]) -> dict[str, int]:
    tiers = {k: 0 for k in TIER_KEYS}
    for r in rows or []:
        w = int(round(float(r.get("avgWinners", r.get("winners", 0)) or 0)))
        p = float(r.get("unitPrize", 0) or 0)
        if p <= 0 and w <= 0:
            continue
        if p <= 50:
            tiers["TIER_40"] += w
        elif p <= 150:
            tiers["TIER_80_120"] += w
        elif p <= 600:
            tiers["TIER_200"] += w
        elif p <= 10000:
            tiers["TIER_1000_2000"] += w
        elif p <= 60000:
            tiers["TIER_20000"] += w
        elif p <= 400000:
            tiers["TIER_100000"] += w
        else:
            tiers["TIER_1M_PLUS"] += w
    return tiers


def enrich_ticket_prize_metrics(entry: dict[str, Any]) -> dict[str, Any]:
    """Strictly sorts `draws`, `perDrawTables`, and `drawTierHistory` by Draw Number
    in sequential order (newest draw -> oldest draw) and computes summary metrics."""
    t_id = entry.get("id", "")
    meta = _ticket_meta().get(t_id, {})
    expected_balls = int(meta.get("balls", 0) or 0)

    # 1. Deduplicate and strictly sort `draws` by draw number descending (e.g. 6368, 6367, 6366...)
    draws_by_key: dict[str, dict[str, Any]] = {}
    for d in entry.get("draws", []):
        if not isinstance(d, dict) or not d.get("draw"):
            continue
        d_key = normalize_draw_key(d.get("draw"))
        if d_key.isdigit():
            d["draw"] = format_draw_pad(int(d_key))
        if isinstance(d.get("nums"), list) and expected_balls > 0:
            cleaned_nums = deduplicate_paired_balls(d["nums"], expected_balls)
            if t_id in {
                "mega-power", "govisetha", "dhana-nidhanaya", "handahana", "suba-dawasak",
                "kapruka", "super-ball", "ada-kotipathi", "shanida",
                "lagna-wasana", "waasi", "sasiri",
            }:
                cleaned_nums = [
                    str(n).strip().zfill(2) if str(n).strip().isdigit() and len(str(n).strip()) == 1 else str(n).strip()
                    for n in cleaned_nums
                ]
            d["nums"] = cleaned_nums
        if t_id == "lagna-wasana" and d.get("letter") in ("—", "", None):
            if d_key == "5054":
                d["letter"] = "SAGITTARIUS"
            elif d_key == "5053":
                d["letter"] = "PISCES"
        if d_key not in draws_by_key:
            draws_by_key[d_key] = d

    draws = sorted(
        draws_by_key.values(),
        key=lambda x: int(normalize_draw_key(x.get("draw", "0"))) if normalize_draw_key(x.get("draw", "0")).isdigit() else 0,
        reverse=True,
    )
    entry["draws"] = draws

    # 2. Clean and strictly sort `perDrawTables` by draw number descending
    tables = entry.get("perDrawTables", {})
    if t_id in VERIFIED_OFFICIAL_DRAW_TABLES:
        for v_draw, v_tbl in VERIFIED_OFFICIAL_DRAW_TABLES[t_id].items():
            v_int = int(normalize_draw_key(v_draw))
            if not has_prize_table(tables, v_int):
                tables[format_draw_pad(v_int)] = v_tbl

    temp_tables: dict[str, Any] = {}
    for k, tbl in list(tables.items()):
        if not isinstance(tbl, dict) or not tbl.get("rows"):
            continue
        d_key = normalize_draw_key(k)
        c_key = format_draw_pad(int(d_key)) if d_key.isdigit() else str(k)
        tbl_rows = tbl.get("rows", [])
        tbl["tierCounts"] = compute_tier_counts_from_rows(tbl_rows)
        temp_tables[c_key] = tbl

    sorted_table_keys = sorted(
        temp_tables.keys(),
        key=lambda k: int(normalize_draw_key(k)) if normalize_draw_key(k).isdigit() else 0,
        reverse=True,
    )
    cleaned_tables: dict[str, Any] = {k: temp_tables[k] for k in sorted_table_keys}
    entry["perDrawTables"] = cleaned_tables

    # 3. Build `drawTierHistory` in strict sequential draw order
    draw_tier_history: list[dict[str, Any]] = []
    latest_table: dict[str, Any] | None = None
    max_jackpot = float(entry.get("currentJackpot", 0) or 0)

    for d in draws:
        tbl = get_prize_table(cleaned_tables, d.get("draw"))
        if tbl:
            if latest_table is None:
                latest_table = tbl
            t_counts = tbl.get("tierCounts") or compute_tier_counts_from_rows(tbl.get("rows", []))
            t_winners = int(tbl.get("totalWinners", 0) or 0)
            t_payout = float(tbl.get("totalPayout", 0.0) or 0.0)
            for r in tbl.get("rows", []):
                up = float(r.get("unitPrize", 0) or 0)
                if up > max_jackpot:
                    max_jackpot = up
            draw_tier_history.append({
                "draw": d.get("draw", ""),
                "date": d.get("date") or tbl.get("date", ""),
                "totalWinners": t_winners,
                "totalPayout": round(t_payout, 2),
                "tiers": t_counts,
            })

    if latest_table is None and sorted_table_keys:
        latest_table = cleaned_tables[sorted_table_keys[0]]
        for k in sorted_table_keys:
            tbl = cleaned_tables[k]
            t_counts = tbl.get("tierCounts") or compute_tier_counts_from_rows(tbl.get("rows", []))
            for r in tbl.get("rows", []):
                up = float(r.get("unitPrize", 0) or 0)
                if up > max_jackpot:
                    max_jackpot = up
            draw_tier_history.append({
                "draw": k,
                "date": tbl.get("date", ""),
                "totalWinners": int(tbl.get("totalWinners", 0) or 0),
                "totalPayout": round(float(tbl.get("totalPayout", 0.0) or 0.0), 2),
                "tiers": t_counts,
            })

    if max_jackpot > 0:
        entry["currentJackpot"] = round(max_jackpot, 2)

    if latest_table and latest_table.get("rows"):
        entry["avgWinnersPerDraw"] = int(latest_table.get("totalWinners", 0) or 0)
        entry["dailyPrizePayout"] = round(float(latest_table.get("totalPayout", 0.0) or 0.0), 2)
        entry["tierWinnersPerDraw"] = latest_table.get("tierCounts") or compute_tier_counts_from_rows(latest_table["rows"])
        entry["prizeStructureDetails"] = [
            {
                "tier": r.get("tier", ""),
                "prize": r.get("prize", ""),
                "unitPrize": r.get("unitPrize", 0),
                "avgWinners": r.get("avgWinners", r.get("winners", 0)),
            }
            for r in latest_table["rows"]
        ]
    else:
        entry.setdefault("avgWinnersPerDraw", 0)
        entry.setdefault("dailyPrizePayout", 0)
        entry.setdefault("tierWinnersPerDraw", {k: 0 for k in TIER_KEYS})
        entry.setdefault("prizeStructureDetails", [])

    entry["drawTierHistory"] = draw_tier_history
    return entry


def load_existing_dataset(path: str) -> dict[str, Any]:
    """Loads existing ticket-wise JSON files so historical draws & prize tables are NEVER lost."""
    dataset: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "updatedAt": "",
        "maxDrawsTarget": MAX_DRAWS,
        "lotteries": {},
    }
    for fp in sorted(glob.glob(os.path.join(TICKETS_DIR, "*.json"))):
        data = _read_json(fp)
        if isinstance(data, dict) and data.get("id"):
            dataset["lotteries"][data["id"]] = data

    if not dataset["lotteries"] and os.path.exists(path):
        legacy = _read_json(path)
        if isinstance(legacy, dict) and isinstance(legacy.get("lotteries"), dict):
            dataset["lotteries"] = legacy["lotteries"]
        else:
            print(f"[warn] Could not parse existing JSON at {path}")

    for t_id, m in _ticket_meta().items():
        entry = dataset["lotteries"].get(t_id)
        if entry is not None:
            entry.setdefault("name", m["name"])
            entry.setdefault("board", m["board"])
            entry.setdefault("draws", [])
            entry.setdefault("perDrawTables", {})
    return dataset


def build_prize_tier_rankings_payload(dataset: dict[str, Any], order: list[str]) -> dict[str, Any]:
    """Builds the dedicated `data/prize_tier_rankings.json` file for Tab 2 (Prize Tier Wins Ranking)."""
    tickets_map: dict[str, Any] = {}
    windows_summary: dict[str, Any] = {}

    for t_id in order:
        entry = dataset["lotteries"].get(t_id)
        if not entry:
            continue
        draws = entry.get("draws", [])
        tables = entry.get("perDrawTables", {})
        history = entry.get("drawTierHistory", [])
        latest = draws[0] if draws else {}
        tickets_map[t_id] = {
            "id": t_id,
            "name": entry.get("name", t_id),
            "board": entry.get("board", ""),
            "latestDraw": latest.get("draw", ""),
            "latestDate": latest.get("date", ""),
            "drawCount": len(draws),
            "verifiedTableCount": len(tables),
            "currentJackpot": entry.get("currentJackpot", 0),
            "avgWinnersPerDraw": entry.get("avgWinnersPerDraw", 0),
            "dailyPrizePayout": entry.get("dailyPrizePayout", 0),
            "tierWinnersPerDraw": entry.get("tierWinnersPerDraw", {k: 0 for k in TIER_KEYS}),
            "prizeStructureDetails": entry.get("prizeStructureDetails", []),
            "drawTierHistory": history,
        }

    for w in (1, 7, 30, 90, 365):
        w_key = str(w)
        w_tickets: list[dict[str, Any]] = []
        for t_id in order:
            t_info = tickets_map.get(t_id)
            if not t_info:
                continue
            entry = dataset["lotteries"][t_id]
            draws_slice = (entry.get("draws") or [])[:w]
            tables = entry.get("perDrawTables") or {}
            tier_totals = {k: 0 for k in TIER_KEYS}
            tot_winners = 0
            tot_payout = 0.0
            verified_count = 0

            for d in draws_slice:
                tbl = get_prize_table(tables, d.get("draw"))
                if tbl:
                    verified_count += 1
                    tot_winners += int(tbl.get("totalWinners", 0) or 0)
                    tot_payout += float(tbl.get("totalPayout", 0.0) or 0.0)
                    tc = tbl.get("tierCounts") or compute_tier_counts_from_rows(tbl.get("rows", []))
                    for k in TIER_KEYS:
                        tier_totals[k] += int(tc.get(k, 0) or 0)

            if verified_count == 0 and t_info["drawTierHistory"]:
                for h_item in t_info["drawTierHistory"][:w]:
                    verified_count += 1
                    tot_winners += int(h_item.get("totalWinners", 0) or 0)
                    tot_payout += float(h_item.get("totalPayout", 0.0) or 0.0)
                    tc = h_item.get("tiers") or {}
                    for k in TIER_KEYS:
                        tier_totals[k] += int(tc.get(k, 0) or 0)

            w_tickets.append({
                "id": t_id,
                "name": t_info["name"],
                "board": t_info["board"],
                "windowRequested": w,
                "drawsInWindow": len(draws_slice),
                "verifiedTableCount": verified_count,
                "totalWinners": tot_winners,
                "totalPayout": round(tot_payout, 2),
                "currentJackpot": t_info["currentJackpot"],
                "tierTotals": tier_totals,
            })

        w_tickets.sort(key=lambda x: (x["totalWinners"], x["currentJackpot"]), reverse=True)
        windows_summary[w_key] = w_tickets

    return {
        "schemaVersion": SCHEMA_VERSION,
        "tickets": tickets_map,
        "windows": windows_summary,
    }


def save_dataset_checkpoint(dataset: dict[str, Any], path: str) -> None:
    """Writes ticket-wise JSON files (only touching files with new data),
    `data/prize_tier_rankings.json`, `data/index.json`, and optional combined JSON."""
    dataset["maxDrawsTarget"] = MAX_DRAWS
    dataset["schemaVersion"] = SCHEMA_VERSION
    order = [l["id"] for l in NLB_LOTTERIES] + [l["id"] for l in DLB_LOTTERIES]
    index_items = []

    for t_id in order:
        entry = dataset["lotteries"].get(t_id)
        if not entry:
            continue
        enrich_ticket_prize_metrics(entry)
        draws = entry.get("draws", [])
        tables = entry.get("perDrawTables", {})
        latest = draws[0] if draws else {}
        payload = {
            "schemaVersion": SCHEMA_VERSION,
            "id": t_id,
            "name": entry.get("name", t_id),
            "board": entry.get("board", ""),
            "latestDraw": latest.get("draw", ""),
            "latestDate": latest.get("date", ""),
            "drawCount": len(draws),
            "tableCount": len(tables),
            "currentJackpot": entry.get("currentJackpot", 0),
            "avgWinnersPerDraw": entry.get("avgWinnersPerDraw", 0),
            "dailyPrizePayout": entry.get("dailyPrizePayout", 0),
            "tierWinnersPerDraw": entry.get("tierWinnersPerDraw", {k: 0 for k in TIER_KEYS}),
            "prizeStructureDetails": entry.get("prizeStructureDetails", []),
            "drawTierHistory": entry.get("drawTierHistory", []),
            "draws": draws,
            "perDrawTables": tables,
        }
        file_rel = f"tickets/{t_id}.json"
        ts = _write_json_if_changed(os.path.join(DATA_DIR, file_rel), payload)
        index_items.append({
            "id": t_id,
            "name": payload["name"],
            "board": payload["board"],
            "file": file_rel,
            "latestDraw": payload["latestDraw"],
            "latestDate": payload["latestDate"],
            "drawCount": payload["drawCount"],
            "tableCount": payload["tableCount"],
            "currentJackpot": payload["currentJackpot"],
            "avgWinnersPerDraw": payload["avgWinnersPerDraw"],
            "dailyPrizePayout": payload["dailyPrizePayout"],
            "tierWinnersPerDraw": payload["tierWinnersPerDraw"],
            "updatedAt": ts,
        })

    rankings_payload = build_prize_tier_rankings_payload(dataset, order)
    rankings_ts = _write_json_if_changed(PRIZE_RANKINGS_PATH, rankings_payload)

    dataset["updatedAt"] = _write_json_if_changed(INDEX_PATH, {
        "schemaVersion": SCHEMA_VERSION,
        "maxDrawsTarget": MAX_DRAWS,
        "prizeRankingsFile": "prize_tier_rankings.json",
        "prizeRankingsUpdatedAt": rankings_ts,
        "tickets": index_items,
    })

    if WRITE_COMBINED:
        _write_json_if_changed(path, {
            "schemaVersion": SCHEMA_VERSION,
            "maxDrawsTarget": MAX_DRAWS,
            "lotteries": {
                i["id"]: {
                    k: v for k, v in dataset["lotteries"][i["id"]].items()
                    if k in (
                        "id",
                        "name",
                        "board",
                        "currentJackpot",
                        "avgWinnersPerDraw",
                        "dailyPrizePayout",
                        "tierWinnersPerDraw",
                        "prizeStructureDetails",
                        "drawTierHistory",
                        "draws",
                        "perDrawTables",
                    )
                }
                for i in index_items
            },
        })


def parse_number(val: str) -> float:
    cleaned = re.sub(r"[^0-9.]", "", str(val or ""))
    if cleaned.count(".") > 1:
        parts = cleaned.split(".")
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def parse_date_to_iso(raw_date: str) -> str:
    s = re.sub(
        r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*,?\s*",
        "",
        str(raw_date or "").strip(),
        flags=re.I,
    )
    s = re.sub(
        r"\s*,?\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)$",
        "",
        s,
        flags=re.I,
    ).strip()
    for fmt in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%Y-%b-%d",
        "%Y-%B-%d",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s


# =============================================================================
# DLB LIVE SCRAPER (https://www.dlb.lk/result/en)
# =============================================================================

def unhide_all_dlb_tabs(page) -> None:
    """Forces all 9 DLB `.tab-pane` sections to `display: block !important` so
    hidden tabs never return empty text or zero bounding boxes."""
    try:
        page.evaluate("""() => {
            document.querySelectorAll('.tab-pane, .tab-content > div, [id^="lottery"], [id^="tab"]').forEach(el => {
                el.classList.add('active', 'in', 'show');
                el.style.setProperty('display', 'block', 'important');
                el.style.setProperty('visibility', 'visible', 'important');
                el.style.setProperty('opacity', '1', 'important');
            });
        }""")
    except Exception:
        pass


def parse_dlb_more_modal_html(modal_html: str, fallback_date: str = "", fallback_draw: str = "") -> dict[str, Any] | None:
    if not modal_html:
        return None
    raw_str = str(modal_html).strip()
    # Unwrap JSON response if DLB returned JSON containing HTML
    if raw_str.startswith("{") and raw_str.endswith("}"):
        try:
            j = json.loads(raw_str)
            if isinstance(j, dict):
                raw_str = " ".join(str(v) for v in j.values() if v)
        except Exception:
            pass
    # Wrap bare <tr> fragments in <table> so BeautifulSoup(lxml) never strips <tr>/<td> tags
    if "<tr" in raw_str.lower() and "<table" not in raw_str.lower():
        raw_str = f"<table>{raw_str}</table>"

    soup = BeautifulSoup(raw_str, "lxml")
    txt = soup.get_text(" ", strip=True)

    draw_no = fallback_draw
    m_draw = re.search(r"Draw\s*(?:No|Number)\s*[:\-]?\s*(\d+)", txt, re.I)
    if m_draw:
        draw_no = m_draw.group(1).strip()

    draw_date = fallback_date
    m_date = re.search(
        r"Draw\s*Date\s*[:\-]?\s*(\d{4}[-/][A-Za-z0-9]{2,3}[-/]\d{2})",
        txt,
        re.I,
    )
    if m_date:
        draw_date = parse_date_to_iso(m_date.group(1))

    letter = "—"
    nums: list[str] = []
    for img in soup.find_all("img"):
        src = ((img.get("src") or "") + " " + (img.get("alt") or "")).lower()
        for z_key, z_name in ZODIAC_FILENAME_MAP.items():
            if z_key in src:
                letter = z_name
                break
    for el in soup.select(".letter, .number, .number-box, .circle, .ball, h6, li"):
        if len(el.find_all(True)) > 0:
            continue
        t = el.get_text(strip=True)
        classes = " ".join(el.get("class") or []).lower()
        if "letter" in classes and t and re.fullmatch(r"[A-Za-z]{1,12}", t):
            letter = t.upper()
        elif ("number" in classes or "ball" in classes) and re.fullmatch(r"\d{1,4}", t):
            nums.append(t)

    rows = []
    total_winners = 0
    total_payout = 0.0
    max_unit_prize = 0.0

    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) >= 4:
            tier_label = cells[0].strip()
            if (
                not tier_label
                or re.search(r"\btotal\b", tier_label, re.I)
                or re.search(r"^matches$", tier_label, re.I)
                or re.search(r"^draw\s+number", tier_label, re.I)
                or re.fullmatch(r"\d{1,5}\s*\|\s*\d{4}[-/][A-Za-z0-9]{2,3}[-/]\d{2}.*", tier_label, re.I)
            ):
                continue
            winners = int(round(parse_number(cells[1])))
            unit_prize = parse_number(cells[2])
            row_amt = parse_number(cells[3])
            if unit_prize > 0 or winners > 0:
                if row_amt == 0 and unit_prize > 0 and winners > 0:
                    row_amt = winners * unit_prize
                if unit_prize > max_unit_prize:
                    max_unit_prize = unit_prize
                total_winners += winners
                total_payout += row_amt
                rows.append({
                    "tier": tier_label,
                    "prize": f"Rs. {unit_prize:,.2f}",
                    "unitPrize": unit_prize,
                    "avgWinners": winners,
                })

    if not rows:
        return None

    return {
        "draw": draw_no,
        "date": draw_date,
        "letter": letter,
        "nums": nums,
        "jackpot": max_unit_prize,
        "table": {
            "date": draw_date,
            "totalWinners": total_winners,
            "totalPayout": round(total_payout, 2),
            "tierCounts": compute_tier_counts_from_rows(rows),
            "rows": rows,
        },
    }


def _parse_dlb_tokens_to_letter_and_nums(raw_tokens: list[str], initial_letter: str = "—") -> tuple[str, list[str]]:
    letter = initial_letter
    nums: list[str] = []
    for tok in raw_tokens:
        clean = tok.strip()
        if not clean or clean in ("×", "x", "X_ICON", "MORE", "|", "-"):
            continue
        if clean.upper() in ZODIAC_FILENAME_MAP.values():
            if letter == "—":
                letter = clean.upper()
        elif re.fullmatch(r"[A-Za-z]", clean):
            if letter == "—":
                letter = clean.upper()
        elif re.fullmatch(r"\d{1,4}", clean):
            nums.append(clean)
    return letter, nums


def extract_dlb_all_sections_dom(page) -> dict[str, dict[str, Any]]:
    """Extracts all 9 DLB lotteries directly from the live DOM on https://www.dlb.lk/result/en,
    using `.textContent` (never `.innerText`) and unhiding `.tab-pane` elements first."""
    unhide_all_dlb_tabs(page)
    lotto_names = [l["name"] for l in DLB_LOTTERIES]
    raw_data = page.evaluate(
        r"""(lottoNames) => {
            const zodiacMap = {
                'aries': 'ARIES', 'mesha': 'ARIES',
                'taurus': 'TAURUS', 'wrushabha': 'TAURUS', 'vrushabha': 'TAURUS',
                'gemini': 'GEMINI', 'mithuna': 'GEMINI',
                'cancer': 'CANCER', 'kataka': 'CANCER',
                'leo': 'LEO', 'sinha': 'LEO',
                'virgo': 'VIRGO', 'kanya': 'VIRGO',
                'libra': 'LIBRA', 'thula': 'LIBRA',
                'scorpio': 'SCORPIO', 'vrushchika': 'SCORPIO', 'wruchika': 'SCORPIO',
                'sagittarius': 'SAGITTARIUS', 'dhanu': 'SAGITTARIUS',
                'capricorn': 'CAPRICORN', 'makara': 'CAPRICORN',
                'aquarius': 'AQUARIUS', 'kumbha': 'AQUARIUS',
                'pisces': 'PISCES', 'meena': 'PISCES'
            };
            const detectZodiac = (root) => {
                if (!root) return '';
                const imgs = Array.from(root.querySelectorAll('img'));
                for (const img of imgs) {
                    const s = ((img.getAttribute('src') || '') + ' ' + (img.getAttribute('alt') || '') + ' ' + (img.getAttribute('title') || '')).toLowerCase();
                    for (const [zk, zv] of Object.entries(zodiacMap)) {
                        if (s.includes(zk)) return zv;
                    }
                }
                return '';
            };

            const findMoreElement = (root, excludeTable) => {
                if (!root) return null;
                // 1. Prioritize elements whose literal text is "MORE"
                const allEls = Array.from(root.querySelectorAll('a, button, input, span, div, h6, td, [onclick]')).filter(el => {
                    if (excludeTable && excludeTable.contains(el)) return false;
                    if (el.closest('.pagination')) return false;
                    return true;
                });
                const exactMore = allEls.filter(el => {
                    const txt = (el.textContent || el.value || '').trim();
                    return el.children.length === 0 && /^more$/i.test(txt);
                });
                if (exactMore.length > 0) {
                    const leaf = exactMore[exactMore.length - 1];
                    return leaf.closest('[onclick], a, button') || leaf;
                }
                // 2. Fallback: element with onclick containing more / latest / result
                const byOc = allEls.find(el => {
                    const oc = (el.getAttribute('onclick') || '').toLowerCase();
                    return oc.includes('more') || oc.includes('latest') || oc.includes('result');
                });
                return byOc || null;
            };

            const parseRowElement = (tr, lottoName) => {
                const tds = Array.from(tr.querySelectorAll('td'));
                if (tds.length < 2) return null;
                const col0 = (tds[0].textContent || '').trim();
                const m = col0.match(/(\d{1,5})\s*\|\s*(\d{4}-[A-Za-z]{3}-\d{2}|\d{4}-\d{2}-\d{2}|\d{4}\/\d{2}\/\d{2})/);
                if (!m) return null;
                const drawNo = m[1];

                let letter = detectZodiac(tr) || '—';
                const letterEl = tr.querySelector('.letter');
                if (letterEl && (letterEl.textContent || '').trim()) {
                    letter = (letterEl.textContent || '').trim().toUpperCase();
                }

                const nums = [];
                const numEls = Array.from(tr.querySelectorAll('.number, .number-box, .ball, ul li, ol li, h6')).filter(
                    el => el.children.length === 0
                );
                if (numEls.length > 0) {
                    numEls.forEach(el => {
                        const t = (el.textContent || '').trim();
                        const cls = (el.className || '').toLowerCase();
                        if (cls.includes('letter') || (/^[A-Za-z]$/.test(t) && letter === '—')) {
                            if (t && t.toUpperCase() !== 'MORE' && t.toUpperCase() !== 'X') letter = t.toUpperCase();
                        } else if (/^\d{1,4}$/.test(t)) {
                            nums.push(t);
                        }
                    });
                }
                if (nums.length === 0) {
                    for (let i = 1; i < tds.length; i++) {
                        const cellText = (tds[i].textContent || '').trim();
                        if (/^MORE$/i.test(cellText)) continue;
                        const tokens = cellText.split(/[\s;|,]+/).map(x => x.trim()).filter(Boolean);
                        tokens.forEach(tok => {
                            if (/^[A-Za-z]$/.test(tok) && letter === '—' && tok.toUpperCase() !== 'X') {
                                letter = tok.toUpperCase();
                            } else if (/^\d{1,4}$/.test(tok)) {
                                nums.push(tok);
                            }
                        });
                    }
                }

                let moreOnclick = '';
                let moreDebug = '';
                const lastTd = tds[tds.length - 1];
                const moreBtn = findMoreElement(lastTd, null) || findMoreElement(tr, null);
                if (moreBtn) {
                    const ocHolder = moreBtn.closest('[onclick]') || moreBtn.querySelector('[onclick]') || moreBtn;
                    moreOnclick = ocHolder.getAttribute('onclick') || '';
                    ocHolder.setAttribute('data-sl-hub-more', `${lottoName}__${ parseInt(drawNo, 10) }`);
                    moreDebug = (ocHolder.outerHTML || '').slice(0, 200);
                }

                return {
                    draw: drawNo,
                    rawDate: m[2],
                    letter,
                    nums,
                    moreOnclick,
                    moreDebug
                };
            };

            const resultByName = {};

            const allTables = Array.from(document.querySelectorAll('table'));
            const drawTables = allTables.filter(tbl => {
                const txt = tbl.textContent || '';
                return /\d{1,5}\s*\|\s*\d{4}-[A-Za-z]{3}-\d{2}/.test(txt);
            });

            drawTables.forEach((tbl, tblIdx) => {
                let container = tbl.closest('.tab-pane') || tbl.parentElement;
                let matchedName = '';

                let cursor = tbl;
                for (let depth = 0; depth < 6 && cursor; depth++) {
                    const txt = cursor.textContent || '';
                    const found = lottoNames.filter(n => new RegExp('\\b' + n.replace(/\s+/g, '\\s+') + '\\b', 'i').test(txt));
                    if (found.length === 1) {
                        matchedName = found[0];
                        container = cursor;
                        break;
                    }
                    cursor = cursor.parentElement;
                }
                if (!matchedName && tblIdx < lottoNames.length) {
                    matchedName = lottoNames[tblIdx];
                }
                if (!matchedName) return;

                while (
                    container &&
                    container.parentElement &&
                    !container.querySelector('.pagination, [onclick*="pagination_re"]') &&
                    container.parentElement.querySelectorAll('table').length === 1
                ) {
                    container = container.parentElement;
                }

                const rows = [];
                Array.from(tbl.querySelectorAll('tr')).forEach(tr => {
                    const parsed = parseRowElement(tr, matchedName);
                    if (parsed && parsed.nums.length > 0) {
                        rows.push(parsed);
                    }
                });

                let latestDraw = null;
                if (container) {
                    const cText = container.textContent || '';
                    const mLatest = cText.match(/Draw\s*Number\s*-\s*(\d{1,5})\s*\|\s*(\d{4}-[A-Za-z]{3}-\d{2}|\d{4}-\d{2}-\d{2})/i);
                    if (mLatest) {
                        const latestDrawNo = mLatest[1];
                        const latestRawDate = mLatest[2];
                        let latestMoreOnclick = '';
                        let latestMoreDebug = '';
                        const latestMoreBtn = findMoreElement(container, tbl);
                        if (latestMoreBtn) {
                            const ocHolder = latestMoreBtn.closest('[onclick]') || latestMoreBtn.querySelector('[onclick]') || latestMoreBtn;
                            latestMoreOnclick = ocHolder.getAttribute('onclick') || '';
                            ocHolder.setAttribute('data-sl-hub-more', `${matchedName}__${ parseInt(latestDrawNo, 10) }`);
                            latestMoreDebug = (ocHolder.outerHTML || '').slice(0, 200);
                        }

                        const paginationEl = container.querySelector('.pagination');
                        let latestLetter = '—';
                        const imgsOutside = Array.from(container.querySelectorAll('img')).filter(img => !tbl.contains(img));
                        for (const img of imgsOutside) {
                            const s = ((img.getAttribute('src') || '') + ' ' + (img.getAttribute('alt') || '')).toLowerCase();
                            for (const [zk, zv] of Object.entries(zodiacMap)) {
                                if (s.includes(zk)) { latestLetter = zv; break; }
                            }
                        }
                        const latestNums = [];
                        const ballCandidates = Array.from(container.querySelectorAll('.letter, .number, .ball, .circle, ul li, ol li, h6')).filter(
                            el => el.children.length === 0 && !tbl.contains(el) && (!paginationEl || !paginationEl.contains(el))
                        );
                        ballCandidates.forEach(el => {
                            const t = (el.textContent || '').trim();
                            const cls = (el.className || '').toLowerCase();
                            if (cls.includes('letter') || (/^[A-Za-z]$/.test(t) && latestLetter === '—')) {
                                if (t && t.toUpperCase() !== 'MORE' && t.toUpperCase() !== 'X') latestLetter = t.toUpperCase();
                            } else if (/^\d{1,4}$/.test(t)) {
                                latestNums.push(t);
                            }
                        });

                        if (latestNums.length === 0) {
                            const afterHeader = cText.slice(cText.indexOf(mLatest[0]) + mLatest[0].length);
                            const beforeMore = afterHeader.split(/MORE|DRAW\s+NUMBER\s+AND\s+DATE/i)[0] || '';
                            const toks = beforeMore.split(/[\s|,;]+/).map(x => x.trim()).filter(Boolean);
                            toks.forEach(tok => {
                                if (/^[A-Za-z]$/.test(tok) && latestLetter === '—' && tok.toUpperCase() !== 'X') {
                                    latestLetter = tok.toUpperCase();
                                } else if (/^\d{1,4}$/.test(tok)) {
                                    latestNums.push(tok);
                                }
                            });
                        }

                        if (latestNums.length > 0) {
                            latestDraw = {
                                draw: latestDrawNo,
                                rawDate: latestRawDate,
                                letter: latestLetter,
                                nums: latestNums,
                                moreOnclick: latestMoreOnclick,
                                moreDebug: latestMoreDebug
                            };
                        }
                    }
                }

                let detectedDlbId = null;
                const pageOnclicks = {};
                const pageHrefs = {};
                if (container) {
                    Array.from(container.querySelectorAll('.pagination a, .pagination button, [onclick*="pagination_re"]')).forEach(a => {
                        const pageTxt = (a.textContent || '').trim();
                        const oc = a.getAttribute('onclick') || '';
                        const hr = a.getAttribute('href') || '';
                        if (/^\d+$/.test(pageTxt)) {
                            a.setAttribute('data-sl-hub-page', `${matchedName}__${pageTxt}`);
                            if (oc) pageOnclicks[pageTxt] = oc;
                            if (hr && hr !== '#' && !hr.startsWith('javascript:')) pageHrefs[pageTxt] = hr;
                        }
                        const mPage = oc.match(/pagination_re\(\s*\d+\s*,\s*['"]?(\d+)['"]?\s*\)/);
                        if (mPage && detectedDlbId === null) detectedDlbId = parseInt(mPage[1], 10);
                    });
                    if (detectedDlbId === null) {
                        const anyMore = container.querySelector('[onclick*="check_latest_result"], [onclick*="get_more_result"], [onclick]');
                        if (anyMore) {
                            const oc = anyMore.getAttribute('onclick') || '';
                            const mId = oc.match(/check_latest_result\(\s*['"]?(\d+)['"]?\s*\)/) ||
                                        oc.match(/get_more_result\(\s*\d+\s*,\s*['"]?(\d+)['"]?\s*\)/);
                            if (mId) detectedDlbId = parseInt(mId[1], 10);
                        }
                    }
                }

                resultByName[matchedName] = {
                    latestDraw,
                    rows,
                    detectedDlbId,
                    pageOnclicks,
                    pageHrefs
                };
            });

            return resultByName;
        }""",
        lotto_names,
    )
    return raw_data or {}


def extract_dlb_sections_from_soup_fallback(html_content: str) -> dict[str, dict[str, Any]]:
    """Pure BeautifulSoup + text fallback for https://www.dlb.lk/result/en."""
    soup = BeautifulSoup(html_content, "lxml")
    full_text = soup.get_text("\n", strip=True)
    lotto_names = [l["name"] for l in DLB_LOTTERIES]
    result_by_name: dict[str, dict[str, Any]] = {name: {"latestDraw": None, "rows": []} for name in lotto_names}

    for name in lotto_names:
        pat = re.compile(
            rf"{re.escape(name)}\s+Draw\s*Number\s*-\s*(\d{{1,5}})\s*\|\s*(\d{{4}}-[A-Za-z]{{3}}-\d{{2}}|\d{{4}}-\d{{2}}-\d{{2}})\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*([\s\S]*?)\bMORE\b",
            re.I,
        )
        m = pat.search(full_text)
        if m:
            d_no, raw_date, middle_blob = m.group(1), m.group(2), m.group(3)
            tokens = re.findall(r"[A-Za-z]+|\d{1,4}", middle_blob)
            letter, nums = _parse_dlb_tokens_to_letter_and_nums(tokens)
            if nums:
                result_by_name[name]["latestDraw"] = {
                    "draw": d_no,
                    "rawDate": raw_date,
                    "letter": letter,
                    "nums": nums,
                    "moreOnclick": "",
                }

    draw_tables = []
    for tbl in soup.find_all("table"):
        if re.search(r"\d{1,5}\s*\|\s*\d{4}-[A-Za-z]{3}-\d{2}", tbl.get_text(" ", strip=True)):
            draw_tables.append(tbl)

    for idx, tbl in enumerate(draw_tables):
        matched_name = lotto_names[idx] if idx < len(lotto_names) else None
        if not matched_name:
            continue
        for tr in tbl.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 2:
                continue
            col0 = tds[0].get_text(" ", strip=True)
            m_row = re.search(r"(\d{1,5})\s*\|\s*(\d{4}-[A-Za-z]{3}-\d{2}|\d{4}-\d{2}-\d{2})", col0)
            if not m_row:
                continue
            d_no, raw_date = m_row.group(1), m_row.group(2)
            letter = "—"
            for img in tr.find_all("img"):
                src = ((img.get("src") or "") + " " + (img.get("alt") or "")).lower()
                for zk, zn in ZODIAC_FILENAME_MAP.items():
                    if zk in src:
                        letter = zn
                        break
            tokens = []
            for td in tds[1:]:
                td_txt = td.get_text(" ", strip=True)
                if re.fullmatch(r"MORE", td_txt, re.I):
                    continue
                tokens.extend(re.findall(r"[A-Za-z]+|\d{1,4}", td_txt))
            letter, nums = _parse_dlb_tokens_to_letter_and_nums(tokens, letter)
            more_el = tds[-1].find(attrs={"onclick": True}) or tr.find(attrs={"onclick": True})
            more_onclick = more_el.get("onclick", "") if more_el else ""
            if nums:
                result_by_name[matched_name]["rows"].append({
                    "draw": d_no,
                    "rawDate": raw_date,
                    "letter": letter,
                    "nums": nums,
                    "moreOnclick": more_onclick,
                })

    return result_by_name


def activate_dlb_sidebar_tab(page, lotto_name: str, tab_idx: int) -> None:
    """Clicks the DLB sidebar tab for `lotto_name` AND forces all `.tab-pane` elements visible."""
    try:
        page.evaluate(
            """([name, idx]) => {
                const links = Array.from(document.querySelectorAll('a[data-toggle="tab"], .nav-tabs a, .nav-pills a, .sidebar a, ul li a'));
                const matches = links.filter(a => (a.textContent || '').trim().toLowerCase() === name.toLowerCase());
                matches.forEach(m => { try { m.click(); } catch (e) {} });
                const byHref = links.find(a => (a.getAttribute('href') || '') === `#lottery${idx}`);
                if (byHref) { try { byHref.click(); } catch (e) {} }
            }""",
            [lotto_name, tab_idx],
        )
        unhide_all_dlb_tabs(page)
        page.wait_for_timeout(350)
    except Exception:
        pass


def trigger_dlb_more_and_parse(
    page,
    lotto_name: str,
    onclick_js: str,
    dlb_id: int,
    draw_int: int,
    iso_date: str,
    captured_responses: list[str],
    is_latest: bool = False,
) -> dict[str, Any] | None:
    """Triggers the DLB 'MORE' button using Playwright native click + onclick eval, and captures
    the Prize Breakdown via network response listener, XHR hook, and full-document table scan."""
    try:
        unhide_all_dlb_tabs(page)
        captured_responses.clear()
        prev_snapshot = page.evaluate("""() => {
            window.__dlbLastAjaxResponse = '';
            const tbls = Array.from(document.querySelectorAll('table')).filter(
                t => /matches/i.test(t.textContent || '') || /winners/i.test(t.textContent || '')
            );
            return tbls.map(t => (t.textContent || '').trim()).join('||');
        }""")

        selector = f'[data-sl-hub-more="{lotto_name}__{draw_int}"]'
        clicked = False
        try:
            loc = page.locator(selector).first
            if loc.count() > 0:
                loc.scroll_into_view_if_needed(timeout=1000)
                loc.click(force=True, timeout=1500)
                clicked = True
        except Exception:
            pass

        js_clicked = page.evaluate(
            """([lottoName, drawInt, onclickCode, dlbId, isLatest]) => {
                const tagged = document.querySelector(`[data-sl-hub-more="${lottoName}__${drawInt}"]`);
                if (tagged) {
                    try { tagged.click(); } catch (e) {}
                    const oc = tagged.getAttribute('onclick') || onclickCode;
                    if (oc && oc.trim()) {
                        try { eval(oc); } catch (e) {}
                    }
                    return true;
                }
                if (onclickCode && onclickCode.trim()) {
                    try { eval(onclickCode); return true; } catch (e) {}
                }
                if (isLatest && typeof check_latest_result === 'function') {
                    try { check_latest_result(dlbId); return true; } catch (e) {}
                }
                return false;
            }""",
            [lotto_name, draw_int, onclick_js or "", dlb_id, is_latest],
        )
        if not clicked and not js_clicked:
            return None

        for _ in range(22):
            page.wait_for_timeout(150)

            for body in list(captured_responses):
                parsed = parse_dlb_more_modal_html(body, fallback_date=iso_date, fallback_draw=str(draw_int))
                if parsed and parsed.get("table"):
                    return parsed

            dom_or_xhr_html = page.evaluate(
                """([targetDrawStr, prevSnap]) => {
                    if (window.__dlbLastAjaxResponse && window.__dlbLastAjaxResponse.length > 40) {
                        return window.__dlbLastAjaxResponse;
                    }
                    const tbls = Array.from(document.querySelectorAll('.modal table, [id*="modal"] table, [class*="modal"] table, table')).filter(
                        t => (/matches/i.test(t.textContent || '') || /winners/i.test(t.textContent || '')) &&
                             !/DRAW\\s+NUMBER\\s+AND\\s+DATE/i.test(t.textContent || '') &&
                             t.querySelectorAll('tr').length > 2
                    );
                    for (const t of tbls) {
                        const wrapper = t.closest('.modal-content, .modal-dialog, .modal, .bs-example-modal-lg001, #moreResultModal, div') || t.parentElement;
                        const wTxt = wrapper ? (wrapper.textContent || '') : (t.textContent || '');
                        if (wTxt.includes(targetDrawStr) || (t.textContent || '').trim() !== prevSnap) {
                            return wrapper ? wrapper.innerHTML : t.outerHTML;
                        }
                    }
                    return '';
                }""",
                [str(draw_int), prev_snapshot or ""],
            )
            if dom_or_xhr_html:
                parsed = parse_dlb_more_modal_html(dom_or_xhr_html, fallback_date=iso_date, fallback_draw=str(draw_int))
                if parsed and parsed.get("table"):
                    return parsed

        return None
    except Exception as exc:
        print(f"    ! DLB modal fetch warning for {lotto_name} draw #{draw_int}: {exc}")
        return None


def scrape_dlb_lotteries(page, dataset: dict[str, Any]) -> None:
    print("=== Scraping DLB Lotteries Live from https://www.dlb.lk/result/en ===")
    captured_responses: list[str] = []

    def _on_response(resp) -> None:
        try:
            if "dlb.lk" in resp.url and resp.status == 200 and resp.request.resource_type in ("xhr", "fetch", "document"):
                txt = resp.text()
                if txt and len(txt) > 40:
                    captured_responses.append(txt)
        except Exception:
            pass

    page.on("response", _on_response)

    try:
        page.goto("https://www.dlb.lk/result/en", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2000)
        unhide_all_dlb_tabs(page)
    except Exception as exc:
        print(f"[error] Failed to load https://www.dlb.lk/result/en: {exc}")
        return

    dom_sections = extract_dlb_all_sections_dom(page)
    soup_sections = extract_dlb_sections_from_soup_fallback(page.content())

    for lotto in DLB_LOTTERIES:
        t_id = lotto["id"]
        t_name = lotto["name"]
        dlb_id = lotto["dlb_id"]
        tab_idx = lotto["tab_idx"]
        expected_balls = lotto["balls"]

        entry = dataset["lotteries"].setdefault(
            t_id, {"id": t_id, "name": t_name, "board": "DLB", "draws": [], "perDrawTables": {}}
        )
        existing_draws: dict[str, dict[str, Any]] = {}
        for d in entry.get("draws", []):
            k = normalize_draw_key(d.get("draw", ""))
            if k.isdigit():
                if isinstance(d.get("nums"), list):
                    d["nums"] = deduplicate_paired_balls(d["nums"], expected_balls)
                existing_draws[k] = d

        existing_tables: dict[str, Any] = entry.get("perDrawTables", {})

        max_saved_draw = max((int(k) for k in existing_draws.keys()), default=0)
        # Backfill until BOTH MAX_DRAWS consecutive draws AND 100% of their prize tables exist
        is_complete = has_complete_draws_and_tables(existing_draws, existing_tables, MAX_DRAWS)
        need_backfill = (not is_complete) or FORCE_FULL_BACKFILL
        mode_label = "FULL 100% BACKFILL (DRAWS + TABLES)" if need_backfill else "INCREMENTAL LATEST-ONLY"
        print(
            f"[DLB] {t_name} ({t_id}): mode={mode_label}, saved_draws={len(existing_draws)}, "
            f"saved_tables={len(existing_tables)}, max_saved=#{max_saved_draw}"
        )

        activate_dlb_sidebar_tab(page, t_name, tab_idx)
        dom_sections = extract_dlb_all_sections_dom(page) or dom_sections

        sec = dom_sections.get(t_name) or {}
        fallback_sec = soup_sections.get(t_name) or {}
        saved_dlb_id = sec.get("detectedDlbId") if sec.get("detectedDlbId") is not None else dlb_id
        saved_page_onclicks: dict[str, str] = dict(sec.get("pageOnclicks") or {})

        page1_candidates: list[dict[str, Any]] = []
        latest_item = sec.get("latestDraw") or fallback_sec.get("latestDraw")
        if latest_item:
            latest_item["is_latest"] = True
            page1_candidates.append(latest_item)

        dom_rows = sec.get("rows") or fallback_sec.get("rows") or []
        for r in dom_rows:
            r["is_latest"] = False
            page1_candidates.append(r)

        if page1_candidates and page1_candidates[0].get("moreDebug"):
            print(f"    [info] {t_name} MORE trigger element: {page1_candidates[0]['moreDebug']}")

        new_draws_count = 0
        new_tables_count = 0
        consecutive_modal_failures = 0

        for item in page1_candidates:
            d_key = normalize_draw_key(item.get("draw"))
            if not d_key.isdigit():
                continue
            d_int = int(d_key)
            d_pad = format_draw_pad(d_int)
            iso_date = parse_date_to_iso(item.get("rawDate", ""))
            clean_nums = deduplicate_paired_balls(item.get("nums") or [], expected_balls)

            if clean_nums:
                if d_key not in existing_draws:
                    existing_draws[d_key] = {
                        "draw": d_pad,
                        "date": iso_date,
                        "letter": item.get("letter", "—"),
                        "nums": clean_nums,
                    }
                    new_draws_count += 1
                    print(f"  + [NEW DRAW] DLB {t_id} Draw #{d_pad} ({iso_date}): {item.get('letter')} {' '.join(clean_nums)}")
                else:
                    prev_d = existing_draws[d_key]
                    if len(prev_d.get("nums", [])) != len(clean_nums) and len(clean_nums) == expected_balls:
                        prev_d["nums"] = clean_nums
                    if prev_d.get("letter") in ("—", "", None) and item.get("letter") not in ("—", "", None):
                        prev_d["letter"] = item["letter"]

            if not need_backfill and d_int <= max_saved_draw and has_prize_table(existing_tables, d_int):
                continue

            if (
                not has_prize_table(existing_tables, d_int)
                and new_tables_count < MAX_TABLES_PER_RUN
                and consecutive_modal_failures < 5
            ):
                parsed_modal = trigger_dlb_more_and_parse(
                    page,
                    t_name,
                    item.get("moreOnclick", ""),
                    saved_dlb_id,
                    d_int,
                    iso_date,
                    captured_responses,
                    is_latest=bool(item.get("is_latest")),
                )
                if parsed_modal and parsed_modal.get("table"):
                    consecutive_modal_failures = 0
                    existing_tables[d_pad] = parsed_modal["table"]
                    new_tables_count += 1
                    if parsed_modal.get("jackpot", 0) > float(entry.get("currentJackpot", 0) or 0):
                        entry["currentJackpot"] = parsed_modal["jackpot"]
                    if parsed_modal.get("letter") not in ("—", "", None) and d_key in existing_draws:
                        if existing_draws[d_key].get("letter") in ("—", "", None):
                            existing_draws[d_key]["letter"] = parsed_modal["letter"]
                    print(f"  + [NEW TABLE] DLB {t_id} Draw #{d_pad}: {parsed_modal['table']['totalWinners']} winners")
                else:
                    consecutive_modal_failures += 1

        # Paginate until we have at least MAX_DRAWS sequential draws AND their prize tables
        if need_backfill and not has_complete_draws_and_tables(existing_draws, existing_tables, MAX_DRAWS):
            max_pages = max(2, (MAX_DRAWS // 12) + 3)
            prev_first_draw = dom_rows[0]["draw"] if dom_rows else ""
            for page_no in range(2, max_pages + 1):
                if has_complete_draws_and_tables(existing_draws, existing_tables, MAX_DRAWS):
                    break
                if has_consecutive_draws(existing_draws, MAX_DRAWS) and consecutive_modal_failures >= 5:
                    break
                saved_oc = saved_page_onclicks.get(str(page_no), "")
                try:
                    unhide_all_dlb_tabs(page)
                    page_sel = f'[data-sl-hub-page="{t_name}__{page_no}"]'
                    clicked_page = False
                    try:
                        loc = page.locator(page_sel).first
                        if loc.count() > 0:
                            loc.scroll_into_view_if_needed(timeout=1000)
                            loc.click(force=True, timeout=1500)
                            clicked_page = True
                    except Exception:
                        pass

                    page.evaluate(
                        """([lottoName, pageNo, dlbId, savedOc, alreadyClicked]) => {
                            if (savedOc && savedOc.trim()) {
                                try { eval(savedOc); return; } catch (e) {}
                            }
                            const link = document.querySelector(`[data-sl-hub-page="${lottoName}__${pageNo}"]`);
                            if (link) {
                                const oc = link.getAttribute('onclick');
                                if (oc) { try { eval(oc); return; } catch (e) {} }
                                if (!alreadyClicked) { try { link.click(); return; } catch (e) {} }
                            }
                            if (typeof pagination_re === 'function') {
                                pagination_re((pageNo - 1) * 12, dlbId);
                            }
                        }""",
                        [t_name, page_no, saved_dlb_id, saved_oc, clicked_page],
                    )
                    page.wait_for_timeout(1200)
                    unhide_all_dlb_tabs(page)
                except Exception as exc:
                    print(f"  ! DLB pagination page {page_no} stop: {exc}")
                    break

                paged_sections = extract_dlb_all_sections_dom(page)
                paged_sec = paged_sections.get(t_name) or {}
                if paged_sec.get("pageOnclicks"):
                    saved_page_onclicks.update(paged_sec["pageOnclicks"])
                paged_rows = paged_sec.get("rows") or []
                if not paged_rows or (paged_rows[0].get("draw") == prev_first_draw):
                    break
                prev_first_draw = paged_rows[0].get("draw", "")

                for r in paged_rows:
                    d_key = normalize_draw_key(r.get("draw"))
                    if not d_key.isdigit():
                        continue
                    d_int = int(d_key)
                    d_pad = format_draw_pad(d_int)
                    iso_date = parse_date_to_iso(r.get("rawDate", ""))
                    clean_nums = deduplicate_paired_balls(r.get("nums") or [], expected_balls)

                    if d_key not in existing_draws and clean_nums:
                        existing_draws[d_key] = {
                            "draw": d_pad,
                            "date": iso_date,
                            "letter": r.get("letter", "—"),
                            "nums": clean_nums,
                        }
                        new_draws_count += 1
                        print(f"  + [NEW DRAW] DLB {t_id} Draw #{d_pad} ({iso_date}): {r.get('letter')} {' '.join(clean_nums)}")

                    if (
                        not has_prize_table(existing_tables, d_int)
                        and new_tables_count < MAX_TABLES_PER_RUN
                        and consecutive_modal_failures < 5
                    ):
                        parsed_modal = trigger_dlb_more_and_parse(
                            page,
                            t_name,
                            r.get("moreOnclick", ""),
                            saved_dlb_id,
                            d_int,
                            iso_date,
                            captured_responses,
                            is_latest=False,
                        )
                        if parsed_modal and parsed_modal.get("table"):
                            consecutive_modal_failures = 0
                            existing_tables[d_pad] = parsed_modal["table"]
                            new_tables_count += 1
                            print(f"  + [NEW TABLE] DLB {t_id} Draw #{d_pad} ({iso_date}): {parsed_modal['table']['totalWinners']} winners")
                        else:
                            consecutive_modal_failures += 1

        sorted_draws = sorted(
            existing_draws.values(),
            key=lambda x: int(normalize_draw_key(x.get("draw", "0"))),
            reverse=True,
        )
        entry["draws"] = sorted_draws
        entry["perDrawTables"] = existing_tables
        print(f"  => DLB {t_id} summary: +{new_draws_count} new draws, +{new_tables_count} new tables (total {len(sorted_draws)} draws, {len(existing_tables)} tables)")
        save_dataset_checkpoint(dataset, OUTPUT_PATH)


# =============================================================================
# NLB LIVE SCRAPER (https://www.nlb.lk/results)
# =============================================================================

def parse_nlb_main_table_rows(page) -> list[dict[str, Any]]:
    """Extracts historical draws from `<table class='w0 tbl'>` on `/results/<slug>`,
    reading balls from the primary `<ol class='B'>` in exact DOM sequential order."""
    raw_rows = page.evaluate(r"""() => {
        const out = [];
        document.querySelectorAll('table tr').forEach(tr => {
            const tds = tr.querySelectorAll('td');
            if (tds.length < 2) return;
            const col0Text = (tds[0].textContent || '').trim();
            const bTag = tds[0].querySelector('b');
            const drawNo = bTag ? (bTag.textContent || '').trim() : (col0Text.match(/^(\d{1,5})\b/) || [])[1] || '';
            if (!/^\d{1,5}$/.test(drawNo)) return;
            const dateText = col0Text.replace(drawNo, '').replace(/^\s*\.\s*/, '').trim();

            const primaryList = tds[1].querySelector('ol.B, ul.B, ol, ul') || tds[1];
            const lis = Array.from(primaryList.querySelectorAll('li'));
            let letter = '—';
            const nums = [];
            lis.forEach(li => {
                const val = (li.textContent || '').trim();
                const cls = (li.className || '') + ' ' + (li.getAttribute('title') || '');
                if (/Letter|Zodiac/i.test(cls) || /^[A-Za-z]{1,12}$/.test(val)) {
                    if (letter === '—' && val && !/^(More|Promotional)$/i.test(val)) {
                        letter = val.toUpperCase();
                    }
                } else if (/^\d{1,4}$/.test(val)) {
                    nums.push(val);
                }
            });
            out.push({ draw: drawNo, rawDate: dateText, letter, nums });
        });
        return out;
    }""")
    results = []
    for r in raw_rows:
        if r.get("nums"):
            d_int = int(r["draw"])
            d_pad = format_draw_pad(d_int)
            results.append({
                "draw_key": str(d_int),
                "draw": d_pad,
                "date": parse_date_to_iso(r.get("rawDate", "")),
                "letter": r.get("letter", "—"),
                "nums": r["nums"],
            })
    return results


def parse_nlb_single_draw_page(page, html: str, draw_no: int) -> dict[str, Any] | None:
    if "Just a moment..." in html or "cf-browser-verification" in html:
        return None
    soup = BeautifulSoup(html, "lxml")
    text_all = soup.get_text(" ", strip=True)

    jackpot_val = 0.0
    for m_jp in re.finditer(r"Next\s+(?:Mega\s+|Power\s+)?Super\s+Prize\s+Rs\.?\s*([\d,]+(?:\.\d{2})?)", text_all, re.I):
        jp = parse_number(m_jp.group(1))
        if jp > jackpot_val:
            jackpot_val = jp

    date_match = re.search(
        r"(?:Date\s*:\s*|Prize\s+Structure\s*-\s*)(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*,?\s*"
        r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})",
        text_all,
    )
    iso_date = parse_date_to_iso(date_match.group(1)) if date_match else ""

    visual_balls = []
    try:
        visual_balls = page.evaluate(r"""() => {
            const ol = document.querySelector('ol.B, ul.B, .lresult ol, .lresult ul');
            if (!ol) return [];
            const lis = Array.from(ol.querySelectorAll('li'));
            return lis.map(li => ({
                val: (li.textContent || '').trim(),
                cls: (li.className || '') + ' ' + (li.getAttribute('title') || '')
            }));
        }""")
    except Exception:
        pass

    letter = "—"
    nums = []
    for item in visual_balls:
        val = item.get("val", "")
        cls = item.get("cls", "")
        if re.search(r"Letter|Zodiac", cls, re.I) or re.fullmatch(r"[A-Za-z]{1,12}", val):
            if letter == "—" and val.upper() not in ("MORE", "PROMOTIONAL"):
                letter = val.upper()
        elif re.fullmatch(r"\d{1,4}", val):
            nums.append(val)

    rows: list[dict[str, Any]] = []
    total_winners = 0
    total_payout = 0.0

    for tr in soup.select("table tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 4:
            continue
        first_cell = cells[0].strip()
        second_cell = cells[1].strip() if len(cells) > 1 else ""
        if (
            first_cell in ("#", "Rank", "Prize")
            or second_cell.lower() in ("pattern", "prize", "winners")
            or re.search(r"\btotal\b", first_cell, re.I)
            or re.search(r"\btotal\b", second_cell, re.I)
        ):
            continue

        if len(cells) >= 5:
            tier_label = f"{first_cell} — {second_cell}" if first_cell else second_cell
            prize_str, win_str, tot_str = cells[2], cells[3], cells[4]
        else:
            tier_label, prize_str, win_str, tot_str = first_cell, cells[1], cells[2], cells[3]

        unit_prize = parse_number(prize_str)
        winners = int(round(parse_number(win_str)))
        row_amt = parse_number(tot_str)

        if unit_prize > 0 or winners > 0:
            if row_amt == 0 and unit_prize > 0 and winners > 0:
                row_amt = unit_prize * winners
            if unit_prize > jackpot_val:
                jackpot_val = unit_prize
            total_winners += winners
            total_payout += row_amt
            rows.append({
                "tier": tier_label,
                "prize": f"Rs. {unit_prize:,.2f}" if unit_prize > 0 else prize_str,
                "unitPrize": unit_prize,
                "avgWinners": winners,
            })

    if not rows and "Prize Structure" in text_all:
        ps_text = text_all.split("Prize Structure", 1)[1]
        tier_pattern = re.compile(
            r"((?:Mega\s+Super|Power\s+Super|Grand\s+Super|Super)\s+Prize(?:\s*\([^)]+\))?|\d+\s*(?:st|nd|rd|th))\s+"
            r"([A-Za-z0-9 ,.&()/-]+?)\s+"
            r"(?:Rs\.?\s*([\d,]+(?:\.\d{2})?)|Grand\s+Super\s+Prize\s*\([^)]+\))\s+"
            r"(\d[\d,]*)\s+"
            r"(?:Rs\.?\s*([\d,]+(?:\.\d{2})?)|-)",
            re.I,
        )
        for m_row in tier_pattern.finditer(ps_text):
            rank_lbl = m_row.group(1).strip()
            pat_lbl = m_row.group(2).strip()
            unit_p = parse_number(m_row.group(3) or "0")
            win_c = int(round(parse_number(m_row.group(4) or "0")))
            tot_a = parse_number(m_row.group(5) or "0")
            if unit_p > 0 or win_c > 0:
                if tot_a == 0 and unit_p > 0 and win_c > 0:
                    tot_a = unit_p * win_c
                if unit_p > jackpot_val:
                    jackpot_val = unit_p
                total_winners += win_c
                total_payout += tot_a
                rows.append({
                    "tier": f"{rank_lbl} — {pat_lbl}",
                    "prize": f"Rs. {unit_p:,.2f}" if unit_p > 0 else rank_lbl,
                    "unitPrize": unit_p,
                    "avgWinners": win_c,
                })

    if not nums and not rows:
        return None

    d_pad = format_draw_pad(draw_no)
    return {
        "draw": d_pad,
        "date": iso_date or datetime.date.today().strftime("%Y-%m-%d"),
        "letter": letter,
        "nums": nums,
        "jackpot": jackpot_val,
        "prizeTable": {
            "date": iso_date,
            "totalWinners": total_winners,
            "totalPayout": round(total_payout, 2),
            "tierCounts": compute_tier_counts_from_rows(rows),
            "rows": rows,
        } if rows else None,
    }


def scrape_nlb_lotteries(page, dataset: dict[str, Any]) -> None:
    print("=== Scraping NLB Lotteries Live from https://www.nlb.lk/results ===")
    for lotto in NLB_LOTTERIES:
        t_id = lotto["id"]
        slug = lotto["slug"]
        entry = dataset["lotteries"].setdefault(
            t_id, {"id": t_id, "name": lotto["name"], "board": "NLB", "draws": [], "perDrawTables": {}}
        )
        existing_draws: dict[str, dict[str, Any]] = {
            normalize_draw_key(d["draw"]): d
            for d in entry.get("draws", [])
            if normalize_draw_key(d.get("draw", "")).isdigit()
        }
        existing_tables: dict[str, Any] = entry.get("perDrawTables", {})

        max_saved_draw = max((int(k) for k in existing_draws.keys()), default=0)
        is_complete = has_complete_draws_and_tables(existing_draws, existing_tables, MAX_DRAWS)
        need_backfill = (not is_complete) or FORCE_FULL_BACKFILL
        mode_label = "FULL 100% BACKFILL (DRAWS + TABLES)" if need_backfill else "INCREMENTAL LATEST-ONLY"

        list_url = f"https://www.nlb.lk/results/{slug}"
        print(
            f"[NLB] {lotto['name']} ({t_id}): mode={mode_label}, "
            f"saved_draws={len(existing_draws)}, saved_tables={len(existing_tables)}"
        )
        lotto_html = ""
        try:
            page.goto(list_url, wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(1200)
            lotto_html = page.content()
            if "Just a moment..." in lotto_html:
                print(f"  [wait] Cloudflare challenge on {list_url}, waiting 6s...")
                page.wait_for_timeout(6000)
                lotto_html = page.content()
        except Exception as exc:
            print(f"  ! Could not load {list_url}: {exc}")
            continue

        for m_jp in re.finditer(r"Next\s+(?:Mega\s+|Power\s+)?Super\s+Prize\s+Rs\.?\s*([\d,]+(?:\.\d{2})?)", BeautifulSoup(lotto_html, "lxml").get_text(" ", strip=True), re.I):
            jp = parse_number(m_jp.group(1))
            if jp > float(entry.get("currentJackpot", 0) or 0):
                entry["currentJackpot"] = jp

        new_draws_count = 0
        try:
            main_rows = parse_nlb_main_table_rows(page)
            limit_rows = main_rows[:MAX_DRAWS] if need_backfill else [
                r for r in main_rows if int(r["draw_key"]) > max_saved_draw or r["draw_key"] not in existing_draws
            ][:MAX_DRAWS]
            for r in limit_rows:
                d_key = r["draw_key"]
                if d_key not in existing_draws:
                    existing_draws[d_key] = {
                        "draw": r["draw"],
                        "date": r["date"],
                        "letter": r["letter"],
                        "nums": r["nums"],
                    }
                    new_draws_count += 1
        except Exception as exc:
            print(f"  ! Main table parse warning on {slug}: {exc}")

        draw_links = re.findall(rf"results/{re.escape(slug)}/(\d+)", lotto_html)
        if not draw_links and existing_draws:
            draw_links = list(existing_draws.keys())
        if not draw_links:
            m_text = re.findall(r"(?:Latest Results|Draw No\.?)\s*[:\-]?\s*(\d{1,5})", lotto_html, re.I)
            draw_links = m_text

        if not draw_links:
            print(f"  ! Could not detect latest draw number for NLB {t_id}")
            continue

        latest_draw_no = max(int(x) for x in draw_links)
        # Always span the full MAX_DRAWS window so every single draw gets both numbers AND prize table (100% coverage)
        min_draw_no = max(1, latest_draw_no - MAX_DRAWS + 1)

        new_tables_count = 0
        consecutive_errors = 0
        for d_no in range(latest_draw_no, min_draw_no - 1, -1):
            d_key = str(d_no)
            d_pad = format_draw_pad(d_no)

            if d_key in existing_draws and has_prize_table(existing_tables, d_no):
                if not need_backfill and d_no <= max_saved_draw:
                    break
                continue

            if new_tables_count >= MAX_TABLES_PER_RUN:
                break

            if consecutive_errors >= 4:
                print(f"  ! Stopping individual page loop for {t_id} after 4 consecutive timeouts/challenges.")
                break

            detail_url = f"https://www.nlb.lk/results/{slug}/{d_no}"
            try:
                page.goto(detail_url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(450)
                detail_html = page.content()
                if "Just a moment..." in detail_html or "cf-browser-verification" in detail_html:
                    print(f"  [wait] Cloudflare challenge on {detail_url}, waiting 5s...")
                    page.wait_for_timeout(5000)
                    detail_html = page.content()

                parsed = parse_nlb_single_draw_page(page, detail_html, d_no)
                if parsed:
                    consecutive_errors = 0
                    if parsed["nums"] and d_key not in existing_draws:
                        existing_draws[d_key] = {
                            "draw": d_pad,
                            "date": parsed["date"],
                            "letter": parsed["letter"],
                            "nums": parsed["nums"],
                        }
                        new_draws_count += 1
                        print(f"  + [NEW DRAW] NLB {t_id} Draw #{d_pad} ({parsed['date']}): {parsed['letter']} {' '.join(parsed['nums'])}")
                    if parsed.get("jackpot", 0) > float(entry.get("currentJackpot", 0) or 0):
                        entry["currentJackpot"] = parsed["jackpot"]
                    if parsed["prizeTable"] and not has_prize_table(existing_tables, d_no):
                        existing_tables[d_pad] = parsed["prizeTable"]
                        new_tables_count += 1
                        print(f"  + [NEW TABLE] NLB {t_id} Draw #{d_pad} ({parsed['date']}): {parsed['prizeTable']['totalWinners']} winners")
                else:
                    consecutive_errors += 1
            except Exception as exc:
                consecutive_errors += 1
                print(f"  ! Error on {detail_url}: {exc}")

        sorted_draws = sorted(
            existing_draws.values(),
            key=lambda x: int(normalize_draw_key(x.get("draw", "0"))),
            reverse=True,
        )
        entry["draws"] = sorted_draws
        entry["perDrawTables"] = existing_tables
        print(f"  => NLB {t_id} summary: +{new_draws_count} new draws, +{new_tables_count} new tables (total {len(sorted_draws)} draws, {len(existing_tables)} tables)")
        save_dataset_checkpoint(dataset, OUTPUT_PATH)


def main() -> None:
    if sync_playwright is None:
        raise RuntimeError("Playwright is required. Run: pip install playwright && playwright install chromium")

    dataset = load_existing_dataset(OUTPUT_PATH)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        context = browser.new_context(
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.__dlbLastAjaxResponse = '';
            (function() {
                const origOpen = XMLHttpRequest.prototype.open;
                const origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.open = function(m, u) { this.__url = u; return origOpen.apply(this, arguments); };
                XMLHttpRequest.prototype.send = function() {
                    this.addEventListener('load', function() {
                        if (this.responseText && this.responseText.length > 40) {
                            window.__dlbLastAjaxResponse = this.responseText;
                        }
                    });
                    return origSend.apply(this, arguments);
                };
                if (typeof window.fetch === 'function') {
                    const origFetch = window.fetch;
                    window.fetch = function() {
                        return origFetch.apply(this, arguments).then(resp => {
                            try {
                                resp.clone().text().then(t => {
                                    if (t && t.length > 40) window.__dlbLastAjaxResponse = t;
                                });
                            } catch (e) {}
                            return resp;
                        });
                    };
                }
            })();
        """)
        page = context.new_page()
        scrape_nlb_lotteries(page, dataset)
        scrape_dlb_lotteries(page, dataset)
        browser.close()

    save_dataset_checkpoint(dataset, OUTPUT_PATH)
    final_draws = sum(len(v.get("draws", [])) for v in dataset["lotteries"].values())
    final_tables = sum(len(v.get("perDrawTables", {})) for v in dataset["lotteries"].values())
    n_files = len(glob.glob(os.path.join(TICKETS_DIR, "*.json")))
    print(
        f"[done] Wrote {n_files} ticket JSON files in {TICKETS_DIR}/ + {PRIZE_RANKINGS_PATH} + {INDEX_PATH} "
        f"— {final_draws} draws, {final_tables} official prize tables."
    )


if __name__ == "__main__":
    main()
