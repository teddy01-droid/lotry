#!/usr/bin/env python3
"""Official NLB & DLB 365-Draw Scraper for GitHub Actions.

Scrapes 100% real draw results (Draw No, Draw Date, Letter/Zodiac Sign, Winning
Numbers) and exact official Prize Breakdown ("MORE") tables for all 18 active
Sri Lankan lotteries:
  - 9 NLB Lotteries (https://www.nlb.lk/results/<slug>/<draw_no>)
  - 9 DLB Lotteries (https://www.dlb.lk/result/en + AJAX get_more_result / pagination)

Incremental caching:
  Reads existing `data/sl_lottery_365_official.json` first so already-fetched
  historical draws are preserved and only missing/new draws up to
  MAX_DRAWS_PER_TICKET (default: 365) are fetched on each run.
"""

import datetime
import json
import os
import re
import time
from typing import Any
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

MAX_DRAWS = int(os.environ.get("MAX_DRAWS_PER_TICKET", "365"))
OUTPUT_PATH = os.environ.get("OUTPUT_JSON_PATH", "data/sl_lottery_365_official.json")

NLB_LOTTERIES = [
    {"id": "mahajana-sampatha", "name": "Mahajana Sampatha", "slug": "mahajana-sampatha"},
    {"id": "govisetha", "name": "Govisetha", "slug": "govisetha"},
    {"id": "mega-power", "name": "Mega Power", "slug": "mega-power"},
    {"id": "dhana-nidhanaya", "name": "Dhana Nidhanaya", "slug": "dhana-nidhanaya"},
    {"id": "handahana", "name": "Handahana", "slug": "handahana"},
    {"id": "ada-sampatha", "name": "Ada Sampatha", "slug": "ada-sampatha"},
    {"id": "nlb-jaya", "name": "NLB Jaya", "slug": "nlb-jaya"},
    {"id": "suba-dawasak", "name": "Suba Dawasak", "slug": "suba-dawasak"},
    {"id": "ayubo", "name": "Ayubo", "slug": "ayubo"},
]

# Official DLB lottery_id mapping on https://www.dlb.lk/result/en
DLB_LOTTERIES = [
    {"id": "ada-kotipathi", "name": "Ada Kotipathi", "dlb_id": 11},
    {"id": "shanida", "name": "Shanida", "dlb_id": 1},
    {"id": "lagna-wasana", "name": "Lagna Wasana", "dlb_id": 2},
    {"id": "supiri-dhana-sampatha", "name": "Supiri Dhana Sampatha", "dlb_id": 3},
    {"id": "super-ball", "name": "Super Ball", "dlb_id": 4},
    {"id": "kapruka", "name": "Kapruka", "dlb_id": 5},
    {"id": "waasi", "name": "Waasi", "dlb_id": 6},
    {"id": "sasiri", "name": "Sasiri", "dlb_id": 7},
    {"id": "jaya-sampatha", "name": "Jaya Sampatha", "dlb_id": 8},
]

ZODIAC_FILENAME_MAP = {
    "aries": "ARIES",
    "taurus": "TAURUS",
    "gemini": "GEMINI",
    "cancer": "CANCER",
    "leo": "LEO",
    "virgo": "VIRGO",
    "libra": "LIBRA",
    "scorpio": "SCORPIO",
    "sagittarius": "SAGITTARIUS",
    "capricorn": "CAPRICORN",
    "aquarius": "AQUARIUS",
    "pisces": "PISCES",
}


def load_existing_dataset(path: str) -> dict[str, Any]:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict) and "lotteries" in data:
                    return data
        except Exception as exc:
            print(f"[warn] Could not parse existing JSON at {path}: {exc}")
    return {
        "schemaVersion": "2.0",
        "updatedAt": "",
        "maxDrawsTarget": MAX_DRAWS,
        "lotteries": {},
    }


def parse_number(val: str) -> float:
    cleaned = re.sub(r"[^0-9.]", "", str(val or ""))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def parse_nlb_draw_page(html: str, draw_no: int) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "lxml")
    # Extract draw date
    text_all = soup.get_text(" ", strip=True)
    date_match = re.search(
        r"(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*"
        r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})",
        text_all,
    )
    iso_date = ""
    if date_match:
        raw_d = date_match.group(1).strip()
        for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%m-%d"):
            try:
                iso_date = datetime.datetime.strptime(raw_d, fmt).strftime("%Y-%m-%d")
                break
            except ValueError:
                pass

    # Extract winning balls/letters
    balls_container = soup.select("ol.B li, ul.B li, .lresult li, .balls li")
    tokens = [li.get_text(strip=True) for li in balls_container if li.get_text(strip=True)]
    letter = "—"
    nums: list[str] = []
    for tok in tokens:
        if re.fullmatch(r"[A-Za-z]{1,12}", tok):
            letter = tok.upper()
        elif re.fullmatch(r"\d{1,4}", tok):
            nums.append(tok)

    # Extract official Prize Structure table
    rows = []
    total_winners = 0
    total_payout = 0.0
    for tr in soup.select("table tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) >= 4:
            tier_label = tds[0]
            if re.search(r"\btotal\b", tier_label, re.I):
                continue
            unit_prize = parse_number(tds[1])
            winners = int(round(parse_number(tds[2])))
            row_amt = parse_number(tds[3])
            if unit_prize > 0 or winners > 0:
                if row_amt == 0 and unit_prize > 0 and winners > 0:
                    row_amt = unit_prize * winners
                total_winners += winners
                total_payout += row_amt
                rows.append(
                    {
                        "tier": tier_label,
                        "prize": f"Rs. {unit_prize:,.2f}",
                        "unitPrize": unit_prize,
                        "avgWinners": winners,
                    }
                )

    if not nums and not rows:
        return None

    return {
        "draw": str(draw_no),
        "date": iso_date or datetime.date.today().strftime("%Y-%m-%d"),
        "letter": letter,
        "nums": nums,
        "prizeTable": {
            "date": iso_date,
            "totalWinners": total_winners,
            "totalPayout": round(total_payout, 2),
            "rows": rows,
        }
        if rows
        else None,
    }


def scrape_nlb_lotteries(page, dataset: dict[str, Any]) -> None:
    print("=== Scraping NLB Lotteries (https://www.nlb.lk/results) ===")
    page.goto("https://www.nlb.lk/results", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(2000)
    index_html = page.content()

    for lotto in NLB_LOTTERIES:
        t_id = lotto["id"]
        slug = lotto["slug"]
        entry = dataset["lotteries"].setdefault(
            t_id, {"id": t_id, "board": "NLB", "draws": [], "perDrawTables": {}}
        )
        existing_draws = {str(int(d["draw"])): d for d in entry["draws"] if str(d.get("draw", "")).isdigit()}
        existing_tables = entry.get("perDrawTables", {})

        # Find latest draw number on index or lottery page
        page.goto(f"https://www.nlb.lk/results/{slug}", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1200)
        lotto_html = page.content()
        draw_links = re.findall(rf"/results/{re.escape(slug)}/(\d+)", lotto_html + index_html)
        if not draw_links:
            print(f"[warn] Could not detect latest draw number for NLB {t_id}")
            continue

        latest_draw_no = max(int(x) for x in draw_links)
        min_draw_no = max(1, latest_draw_no - MAX_DRAWS + 1)
        print(f"[NLB] {lotto['name']}: Latest Draw #{latest_draw_no}, target range #{latest_draw_no}..#{min_draw_no}")

        for d_no in range(latest_draw_no, min_draw_no - 1, -1):
            d_key = str(d_no)
            if d_key in existing_draws and d_key in existing_tables:
                continue
            url = f"https://www.nlb.lk/results/{slug}/{d_no}"
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(500)
                parsed = parse_nlb_draw_page(page.content(), d_no)
                if parsed:
                    existing_draws[d_key] = {
                        "draw": parsed["draw"],
                        "date": parsed["date"],
                        "letter": parsed["letter"],
                        "nums": parsed["nums"],
                    }
                    if parsed["prizeTable"]:
                        existing_tables[d_key] = parsed["prizeTable"]
                    print(
                        f"  + NLB {t_id} #{d_no} ({parsed['date']}): "
                        f"{parsed['prizeTable']['totalWinners'] if parsed['prizeTable'] else 0} winners"
                    )
            except Exception as exc:
                print(f"  ! Error on {url}: {exc}")

        # Sort draws newest first and trim to MAX_DRAWS
        sorted_draws = sorted(existing_draws.values(), key=lambda x: int(x["draw"]), reverse=True)[:MAX_DRAWS]
        entry["draws"] = sorted_draws
        entry["perDrawTables"] = existing_tables


def parse_dlb_more_modal_html(modal_html: str, draw_date: str) -> dict[str, Any] | None:
    soup = BeautifulSoup(modal_html, "lxml")
    rows = []
    total_winners = 0
    total_payout = 0.0

    for tr in soup.select("table tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) >= 4:
            tier_label = tds[0]
            if re.search(r"\btotal\b", tier_label, re.I):
                continue
            winners = int(round(parse_number(tds[1])))
            unit_prize = parse_number(tds[2])
            row_amt = parse_number(tds[3])
            if unit_prize > 0 or winners > 0:
                if row_amt == 0 and unit_prize > 0 and winners > 0:
                    row_amt = winners * unit_prize
                total_winners += winners
                total_payout += row_amt
                rows.append(
                    {
                        "tier": tier_label,
                        "prize": f"Rs. {unit_prize:,.2f}",
                        "unitPrize": unit_prize,
                        "avgWinners": winners,
                    }
                )

    if not rows:
        return None

    next_jackpot = None
    txt = soup.get_text(" ", strip=True)
    jp_m = re.search(r"Next Jackpot\s*:\s*Rs\.?\s*([0-9,]+(?:\.\d+)?)", txt, re.I)
    if jp_m:
        next_jackpot = parse_number(jp_m.group(1))

    return {
        "date": draw_date,
        "totalWinners": total_winners,
        "totalPayout": round(total_payout, 2),
        "nextJackpot": next_jackpot,
        "rows": rows,
    }


def scrape_dlb_lotteries(page, dataset: dict[str, Any]) -> None:
    print("=== Scraping DLB Lotteries (https://www.dlb.lk/result/en) ===")
    page.goto("https://www.dlb.lk/result/en", wait_until="networkidle", timeout=60000)

    for lotto in DLB_LOTTERIES:
        t_id = lotto["id"]
        dlb_id = lotto["dlb_id"]
        entry = dataset["lotteries"].setdefault(
            t_id, {"id": t_id, "board": "DLB", "draws": [], "perDrawTables": {}}
        )
        existing_draws = {str(int(d["draw"])): d for d in entry["draws"] if str(d.get("draw", "")).isdigit()}
        existing_tables = entry.get("perDrawTables", {})

        print(f"[DLB] Selecting {lotto['name']} (lottery_id={dlb_id})...")
        try:
            # Trigger DLB's latest result modal first
            page.evaluate(f"check_latest_result({dlb_id})")
            page.wait_for_timeout(900)
            modal_html = page.inner_html("#myModal") if page.locator("#myModal").count() else ""

            # Also load paginated historical draws for this DLB lottery
            page.select_option("select#lottery_id, select[name='lottery_id']", str(dlb_id))
        except Exception:
            pass

        # Collect draw rows & `get_more_result(draw_id)` buttons across pages until MAX_DRAWS reached
        collected_count = 0
        page_num = 1
        while collected_count < MAX_DRAWS and page_num <= 40:
            html_snap = page.content()
            soup = BeautifulSoup(html_snap, "lxml")

            # Parse each historical draw block on current DLB page
            for tr in soup.select("table tr"):
                text_row = tr.get_text(" ", strip=True)
                m_draw = re.search(r"(\d{1,5})\s*\|\s*(\d{4}-[A-Za-z]{3}-\d{2}|\d{4}-\d{2}-\d{2})", text_row)
                if not m_draw:
                    continue
                d_no = str(int(m_draw.group(1)))
                raw_date = m_draw.group(2)
                iso_date = raw_date
                for fmt in ("%Y-%b-%d", "%Y-%m-%d"):
                    try:
                        iso_date = datetime.datetime.strptime(raw_date, fmt).strftime("%Y-%m-%d")
                        break
                    except ValueError:
                        pass

                # Extract letter or zodiac icon
                letter = "—"
                for img in tr.find_all("img"):
                    src = (img.get("src") or "").lower()
                    for z_key, z_name in ZODIAC_FILENAME_MAP.items():
                        if z_key in src:
                            letter = z_name
                            break

                balls = [li.get_text(strip=True) for li in tr.select("li, .number_circle, .ball") if li.get_text(strip=True)]
                nums = []
                for b in balls:
                    if re.fullmatch(r"[A-Za-z]", b) and letter == "—":
                        letter = b.upper()
                    elif re.fullmatch(r"\d{1,2}", b):
                        nums.append(b)

                if nums:
                    existing_draws[d_no] = {
                        "draw": d_no.zfill(4) if int(d_no) < 1000 else d_no,
                        "date": iso_date,
                        "letter": letter,
                        "nums": nums,
                    }

                # Check if row has get_more_result(internal_id)
                more_btn = tr.find(attrs={"onclick": re.compile(r"get_more_result\(\s*\d+\s*\)")})
                if more_btn and d_no not in existing_tables:
                    m_id = re.search(r"get_more_result\(\s*(\d+)\s*\)", more_btn["onclick"])
                    if m_id:
                        internal_id = m_id.group(1)
                        try:
                            page.evaluate(f"get_more_result({internal_id})")
                            page.wait_for_timeout(650)
                            m_html = page.inner_html("#myModal")
                            parsed_tbl = parse_dlb_more_modal_html(m_html, iso_date)
                            if parsed_tbl:
                                existing_tables[d_no] = parsed_tbl
                                if int(d_no) < 1000:
                                    existing_tables[d_no.zfill(4)] = parsed_tbl
                                print(f"  + DLB {t_id} #{d_no} ({iso_date}): {parsed_tbl['totalWinners']} winners")
                        except Exception as exc:
                            print(f"  ! DLB get_more_result({internal_id}) error: {exc}")

                collected_count = len(existing_draws)
                if collected_count >= MAX_DRAWS:
                    break

            # Advance pagination if available
            page_num += 1
            next_btn = page.locator(f"a[onclick*='pagination_re({page_num})'], a.page-link:has-text('{page_num}')")
            if next_btn.count() > 0 and collected_count < MAX_DRAWS:
                try:
                    next_btn.first.click()
                    page.wait_for_timeout(900)
                except Exception:
                    break
            else:
                break

        sorted_draws = sorted(existing_draws.values(), key=lambda x: int(x["draw"]), reverse=True)[:MAX_DRAWS]
        entry["draws"] = sorted_draws
        entry["perDrawTables"] = existing_tables


def main() -> None:
    dataset = load_existing_dataset(OUTPUT_PATH)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        page = context.new_page()
        scrape_nlb_lotteries(page, dataset)
        scrape_dlb_lotteries(page, dataset)
        browser.close()

    dataset["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dataset["maxDrawsTarget"] = MAX_DRAWS

    os.makedirs(os.path.dirname(OUTPUT_PATH) or ".", exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)

    print(f"[done] Wrote {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH):,} bytes)")


if __name__ == "__main__":
    main()
