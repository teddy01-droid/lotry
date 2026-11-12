#!/usr/bin/env python3
"""100% Pure Live Official NLB & DLB Scraper for GitHub Actions (Zero Hardcoded Data).

Scrapes real draw results (Draw No, Draw Date, Letter/Zodiac Sign, Winning Numbers)
and exact official Prize Breakdown ("MORE") tables directly from:
  1. DLB: https://www.dlb.lk/result/en
     - Uses `check_latest_result(dlb_id)` + `.bs-example-modal-lg001` for latest draw prize table.
     - Parses `<span class="letter">`, `<span class="number">`, and `onclick="get_more_result(result_id, dlb_id)"`
       across paginated historical tables via `pagination_re(page_idx, dlb_id)` up to MAX_DRAWS_PER_TICKET.
  2. NLB: https://www.nlb.lk/results/<slug> & https://www.nlb.lk/results/<slug>/<draw_no>
     - Extracts historical draws from `<table class="w0 tbl">` (sorting `ol.B li` by visual X coordinate)
       and matches relative `href="results/<slug>/<draw_no>"` links.
     - Visits individual draw pages `/results/<slug>/<draw_no>` up to MAX_DRAWS_PER_TICKET to scrape
       each draw's official Prize Structure table and older draws up to 365 draws.
"""

import datetime
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
                if isinstance(data, dict) and isinstance(data.get("lotteries"), dict):
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


def parse_date_to_iso(raw_date: str) -> str:
    s = re.sub(r"^(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*,?\s*", "", str(raw_date or "").strip(), flags=re.I)
    s = re.sub(r"\s+(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)$", "", s, flags=re.I).strip()
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%Y-%b-%d", "%Y-%B-%d", "%Y-%m-%d"):
        try:
            return datetime.datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return s


def parse_dlb_more_modal_html(modal_html: str, fallback_date: str = "") -> dict[str, Any] | None:
    if not modal_html or "<table" not in modal_html.lower():
        return None
    soup = BeautifulSoup(modal_html, "lxml")
    txt = soup.get_text(" ", strip=True)

    draw_no = ""
    m_draw = re.search(r"Draw\s*No\s*:\s*(\d+)", txt, re.I)
    if m_draw:
        draw_no = m_draw.group(1).strip()

    draw_date = fallback_date
    m_date = re.search(r"Draw\s*Date\s*:\s*(\d{4}-[A-Za-z]{3}-\d{2}|\d{4}-\d{2}-\d{2})", txt, re.I)
    if m_date:
        draw_date = parse_date_to_iso(m_date.group(1))

    # Extract balls from modal header if present
    letter = "—"
    nums: list[str] = []
    for img in soup.find_all("img"):
        src = (img.get("src") or "").lower()
        for z_key, z_name in ZODIAC_FILENAME_MAP.items():
            if z_key in src:
                letter = z_name
                break
    for el in soup.select(".letter, .number, .circle, .ball, li"):
        t = el.get_text(strip=True)
        classes = " ".join(el.get("class") or []).lower()
        if "letter" in classes and t:
            letter = t.upper()
        elif "number" in classes and re.fullmatch(r"\d{1,4}", t):
            nums.append(t)

    rows = []
    total_winners = 0
    total_payout = 0.0

    for tr in soup.select("table tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) >= 4:
            tier_label = tds[0].strip()
            if not tier_label or re.search(r"\btotal\b", tier_label, re.I) or re.search(r"^matches$", tier_label, re.I):
                continue
            winners = int(round(parse_number(tds[1])))
            unit_prize = parse_number(tds[2])
            row_amt = parse_number(tds[3])
            if unit_prize > 0 or winners > 0:
                if row_amt == 0 and unit_prize > 0 and winners > 0:
                    row_amt = winners * unit_prize
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
        "table": {
            "date": draw_date,
            "totalWinners": total_winners,
            "totalPayout": round(total_payout, 2),
            "rows": rows,
        },
    }


def extract_dlb_rows_for_lottery(html_content: str, dlb_id: int) -> list[dict[str, Any]]:
    """Finds all historical table rows belonging to `dlb_id` via `get_more_result(res_id, dlb_id)`."""
    soup = BeautifulSoup(html_content, "lxml")
    extracted = []
    pattern = re.compile(rf"get_more_result\(\s*(\d+)\s*,\s*{dlb_id}\s*\)")

    for a_tag in soup.find_all("a", attrs={"onclick": pattern}):
        m_call = pattern.search(a_tag.get("onclick") or "")
        if not m_call:
            continue
        res_id = int(m_call.group(1))
        tr = a_tag.find_parent("tr")
        if not tr:
            continue

        tds = tr.find_all("td")
        if not tds:
            continue

        # Column 0: "3167 | 2026-Nov-10 Tuesday"
        col0 = tds[0].get_text(" ", strip=True)
        m_draw = re.search(r"(\d{1,5})\s*\|\s*(\d{4}-[A-Za-z]{3}-\d{2}|\d{4}-\d{2}-\d{2})", col0)
        if not m_draw:
            continue
        draw_int = int(m_draw.group(1))
        draw_str = str(draw_int).zfill(4) if draw_int < 1000 else str(draw_int)
        iso_date = parse_date_to_iso(m_draw.group(2))

        letter = "—"
        for img in tr.find_all("img"):
            src = (img.get("src") or "").lower()
            for z_key, z_name in ZODIAC_FILENAME_MAP.items():
                if z_key in src:
                    letter = z_name
                    break

        letter_span = tr.select_one("span.letter")
        if letter_span and letter_span.get_text(strip=True):
            letter = letter_span.get_text(strip=True).upper()

        nums = [
            sp.get_text(strip=True)
            for sp in tr.select("span.number")
            if re.fullmatch(r"\d{1,4}", sp.get_text(strip=True))
        ]
        if not nums:
            # Fallback if numbers are separated by spaces/semicolons in tds[1] or tds[2]
            for td in tds[1:3]:
                for tok in re.findall(r"[A-Za-z]|\d{1,4}", td.get_text(" ", strip=True)):
                    if re.fullmatch(r"[A-Za-z]", tok) and letter == "—":
                        letter = tok.upper()
                    elif re.fullmatch(r"\d{1,4}", tok):
                        nums.append(tok)

        extracted.append({
            "res_id": res_id,
            "draw_key": str(draw_int),
            "draw": draw_str,
            "date": iso_date,
            "letter": letter,
            "nums": nums,
        })
    return extracted


def scrape_dlb_lotteries(page, dataset: dict[str, Any]) -> None:
    print("=== Scraping DLB Lotteries Live from https://www.dlb.lk/result/en ===")
    page.goto("https://www.dlb.lk/result/en", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2000)

    for lotto in DLB_LOTTERIES:
        t_id = lotto["id"]
        dlb_id = lotto["dlb_id"]
        entry = dataset["lotteries"].setdefault(
            t_id, {"id": t_id, "name": lotto["name"], "board": "DLB", "draws": [], "perDrawTables": {}}
        )
        existing_draws = {str(int(str(d["draw"]).lstrip("0") or "0")): d for d in entry["draws"] if str(d.get("draw", "")).lstrip("0").isdigit()}
        existing_tables = entry.get("perDrawTables", {})

        print(f"[DLB] Scraping {lotto['name']} (lottery_id={dlb_id}, target={MAX_DRAWS} draws)...")

        # 1. Fetch Latest Draw via check_latest_result(dlb_id) -> populates .bs-example-modal-lg001
        try:
            page.evaluate("""(id) => {
                const m = document.querySelector('.bs-example-modal-lg001');
                if (m) m.innerHTML = '';
                if (typeof check_latest_result === 'function') check_latest_result(id);
            }""", dlb_id)
            page.wait_for_function(
                "() => { const m = document.querySelector('.bs-example-modal-lg001'); return m && m.querySelectorAll('table tr').length > 2; }",
                timeout=8000,
            )
            modal_html = page.inner_html(".bs-example-modal-lg001")
            parsed_latest = parse_dlb_more_modal_html(modal_html)
            if parsed_latest and parsed_latest["draw"]:
                d_int = int(parsed_latest["draw"])
                d_key = str(d_int)
                d_pad = d_key.zfill(4) if d_int < 1000 else d_key
                existing_tables[d_key] = parsed_latest["table"]
                existing_tables[d_pad] = parsed_latest["table"]
                if parsed_latest["nums"]:
                    existing_draws[d_key] = {
                        "draw": d_pad,
                        "date": parsed_latest["date"],
                        "letter": parsed_latest["letter"],
                        "nums": parsed_latest["nums"],
                    }
                print(f"  + DLB {t_id} Latest Draw #{d_pad} ({parsed_latest['date']}): {parsed_latest['table']['totalWinners']} winners")
        except Exception as exc:
            print(f"  ! DLB check_latest_result({dlb_id}) warning: {exc}")

        # Also extract latest draw numbers from the page header block if modal didn't include balls
        try:
            header_draws = page.evaluate("""(id) => {
                const btn = document.querySelector(`a[onclick*="check_latest_result(${id})"]`);
                if (!btn) return null;
                const box = btn.closest('.col-md-12, .result_box, .tab-pane, div') || btn.parentElement.parentElement;
                const txt = box ? box.innerText : '';
                const balls = box ? Array.from(box.querySelectorAll('li, .letter, .number, h6')).map(e => e.innerText.trim()).filter(Boolean) : [];
                const imgs = box ? Array.from(box.querySelectorAll('img')).map(i => i.src) : [];
                return { txt, balls, imgs };
            }""", dlb_id)
            if header_draws and header_draws.get("txt"):
                m_hd = re.search(r"Draw\s*Number\s*-\s*(\d+)\s*\|\s*(\d{4}-[A-Za-z]{3}-\d{2}|\d{4}-\d{2}-\d{2})", header_draws["txt"], re.I)
                if m_hd:
                    d_int = int(m_hd.group(1))
                    d_key = str(d_int)
                    d_pad = d_key.zfill(4) if d_int < 1000 else d_key
                    iso_d = parse_date_to_iso(m_hd.group(2))
                    let_val = "—"
                    for src in header_draws.get("imgs", []):
                        for zk, zn in ZODIAC_FILENAME_MAP.items():
                            if zk in src.lower():
                                let_val = zn
                                break
                    num_list = []
                    for b in header_draws.get("balls", []):
                        if re.fullmatch(r"[A-Za-z]", b) and let_val == "—":
                            let_val = b.upper()
                        elif re.fullmatch(r"\d{1,4}", b):
                            num_list.append(b)
                    if num_list and d_key not in existing_draws:
                        existing_draws[d_key] = {"draw": d_pad, "date": iso_d, "letter": let_val, "nums": num_list}
        except Exception:
            pass

        # 2. Paginate through historical draws via pagination_re(page_idx, dlb_id) up to MAX_DRAWS
        max_pages = max(1, (MAX_DRAWS // 12) + 2)
        for page_idx in range(0, max_pages):
            if len(existing_draws) >= MAX_DRAWS and len(existing_tables) >= MAX_DRAWS:
                break

            if page_idx > 0:
                try:
                    page.evaluate(f"pagination_re({page_idx}, {dlb_id})")
                    page.wait_for_timeout(950)
                except Exception as exc:
                    print(f"  ! DLB pagination_re({page_idx}, {dlb_id}) stop: {exc}")
                    break

            rows_on_page = extract_dlb_rows_for_lottery(page.content(), dlb_id)
            if not rows_on_page:
                break

            for r_info in rows_on_page:
                d_key = r_info["draw_key"]
                d_pad = r_info["draw"]
                if r_info["nums"]:
                    existing_draws[d_key] = {
                        "draw": d_pad,
                        "date": r_info["date"],
                        "letter": r_info["letter"],
                        "nums": r_info["nums"],
                    }

                if d_key not in existing_tables and d_pad not in existing_tables and len(existing_tables) < MAX_DRAWS:
                    res_id = r_info["res_id"]
                    try:
                        page.evaluate("""([rId, lId]) => {
                            const m = document.querySelector('.bs-example-modal-lg001');
                            if (m) m.innerHTML = '';
                            if (typeof get_more_result === 'function') get_more_result(rId, lId);
                        }""", [res_id, dlb_id])
                        page.wait_for_function(
                            "() => { const m = document.querySelector('.bs-example-modal-lg001'); return m && m.querySelectorAll('table tr').length > 2; }",
                            timeout=7000,
                        )
                        m_html = page.inner_html(".bs-example-modal-lg001")
                        parsed_tbl = parse_dlb_more_modal_html(m_html, r_info["date"])
                        if parsed_tbl and parsed_tbl["table"]:
                            existing_tables[d_key] = parsed_tbl["table"]
                            existing_tables[d_pad] = parsed_tbl["table"]
                            print(f"  + DLB {t_id} Draw #{d_pad} ({r_info['date']}): {parsed_tbl['table']['totalWinners']} winners")
                    except Exception as exc:
                        print(f"  ! DLB get_more_result({res_id}, {dlb_id}) warning: {exc}")

                if len(existing_draws) >= MAX_DRAWS and len(existing_tables) >= MAX_DRAWS:
                    break

        sorted_draws = sorted(existing_draws.values(), key=lambda x: int(str(x["draw"]).lstrip("0") or "0"), reverse=True)[:MAX_DRAWS]
        entry["draws"] = sorted_draws
        entry["perDrawTables"] = existing_tables
        save_dataset_checkpoint(dataset, OUTPUT_PATH)


def parse_nlb_main_table_rows(page) -> list[dict[str, Any]]:
    """Extracts the ~200 historical draws directly from `<table class='w0 tbl'>` on `/results/<slug>`,
    sorting each draw's `<ol class='B'><li>` balls by visual X coordinate (`getBoundingClientRect().left`).
    """
    raw_rows = page.evaluate(r"""() => {
        const out = [];
        document.querySelectorAll('table tr').forEach(tr => {
            const tds = tr.querySelectorAll('td');
            if (tds.length < 2) return;
            const col0Text = tds[0].innerText.trim();
            const bTag = tds[0].querySelector('b');
            const drawNo = bTag ? bTag.innerText.trim() : '';
            if (!/^\d{1,5}$/.test(drawNo)) return;
            const dateText = col0Text.replace(drawNo, '').trim();

            const lis = Array.from(tds[1].querySelectorAll('ol.B li, ul.B li, li'));
            lis.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
            let letter = '—';
            const nums = [];
            lis.forEach(li => {
                const val = li.innerText.trim();
                const cls = (li.className || '') + ' ' + (li.getAttribute('title') || '');
                if (/Letter|Zodiac/i.test(cls) || /^[A-Za-z]{1,12}$/.test(val)) {
                    if (letter === '—' && val) letter = val.toUpperCase();
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
            d_pad = str(d_int).zfill(4) if d_int < 1000 else str(d_int)
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

    date_match = re.search(
        r"Date\s*:\s*(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)?\s*"
        r"([A-Z][a-z]+\s+\d{1,2},\s+\d{4}|\d{4}-\d{2}-\d{2})",
        text_all,
    )
    iso_date = parse_date_to_iso(date_match.group(1)) if date_match else ""

    # Read balls in visual left-to-right order via getBoundingClientRect().left
    visual_balls = []
    try:
        visual_balls = page.evaluate(r"""() => {
            const ol = document.querySelector('ol.B, ul.B, .lresult ol, .lresult ul');
            if (!ol) return [];
            const lis = Array.from(ol.querySelectorAll('li'));
            lis.sort((a, b) => a.getBoundingClientRect().left - b.getBoundingClientRect().left);
            return lis.map(li => ({
                val: li.innerText.trim(),
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

    rows = []
    total_winners = 0
    total_payout = 0.0
    for tr in soup.select("table tr"):
        tds = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(tds) >= 4:
            if len(tds) >= 5:
                tier_label = f"{tds[0]} — {tds[1]}" if tds[0] and not re.search(r"total", tds[0], re.I) else tds[1]
                prize_str, win_str, tot_str = tds[2], tds[3], tds[4]
            else:
                tier_label, prize_str, win_str, tot_str = tds[0], tds[1], tds[2], tds[3]

            if re.search(r"\btotal\b", tier_label, re.I) or re.search(r"\btotal\b", tds[0], re.I):
                continue
            unit_prize = parse_number(prize_str)
            winners = int(round(parse_number(win_str)))
            row_amt = parse_number(tot_str)
            if unit_prize > 0 or winners > 0:
                if row_amt == 0 and unit_prize > 0 and winners > 0:
                    row_amt = unit_prize * winners
                total_winners += winners
                total_payout += row_amt
                rows.append({
                    "tier": tier_label,
                    "prize": f"Rs. {unit_prize:,.2f}",
                    "unitPrize": unit_prize,
                    "avgWinners": winners,
                })

    if not nums and not rows:
        return None

    d_pad = str(draw_no).zfill(4) if draw_no < 1000 else str(draw_no)
    return {
        "draw": d_pad,
        "date": iso_date or datetime.date.today().strftime("%Y-%m-%d"),
        "letter": letter,
        "nums": nums,
        "prizeTable": {
            "date": iso_date,
            "totalWinners": total_winners,
            "totalPayout": round(total_payout, 2),
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
        existing_draws = {str(int(str(d["draw"]).lstrip("0") or "0")): d for d in entry["draws"] if str(d.get("draw", "")).lstrip("0").isdigit()}
        existing_tables = entry.get("perDrawTables", {})

        list_url = f"https://www.nlb.lk/results/{slug}"
        print(f"[NLB] Loading {lotto['name']} ({list_url})...")
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

        # 1. Extract up to ~200 draws directly from `<table class='w0 tbl'>` on `/results/<slug>`
        try:
            main_rows = parse_nlb_main_table_rows(page)
            for r in main_rows[:MAX_DRAWS]:
                existing_draws[r["draw_key"]] = {
                    "draw": r["draw"],
                    "date": r["date"],
                    "letter": r["letter"],
                    "nums": r["nums"],
                }
            if main_rows:
                print(f"  + Extracted {min(len(main_rows), MAX_DRAWS)} historical draw results from {slug} main table.")
        except Exception as exc:
            print(f"  ! Main table parse warning on {slug}: {exc}")

        # 2. Detect latest draw number (supports relative `results/<slug>/<draw_no>` WITHOUT leading slash!)
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
        min_draw_no = max(1, latest_draw_no - MAX_DRAWS + 1)
        print(f"  -> Scraping individual draw Prize Structure tables #{latest_draw_no}..#{min_draw_no}")

        consecutive_errors = 0
        for d_no in range(latest_draw_no, min_draw_no - 1, -1):
            d_key = str(d_no)
            d_pad = d_key.zfill(4) if d_no < 1000 else d_key
            if d_key in existing_draws and (d_key in existing_tables or d_pad in existing_tables):
                continue
            if consecutive_errors >= 5:
                print(f"  ! Stopping individual page loop for {t_id} after 5 consecutive timeouts/challenges.")
                break

            detail_url = f"https://www.nlb.lk/results/{slug}/{d_no}"
            try:
                page.goto(detail_url, wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(450)
                parsed = parse_nlb_single_draw_page(page, page.content(), d_no)
                if parsed:
                    consecutive_errors = 0
                    if parsed["nums"] and d_key not in existing_draws:
                        existing_draws[d_key] = {
                            "draw": d_pad,
                            "date": parsed["date"],
                            "letter": parsed["letter"],
                            "nums": parsed["nums"],
                        }
                    if parsed["prizeTable"]:
                        existing_tables[d_key] = parsed["prizeTable"]
                        existing_tables[d_pad] = parsed["prizeTable"]
                        print(f"  + NLB {t_id} Draw #{d_pad} ({parsed['date']}): {parsed['prizeTable']['totalWinners']} winners")
                else:
                    consecutive_errors += 1
            except Exception as exc:
                consecutive_errors += 1
                print(f"  ! Error on {detail_url}: {exc}")

        sorted_draws = sorted(existing_draws.values(), key=lambda x: int(str(x["draw"]).lstrip("0") or "0"), reverse=True)[:MAX_DRAWS]
        entry["draws"] = sorted_draws
        entry["perDrawTables"] = existing_tables
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
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 900},
        )
        context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = context.new_page()
        scrape_dlb_lotteries(page, dataset)
        scrape_nlb_lotteries(page, dataset)
        browser.close()

    save_dataset_checkpoint(dataset, OUTPUT_PATH)
    final_draws = sum(len(v.get("draws", [])) for v in dataset["lotteries"].values())
    final_tables = sum(len(v.get("perDrawTables", {})) for v in dataset["lotteries"].values())
    print(f"[done] Wrote {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH):,} bytes) — {final_draws} draws, {final_tables} official prize tables.")


if __name__ == "__main__":
    main()
