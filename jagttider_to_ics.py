import os
import re
import json
import glob
import time
import hashlib
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


# ----------------------------
# URLs
# ----------------------------
GENERAL_URL = "https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/"
LOCAL_URL = "https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/lokale-jagttider/"


# ----------------------------
# Helpers
# ----------------------------
DATE_RANGE_RE = re.compile(
    r"(?P<d1>\d{1,2})\.(?P<m1>\d{1,2})\s*[-–]\s*(?P<d2>\d{1,2})\.(?P<m2>\d{1,2})"
)

# matches: "01.09 - 31.01 og 16.05 - 15.07" (two periods)
MULTI_RANGE_SPLIT_RE = re.compile(r"\s+og\s+")

# sanitize for filenames / ids
def slug(s: str) -> str:
    s = s.strip().lower()
    s = s.replace("æ", "ae").replace("ø", "oe").replace("å", "aa")
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    return s or "x"


def dtstamp_utc() -> str:
    # iCalendar DTSTAMP in UTC basic format
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def yyyymmdd(d: date) -> str:
    return d.strftime("%Y%m%d")


def wrap_ics_line(line: str) -> List[str]:
    """
    RFC5545 line folding: 75 octets. We'll do a simple 73 chars cut (safe-ish).
    """
    out = []
    max_len = 73
    while len(line) > max_len:
        out.append(line[:max_len])
        line = " " + line[max_len:]
    out.append(line)
    return out


def ics_escape(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(";", "\\;")
        .replace(",", "\\,")
    )


def stable_uid(parts: List[str]) -> str:
    raw = "|".join(parts).encode("utf-8")
    h = hashlib.sha1(raw).hexdigest()[:16]
    return f"{h}@jagttider"


# ----------------------------
# Robust HTTP fetch
# ----------------------------
def fetch_html(url: str, timeout: int = 30, retries: int = 3, backoff: float = 1.2) -> str:
    headers = {
        # Make it look like a normal browser
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "da-DK,da;q=0.9,en-US;q=0.8,en;q=0.7",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "Referer": "https://www.jaegerforbundet.dk/",
    }

    last_err = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
            status = r.status_code
            text = r.text or ""

            # quick sanity checks
            if status >= 400:
                raise RuntimeError(f"HTTP {status} for {url}")

            # If we got a consent/blocked/captcha page, it often contains these
            lowered = text.lower()
            bad_signals = [
                "captcha",
                "cloudflare",
                "access denied",
                "forbidden",
                "consent",
                "cookie",
                "accept cookies",
                "javascript is required",
            ]
            if any(sig in lowered for sig in bad_signals) and "jagttider" not in lowered:
                raise RuntimeError(f"Fetched HTML looks like consent/blocked page for {url}")

            return text

        except Exception as e:
            last_err = e
            if attempt < retries:
                time.sleep(backoff ** attempt)
            else:
                raise RuntimeError(f"Failed to fetch {url} after {retries} tries: {last_err}") from last_err


def assert_page_looks_right(url: str, html: str, must_contain_any: List[str]) -> None:
    h = html.lower()
    if not any(k.lower() in h for k in must_contain_any):
        # Print a short debug snippet to Actions log
        snippet = re.sub(r"\s+", " ", html[:600])
        raise RuntimeError(
            f"HTML for {url} does not look like expected page.\n"
            f"Expected one of: {must_contain_any}\n"
            f"First 600 chars: {snippet}"
        )


# ----------------------------
# Date handling (season spanning year)
# ----------------------------
def to_date_in_season(day: int, month: int, season_year: int) -> date:
    """
    Jagtsæsonen er typisk: efterår -> vinter (fx 01.09 - 31.01).
    Hvis month >= 8 antager vi samme år (season_year),
    ellers næste år (season_year + 1).
    """
    year = season_year if month >= 8 else season_year + 1
    return date(year, month, day)


def parse_ranges_text(text: str, season_year: int) -> List[Tuple[date, date]]:
    """
    Return list of (start, end_inclusive) for one season year.
    Handles one or two ranges: "16.05 - 15.07 og 01.10 - 31.01"
    """
    text = text.strip()
    if not text:
        return []

    # if "ingen jagttid" -> no ranges
    if "ingen jagttid" in text.lower():
        return []

    parts = MULTI_RANGE_SPLIT_RE.split(text)
    ranges = []
    for part in parts:
        m = DATE_RANGE_RE.search(part)
        if not m:
            continue
        d1 = int(m.group("d1"))
        m1 = int(m.group("m1"))
        d2 = int(m.group("d2"))
        m2 = int(m.group("m2"))

        start = to_date_in_season(d1, m1, season_year)
        end = to_date_in_season(d2, m2, season_year)
        # inclusive end in iCal with VALUE=DATE means DTEND should be next day
        ranges.append((start, end))
    return ranges


# ----------------------------
# Parsing: GENERAL
# ----------------------------
def parse_general(html: str) -> List[Dict[str, Any]]:
    """
    Parse general jagttider page.
    Returns items: {species, period_text, extra_text(optional)}
    """
    assert_page_looks_right(GENERAL_URL, html, must_contain_any=["Jagttider", "jagttid"])

    soup = BeautifulSoup(html, "html.parser")

    # We will collect rows that look like: "Artname" + "01.09 - 31.01"
    items = []

    # Strategy: find all text nodes containing a date-range and look left for species name
    # This is robust against layout changes.
    for el in soup.find_all(text=True):
        t = " ".join(el.strip().split())
        if not t:
            continue
        if DATE_RANGE_RE.search(t) or "jagttid" in t.lower() or "ingen jagttid" in t.lower():
            # candidates often inside table rows / cards - look at parent block
            parent = el.parent
            if not parent:
                continue
            block = parent.get_text(" ", strip=True)
            # pick only blocks that contain a date range
            if not (DATE_RANGE_RE.search(block) or "jagttid" in block.lower() or "ingen jagttid" in block.lower()):
                continue

            # Try to split: species name first, then period part
            # Common: "Gråand 01.09 - 31.01"
            # We'll take first chunk before first date-range as species.
            m = DATE_RANGE_RE.search(block)
            if m:
                species_part = block[: m.start()].strip()
                period_part = block[m.start():].strip()
            else:
                # "jagttid (se lokal)" lines: skip from general
                continue

            # Heuristics: species name should be short-ish and not look like a paragraph.
            if len(species_part) == 0 or len(species_part) > 80:
                continue
            if "jagttid" in species_part.lower():
                continue

            species = species_part
            period_text = period_part

            items.append({"species": species, "period_text": period_text})

    # Deduplicate by (species, period_text)
    dedup = {}
    for it in items:
        key = (it["species"], it["period_text"])
        dedup[key] = it
    return list(dedup.values())


# ----------------------------
# Parsing: LOCAL
# ----------------------------
def parse_local(html: str) -> List[Dict[str, Any]]:
    """
    Parse local jagttider page.
    Returns rows: {area, species, period_text}
    area is region/kommune/ø header text.
    """
    assert_page_looks_right(LOCAL_URL, html, must_contain_any=["Lokale jagttider", "Region", "lokale"])

    soup = BeautifulSoup(html, "html.parser")

    # Local page typically has sections/accordions with headings like:
    # "Region Nordjylland - lokale jagttider"
    # and sub headings like "Hele regionen", "Øen Læsø", "Kommune X" etc.
    rows: List[Dict[str, Any]] = []

    # Find headings that include "Region"
    # We'll walk DOM and keep a current_area context.
    current_area = None

    # We use a simple traversal over elements in document order
    for node in soup.find_all(["h1", "h2", "h3", "h4", "strong", "p", "li", "td", "div", "span"]):
        text = node.get_text(" ", strip=True)
        if not text:
            continue

        # Area detectors
        if "Region " in text and "lokale jagttider" in text.lower():
            current_area = text.strip()
            continue

        # Sub-area like "Hele regionen", "Øen X", "Kommune Y"
        if text.lower().startswith("hele regionen") or text.lower().startswith("øen ") or text.lower().endswith(" kommune"):
            current_area = text.strip()
            continue

        # Candidate row: contains a date range OR "ingen jagttid" OR special saturday text
        if (DATE_RANGE_RE.search(text) or "ingen jagttid" in text.lower() or "lørdag" in text.lower()) and current_area:
            block = text

            # Try to detect "Species ... Period"
            # Examples:
            # "Hare 01.11-15.01"
            # "Hare ingen jagttid"
            # "Fasanhan(e) 1. og 2. lørdag i november ..."
            # We'll split by first whitespace run where the rest looks like period/special.
            parts = block.split()
            if len(parts) < 2:
                continue

            species = parts[0].strip()
            rest = " ".join(parts[1:]).strip()

            # If species is too short/garbage, skip
            if len(species) < 2:
                continue

            rows.append(
                {
                    "area": current_area,
                    "species": species,
                    "period_text": rest,
                }
            )

    # Deduplicate
    dedup = {}
    for r in rows:
        key = (r["area"], r["species"], r["period_text"])
        dedup[key] = r
    return list(dedup.values())


# ----------------------------
# ICS generation
# ----------------------------
def make_event_lines(
    calname: str,
    summary: str,
    start: date,
    end_inclusive: date,
    description_lines: List[str],
) -> List[str]:
    # For VALUE=DATE, DTEND is exclusive => +1 day
    dtend_excl = end_inclusive + timedelta(days=1)
    uid = stable_uid([calname, summary, yyyymmdd(start), yyyymmdd(end_inclusive)])

    desc = "\n".join(description_lines).strip()
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp_utc()}",
        f"SUMMARY:{ics_escape(summary)}",
        f"DTSTART;VALUE=DATE:{yyyymmdd(start)}",
        f"DTEND;VALUE=DATE:{yyyymmdd(dtend_excl)}",
    ]
    if desc:
        lines.append(f"DESCRIPTION:{ics_escape(desc)}")
    lines.append("END:VEVENT")
    return lines


def write_ics(path: str, calname: str, events: List[List[str]]) -> None:
    lines: List[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//luka2945//Jagttider ICS//DA",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{ics_escape(calname)}",
    ]
    for ev in events:
        lines.extend(ev)
    lines.append("END:VCALENDAR")

    # fold lines
    folded: List[str] = []
    for ln in lines:
        folded.extend(wrap_ics_line(ln))

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(folded) + "\n")


# ----------------------------
# Config loading
# ----------------------------
def load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def seasons_to_generate(seasons_ahead: int) -> List[int]:
    """
    Returns list of season years: current season year and next seasons.
    We define "season year" by current date:
    If month >= 8 => current year is season_year
    else => season_year = current_year - 1
    """
    today = date.today()
    season_year = today.year if today.month >= 8 else today.year - 1
    return [season_year + i for i in range(seasons_ahead)]


def get_master_species_info(master: Dict[str, Any], species: str) -> Dict[str, Any]:
    species_map = master.get("species", {}) or {}
    key = species.strip()

    # direct match
    if key in species_map:
        return species_map[key]

    # alias match
    for k, v in species_map.items():
        aliases = v.get("aliases") or []
        if any(a.strip().lower() == key.lower() for a in aliases):
            return v
    return {}


# ----------------------------
# Main: build calendars
# ----------------------------
def build_general_calendar(master: Dict[str, Any], cfg: Dict[str, Any], output_path: str) -> int:
    calname = cfg.get("calendar_name") or "Jagttider - Generel"
    seasons_ahead = int(cfg.get("seasons_ahead", 1))

    html = fetch_html(GENERAL_URL)
    general_items = parse_general(html)

    seasons = seasons_to_generate(seasons_ahead)
    events: List[List[str]] = []

    for season_year in seasons:
        for it in general_items:
            species = it["species"]
            period_text = it["period_text"]

            ranges = parse_ranges_text(period_text, season_year)
            if not ranges:
                continue

            info = get_master_species_info(master, species)
            shooting_note = info.get("shooting_time_note")
            img = info.get("image_url")

            for (start, end) in ranges:
                summary = f"{species} – Generel jagttid"
                desc_lines = []
                if shooting_note:
                    desc_lines.append(f"Skydetid: {shooting_note}")
                desc_lines.append(f"Periode: {start.strftime('%d.%m')} til {end.strftime('%d.%m')}")
                if img:
                    desc_lines.append(f"Billede: {img}")
                desc_lines.append("")
                desc_lines.append(f"Kilde: {GENERAL_URL}")

                events.append(make_event_lines(calname, summary, start, end, desc_lines))

    write_ics(output_path, calname, events)
    return len(events)


def build_local_calendar(
    master: Dict[str, Any],
    cfg: Dict[str, Any],
    output_path: str,
    general_period_lookup: Dict[str, List[Tuple[int, List[Tuple[date, date]]]]],
) -> int:
    calname = cfg.get("calendar_name") or "Jagttider - Lokalt"
    seasons_ahead = int(cfg.get("seasons_ahead", 1))

    filters = cfg.get("filters", {}) or {}
    include_area = [s.lower() for s in (filters.get("include_area_keywords") or [])]
    exclude_area = [s.lower() for s in (filters.get("exclude_area_keywords") or [])]

    local_rules = cfg.get("local_rules", {}) or {}
    emit_no_hunting = bool(local_rules.get("emit_no_hunting_events", False))

    attachments = (master.get("attachments") or {})
    local_map_image_url = attachments.get("local_map_image_url")

    html = fetch_html(LOCAL_URL)
    local_rows = parse_local(html)

    # filter rows by area keywords
    def area_allowed(area: str) -> bool:
        a = area.lower()
        if include_area and not any(k in a for k in include_area):
            return False
        if exclude_area and any(k in a for k in exclude_area):
            return False
        return True

    local_rows = [r for r in local_rows if area_allowed(r["area"])]

    seasons = seasons_to_generate(seasons_ahead)
    events: List[List[str]] = []

    for season_year in seasons:
        for r in local_rows:
            area = r["area"]
            species = r["species"]
            period_text = r["period_text"].strip()

            info = get_master_species_info(master, species)
            shooting_note = info.get("shooting_time_note")
            img = info.get("image_url")

            # "ingen jagttid" case
            if "ingen jagttid" in period_text.lower():
                if not emit_no_hunting:
                    continue

                # We clip "ingen jagttid" to the *general* period(s) of that species (same season)
                # If we don't know the general period -> skip (otherwise it becomes "whole year")
                gen_list = general_period_lookup.get(species.lower(), [])
                # pick the matching season year
                matching = [x for x in gen_list if x[0] == season_year]
                if not matching:
                    continue

                for (_, ranges) in matching:
                    for (start, end) in ranges:
                        summary = f"{species} – Lokal tid (Ingen jagttid) ({area})"
                        desc_lines = []
                        if shooting_note:
                            desc_lines.append(f"Skydetid: {shooting_note}")
                        desc_lines.append(f"Periode: {start.strftime('%d.%m')} til {end.strftime('%d.%m')}")
                        desc_lines.append("Status: Ingen jagttid i dette område.")
                        if img:
                            desc_lines.append(f"Billede: {img}")
                        if local_map_image_url:
                            desc_lines.append(f"Kort: {local_map_image_url}")
                        desc_lines.append("")
                        desc_lines.append(f"Kilde: {LOCAL_URL}")

                        events.append(make_event_lines(calname, summary, start, end, desc_lines))
                continue

            # Special “lørdag” rules (simple version): create notes event as a 1-day marker on season start
            # (You can improve later to calculate exact Saturdays)
            if "lørdag" in period_text.lower() and not DATE_RANGE_RE.search(period_text):
                # place as an info event on 1.9 in this season_year
                start = date(season_year, 9, 1)
                end = start
                summary = f"{species} – Lokal tid ({area})"
                desc_lines = []
                if shooting_note:
                    desc_lines.append(f"Skydetid: {shooting_note}")
                desc_lines.append("Periode: (særlige regler)")
                desc_lines.append(f"Regel: {period_text}")
                if img:
                    desc_lines.append(f"Billede: {img}")
                if local_map_image_url:
                    desc_lines.append(f"Kort: {local_map_image_url}")
                desc_lines.append("")
                desc_lines.append(f"Kilde: {LOCAL_URL}")

                events.append(make_event_lines(calname, summary, start, end, desc_lines))
                continue

            # Normal date range(s)
            ranges = parse_ranges_text(period_text, season_year)
            if not ranges:
                continue

            for (start, end) in ranges:
                summary = f"{species} – Lokal tid ({area})"
                desc_lines = []
                if shooting_note:
                    desc_lines.append(f"Skydetid: {shooting_note}")
                desc_lines.append(f"Periode: {start.strftime('%d.%m')} til {end.strftime('%d.%m')}")
                if img:
                    desc_lines.append(f"Billede: {img}")
                if local_map_image_url:
                    desc_lines.append(f"Kort: {local_map_image_url}")
                desc_lines.append("")
                desc_lines.append(f"Kilde: {LOCAL_URL}")

                events.append(make_event_lines(calname, summary, start, end, desc_lines))

    write_ics(output_path, calname, events)
    return len(events)


def main():
    repo_root = os.path.dirname(os.path.abspath(__file__))

    master_path = os.path.join(repo_root, "configs", "master.json")
    if not os.path.exists(master_path):
        raise RuntimeError(f"Missing master config: {master_path}")
    master = load_json(master_path)

    cal_cfg_paths = sorted(glob.glob(os.path.join(repo_root, "configs", "calendars", "*.json")))
    if not cal_cfg_paths:
        raise RuntimeError("No calendar configs found in configs/calendars/*.json")

    out_dir = os.path.join(repo_root, "Jagttids-Kalender")
    os.makedirs(out_dir, exist_ok=True)

    # First: build a general lookup so local "ingen jagttid" can be clipped correctly
    # Build per season_year for each species
    general_html = fetch_html(GENERAL_URL)
    general_items = parse_general(general_html)

    seasons_ahead_default = 1
    # compute max seasons_ahead across configs so lookup covers all
    for p in cal_cfg_paths:
        cfg = load_json(p)
        seasons_ahead_default = max(seasons_ahead_default, int(cfg.get("seasons_ahead", 1)))
    seasons = seasons_to_generate(seasons_ahead_default)

    general_period_lookup: Dict[str, List[Tuple[int, List[Tuple[date, date]]]]] = {}
    for season_year in seasons:
        for it in general_items:
            species = it["species"]
            ranges = parse_ranges_text(it["period_text"], season_year)
            if not ranges:
                continue
            key = species.lower()
            general_period_lookup.setdefault(key, []).append((season_year, ranges))

    total_events = 0
    parsed_general_count = len(general_items)

    # sanity: if general parse almost empty, fail with better debugging
    if parsed_general_count < 5:
        snippet = re.sub(r"\s+", " ", general_html[:800])
        raise RuntimeError(
            f"Parsed general species is too low ({parsed_general_count}). "
            f"Likely fetched wrong HTML.\nFirst 800 chars: {snippet}"
        )

    # Now build each calendar
    for cfg_path in cal_cfg_paths:
        cfg = load_json(cfg_path)
        filename = cfg.get("output_filename")
        if not filename:
            raise RuntimeError(f"Missing output_filename in {cfg_path}")

        output_path = os.path.join(out_dir, filename)

        use_local = bool(cfg.get("use_local", False))

        if not use_local:
            n = build_general_calendar(master, cfg, output_path)
            print(f"[OK] {filename}: {n} events (general)")
            total_events += n
        else:
            # local calendars
            local_html = fetch_html(LOCAL_URL)
            local_rows = parse_local(local_html)
            if len(local_rows) == 0:
                snippet = re.sub(r"\s+", " ", local_html[:900])
                raise RuntimeError(
                    f"0 local parsed - HTML changed or wrong page fetched.\n"
                    f"First 900 chars: {snippet}"
                )
            n = build_local_calendar(master, cfg, output_path, general_period_lookup)
            print(f"[OK] {filename}: {n} events (local)")
            total_events += n

    print(f"Done. Generated ICS files. Total events: {total_events}")


if __name__ == "__main__":
    main()
