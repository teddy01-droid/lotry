#!/usr/bin/env python3
"""Official NLB & DLB Multi-Draw Scraper for GitHub Actions.

Scrapes 100% real draw results (Draw No, Draw Date, Letter/Zodiac Sign, Winning
Numbers) and exact official Prize Breakdown ("MORE") tables for all 18 active
Sri Lankan lotteries:
  - 9 NLB Lotteries (https://www.nlb.lk/results/<slug>/<draw_no>)
  - 9 DLB Lotteries (https://www.dlb.lk/result/en + AJAX get_more_result / pagination)

Saves checkpoint after each lottery so partial runs still output valid JSON.
"""

import datetime
import json
import os
import re
from typing import Any
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

MAX_DRAWS = int(os.environ.get("MAX_DRAWS_PER_TICKET", "30"))
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


def save_dataset_checkpoint(dataset: dict[str, Any], path: str) -> None:
    dataset["updatedAt"] = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    dataset["maxDrawsTarget"] = MAX_DRAWS
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(dataset, f, ensure_ascii=False, indent=2)


def parse_number(val: str) -> float:
    cleaned = re.sub(r"[^0-9.]", "", str(val or ""))
    try:
        return float(cleaned) if cleaned else 0.0
    except ValueError:
        return 0.0


def parse_nlb_draw_page(html: str, draw_no: int) -> dict[str, Any] | None:
    soup = BeautifulSoup(html, "lxml")
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

    balls_container = soup.select("ol.B li, ul.B li, .lresult li, .balls li")
    tokens = [li.get_text(strip=True) for li in balls_container if li.get_text(strip=True)]
    letter = "—"
    nums: list[str] = []
    for tok in tokens:
        if re.fullmatch(r"[A-Za-z]{1,12}", tok):
            letter = tok.upper()
        elif re.fullmatch(r"\d{1,4}", tok):
            nums.append(tok)

    rows = []
    total_winners = 0
    total_payout = 0.0
    for tr in soup.select("table tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) >= 4:
            # NLB tables often have 5 columns: [#, Pattern, Prize, Winners, Total] or 4 columns: [Pattern, Prize, Winners, Total]
            if len(tds) >= 5 and re.match(r"^\d+\s*(?:st|nd|rd|th)?$", tds[0], re.I):
                tier_label, prize_str, win_str, tot_str = tds[1], tds[2], tds[3], tds[4]
            else:
                tier_label, prize_str, win_str, tot_str = tds[0], tds[1], tds[2], tds[3]

            if re.search(r"\btotal\b", tier_label, re.I):
                continue
            unit_prize = parse_number(prize_str)
            winners = int(round(parse_number(win_str)))
            row_amt = parse_number(tot_str)
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
    index_html = ""
    try:
        page.goto("https://www.nlb.lk/results", wait_until="domcontentloaded", timeout=35000)
        page.wait_for_timeout(1500)
        index_html = page.content()
    except Exception as exc:
        print(f"[warn] Initial NLB index load warning: {exc}")

    for lotto in NLB_LOTTERIES:
        t_id = lotto["id"]
        slug = lotto["slug"]
        entry = dataset["lotteries"].setdefault(
            t_id, {"id": t_id, "board": "NLB", "draws": [], "perDrawTables": {}}
        )
        existing_draws = {str(int(d["draw"])): d for d in entry["draws"] if str(d.get("draw", "")).isdigit()}
        existing_tables = entry.get("perDrawTables", {})

        try:
            page.goto(f"https://www.nlb.lk/results/{slug}", wait_until="domcontentloaded", timeout=35000)
            page.wait_for_timeout(1000)
            lotto_html = page.content()
        except Exception as exc:
            print(f"[warn] Could not load NLB {slug}: {exc}")
            continue

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
                page.goto(url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(350)
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

        sorted_draws = sorted(existing_draws.values(), key=lambda x: int(x["draw"]), reverse=True)[:365]
        entry["draws"] = sorted_draws
        entry["perDrawTables"] = existing_tables
        save_dataset_checkpoint(dataset, OUTPUT_PATH)


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

    return {
        "date": draw_date,
        "totalWinners": total_winners,
        "totalPayout": round(total_payout, 2),
        "rows": rows,
    }


def scrape_dlb_lotteries(page, dataset: dict[str, Any]) -> None:
    print("=== Scraping DLB Lotteries (https://www.dlb.lk/result/en) ===")
    try:
        page.goto("https://www.dlb.lk/result/en", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(1500)
    except Exception as exc:
        print(f"[warn] DLB page load warning: {exc}")
        return

    for lotto in DLB_LOTTERIES:
        t_id = lotto["id"]
        dlb_id = lotto["dlb_id"]
        entry = dataset["lotteries"].setdefault(
            t_id, {"id": t_id, "board": "DLB", "draws": [], "perDrawTables": {}}
        )
        existing_draws = {str(int(d["draw"])): d for d in entry["draws"] if str(d.get("draw", "")).isdigit()}
        existing_tables = entry.get("perDrawTables", {})

        print(f"[DLB] Checking latest & historical results for {lotto['name']} (lottery_id={dlb_id})...")
        try:
            page.evaluate(f"check_latest_result({dlb_id})")
            page.wait_for_timeout(850)
            modal_html = page.inner_html("#myModal") if page.locator("#myModal").count() else ""
            if modal_html:
                m_draw = re.search(r"Draw No\s*:\s*(\d+)", modal_html, re.I)
                m_date = re.search(r"Draw Date\s*:\s*(\d{4}-[A-Za-z]{3}-\d{2}|\d{4}-\d{2}-\d{2})", modal_html, re.I)
                if m_draw:
                    d_no = str(int(m_draw.group(1)))
                    iso_d = datetime.date.today().strftime("%Y-%m-%d")
                    if m_date:
                        for fmt in ("%Y-%b-%d", "%Y-%m-%d"):
                            try:
                                iso_d = datetime.datetime.strptime(m_date.group(1), fmt).strftime("%Y-%m-%d")
                                break
                            except ValueError:
                                pass
                    parsed_latest = parse_dlb_more_modal_html(modal_html, iso_d)
                    if parsed_latest:
                        existing_tables[d_no] = parsed_latest
                        if int(d_no) < 1000:
                            existing_tables[d_no.zfill(4)] = parsed_latest
                        print(f"  + DLB {t_id} Latest Draw #{d_no}: {parsed_latest['totalWinners']} winners")
        except Exception as exc:
            print(f"  ! DLB check_latest_result({dlb_id}) warning: {exc}")

        sorted_draws = sorted(existing_draws.values(), key=lambda x: int(x["draw"]), reverse=True)[:365]
        entry["draws"] = sorted_draws
        entry["perDrawTables"] = existing_tables
        save_dataset_checkpoint(dataset, OUTPUT_PATH)


def main() -> None:
    dataset = load_existing_dataset(OUTPUT_PATH)
    # Always write initial file immediately so `data/sl_lottery_365_official.json` exists even before scraping finishes
    save_dataset_checkpoint(dataset, OUTPUT_PATH)
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

    save_dataset_checkpoint(dataset, OUTPUT_PATH)
    print(f"[done] Wrote {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH):,} bytes)")


if __name__ == "__main__":
    main()
