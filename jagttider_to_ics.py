#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

# ----------------------------
# Constants / Helpers
# ----------------------------

OUT_DIR = "Jagttids-Kalender"
MASTER_PATH = os.path.join("configs", "master.json")
CALENDAR_DIR = os.path.join("configs", "calendars")

UA = "Mozilla/5.0 (compatible; JagttiderBot/1.0; +https://github.com/)"

DANISH_MONTHS = {
    "januar": 1,
    "februar": 2,
    "marts": 3,
    "april": 4,
    "maj": 5,
    "juni": 6,
    "juli": 7,
    "august": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
}

DATE_RANGE_RE = re.compile(
    r"(?P<d1>\d{1,2})[./](?P<m1>\d{1,2})\s*[-–]\s*(?P<d2>\d{1,2})[./](?P<m2>\d{1,2})"
)

# example: "01.09 - 31.01" or "16.05 - 15.07 og 01.10 - 31.01"
DATE_RANGE_ANY_RE = re.compile(r"\d{1,2}[./]\d{1,2}\s*[-–]\s*\d{1,2}[./]\d{1,2}")

NO_HUNTING_RE = re.compile(r"\bingen\s+jagttid\b", re.IGNORECASE)

# Special “Saturday rules” seen in local page:
# "1. og 2. lørdag i november"
SAT_1_2_RE = re.compile(r"1\.\s*og\s*2\.\s*lørdag\s*i\s*(\w+)", re.IGNORECASE)
# "alle lørdag i december" / "alle lørdage i december"
SAT_ALL_RE = re.compile(r"alle\s*lørdage?\s*i\s*(\w+)", re.IGNORECASE)


def http_get(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=30)
    r.raise_for_status()
    return r.text


def soup_main_text(html: str) -> List[str]:
    """
    Extracts readable lines from main content.
    We intentionally do not rely on specific CSS classes because DJ pages shift.
    """
    soup = BeautifulSoup(html, "html.parser")
    # Remove scripts/styles
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text("\n")
    # Normalize lines
    lines = []
    for line in text.splitlines():
        s = " ".join(line.strip().split())
        if not s:
            continue
        lines.append(s)
    return lines


def ics_escape(s: str) -> str:
    s = s.replace("\\", "\\\\")
    s = s.replace(";", r"\;").replace(",", r"\,")
    s = s.replace("\n", r"\n")
    return s


def fold_ics_line(line: str) -> str:
    """
    Fold lines at 75 octets-ish. We'll do safe char-count folding.
    """
    if len(line) <= 75:
        return line
    out = []
    while len(line) > 75:
        out.append(line[:75])
        line = " " + line[75:]
    out.append(line)
    return "\r\n".join(out)


def dtstamp_utc() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def uid_for(kind: str, name: str, start: date, end: date, area: str = "") -> str:
    base = f"{kind}|{name}|{start.isoformat()}|{end.isoformat()}|{area}"
    # deterministic-ish uid
    import hashlib
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]
    return f"{h}@jagttider"


@dataclass
class SpeciesMeta:
    image_url: str = ""
    shooting_time_text: str = ""
    aliases: List[str] = None


def load_master() -> dict:
    with open(MASTER_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_calendar_configs() -> List[dict]:
    cfgs = []
    for fn in sorted(os.listdir(CALENDAR_DIR)):
        if not fn.lower().endswith(".json"):
            continue
        path = os.path.join(CALENDAR_DIR, fn)
        with open(path, "r", encoding="utf-8") as f:
            cfgs.append(json.load(f))
    return cfgs


def build_species_index(master: dict) -> Dict[str, SpeciesMeta]:
    idx: Dict[str, SpeciesMeta] = {}
    for sp in master.get("species", []):
        name = sp["name"]
        meta = SpeciesMeta(
            image_url=sp.get("image_url", "") or "",
            shooting_time_text=sp.get("shooting_time_text", "") or "",
            aliases=sp.get("aliases", []) or [],
        )
        idx[name.lower()] = meta
        for a in meta.aliases:
            idx[a.lower()] = meta
    return idx


# ----------------------------
# Season logic
# ----------------------------

def season_year_for(dt: date) -> int:
    """
    Define "jagtsæson-år" as year where season starts Aug 1.
    If date is before Aug 1, it belongs to previous season year.
    """
    if (dt.month, dt.day) < (8, 1):
        return dt.year - 1
    return dt.year


def season_start(season_year: int) -> date:
    return date(season_year, 8, 1)


def season_end(season_year: int) -> date:
    return date(season_year + 1, 7, 31)


def expand_seasons(seasons_ahead: int) -> List[int]:
    today = date.today()
    cur = season_year_for(today)
    return [cur + i for i in range(0, seasons_ahead + 1)]


def pick_year_for_range(season_year: int, m1: int, d1: int, m2: int, d2: int) -> Tuple[date, date]:
    """
    If range crosses new year (m2 < m1), end is in season_year+1 calendar year.
    Otherwise both are in season_year calendar year.
    BUT: if m1 is in Jan-Jul, it likely belongs to season_year+1 year.
    We'll anchor by hunting season: Aug-Dec in season_year, Jan-Jul in season_year+1.
    """
    def actual_year(month: int) -> int:
        return season_year if month >= 8 else season_year + 1

    y1 = actual_year(m1)
    y2 = actual_year(m2)
    start = date(y1, m1, d1)
    end = date(y2, m2, d2)
    return start, end


# ----------------------------
# Parsing: General page
# ----------------------------

def parse_general_periods(lines: List[str]) -> Dict[str, List[Tuple[str, str]]]:
    """
    Returns species -> list of "DD.MM - DD.MM" strings (raw ranges).
    We keep raw and expand later per season.
    """
    species_ranges: Dict[str, List[Tuple[str, str]]] = {}

    # We detect lines like:
    # "Gråand 01.09 - 31.12"
    # "Råbuk*** 16.05 - 15.07 og 01.10 - 31.01"
    # "Sædgås** Ingen generel jagttid (se lokal)"
    for line in lines:
        # Quick skip: headers
        if len(line) < 6:
            continue

        # Must contain a date-range OR "Ingen ... jagttid"
        if not DATE_RANGE_ANY_RE.search(line) and "Ingen" not in line and "ingen" not in line:
            continue

        # split on multiple spaces? We'll parse "species" as text before first date or before "Ingen"
        # Find first date match position:
        m = DATE_RANGE_ANY_RE.search(line)
        if m:
            species_part = line[:m.start()].strip()
            # remove trailing asterisks
            species_part = re.sub(r"[*]+$", "", species_part).strip()
            # get all ranges
            raw_ranges = DATE_RANGE_RE.findall(line)
            if not raw_ranges:
                continue
            for (d1, m1, d2, m2) in raw_ranges:
                r1 = f"{int(d1):02d}.{int(m1):02d}"
                r2 = f"{int(d2):02d}.{int(m2):02d}"
                species_ranges.setdefault(species_part, []).append((r1, r2))
        else:
            # no range, might be "Ingen jagttid" - we do nothing for general calendar (user said: if no general time, just no event)
            continue

    return species_ranges


# ----------------------------
# Parsing: Local page
# ----------------------------

@dataclass
class LocalRule:
    region: str
    area: str
    species: str
    rule_text: str  # e.g. "01.11-15.01" or "ingen jagttid" or "1. og 2. lørdag i november"

def parse_local_rules(lines: List[str]) -> List[LocalRule]:
    """
    Local page structure in text looks like:
      "## Region Midtjylland - lokale jagttider"
      "### Region Midtjylland undtagen Endelave"
      "Hare 01.11-15.01"
      "Agerhøne 16.9-15.10"
      "### Øen Endelave"
      "Råvildt 01.10-08.10"
      "Hare ingen jagttid"
    We'll detect Region headings and Area headings and then parse species lines.
    """
    rules: List[LocalRule] = []
    cur_region = ""
    cur_area = ""

    # Normalize some heading markers: the extracted lines might not include "##" / "###".
    # We simply look for "Region X - lokale jagttider" and "Øen ..." etc, and also bullet headings on page.
    for line in lines:
        if "Region " in line and "lokale jagttider" in line:
            cur_region = line.replace(" - lokale jagttider", "").strip()
            cur_area = "Hele regionen"
            continue

        # Area-like headings:
        # "Hele regionen" / "Region Midtjylland undtagen Endelave" / "Øen Endelave" / "Øen Ærø" etc
        if line.lower().startswith("hele regionen"):
            cur_area = "Hele regionen"
            continue
        if line.lower().startswith("region ") and "undtagen" in line.lower():
            cur_area = line.strip()
            continue
        if line.lower().startswith("øen "):
            cur_area = line.strip()
            continue

        # Parse species line: "<species> <rule>"
        # rule could be a date range, "ingen jagttid" or Saturday-pattern text.
        # Example: "Fasanhan 1. og 2. lørdag i oktober, 1. og 2. lørdag i november, samt alle lørdag i december"
        if not cur_region:
            continue

        # We accept a line if it contains a date range OR "ingen jagttid" OR "lørdag"
        if not DATE_RANGE_ANY_RE.search(line) and not NO_HUNTING_RE.search(line) and "lørdag" not in line.lower():
            continue

        # Split species from rule: assume first date/keyword begins rule
        split_pos = None
        m = DATE_RANGE_ANY_RE.search(line)
        if m:
            split_pos = m.start()
        else:
            mh = NO_HUNTING_RE.search(line)
            if mh:
                split_pos = mh.start()
            else:
                ml = re.search(r"\blørdag\b", line, re.IGNORECASE)
                if ml:
                    split_pos = ml.start()

        if split_pos is None or split_pos <= 0:
            continue

        species = line[:split_pos].strip()
        rule_text = line[split_pos:].strip()

        # Clean species asterisks (just in case)
        species = re.sub(r"[*]+$", "", species).strip()

        rules.append(LocalRule(region=cur_region, area=cur_area, species=species, rule_text=rule_text))

    return rules


# ----------------------------
# Special rule expansion
# ----------------------------

def first_and_second_saturday(year: int, month: int) -> List[date]:
    # find first Saturday
    d = date(year, month, 1)
    while d.weekday() != 5:  # Saturday=5
        d += timedelta(days=1)
    first = d
    second = first + timedelta(days=7)
    return [first, second]


def all_saturdays(year: int, month: int) -> List[date]:
    d = date(year, month, 1)
    while d.weekday() != 5:
        d += timedelta(days=1)
    out = []
    while d.month == month:
        out.append(d)
        d += timedelta(days=7)
    return out


def expand_local_rule_to_dates(rule_text: str, season_year: int) -> List[Tuple[date, date, str]]:
    """
    Returns list of (start_date, end_date, note) for a given season.
    - Date ranges become 1 all-day event (inclusive end).
    - Saturday rules become 1-day events (start=end)
    - "ingen jagttid" handled elsewhere
    """
    out: List[Tuple[date, date, str]] = []

    # Date ranges
    ranges = DATE_RANGE_RE.findall(rule_text)
    for (d1, m1, d2, m2) in ranges:
        start, end = pick_year_for_range(season_year, int(m1), int(d1), int(m2), int(d2))
        out.append((start, end, ""))

    # Saturday rules
    # 1. og 2. lørdag i <month>
    for m in SAT_1_2_RE.findall(rule_text):
        month_name = m.lower()
        if month_name not in DANISH_MONTHS:
            continue
        month = DANISH_MONTHS[month_name]
        # month belongs to season_year if Aug-Dec else season_year+1
        y = season_year if month >= 8 else season_year + 1
        for d in first_and_second_saturday(y, month):
            out.append((d, d, "1. og 2. lørdag"))

    # alle lørdage i <month>
    for m in SAT_ALL_RE.findall(rule_text):
        month_name = m.lower()
        if month_name not in DANISH_MONTHS:
            continue
        month = DANISH_MONTHS[month_name]
        y = season_year if month >= 8 else season_year + 1
        for d in all_saturdays(y, month):
            out.append((d, d, "Alle lørdage"))

    # If nothing parsed but has text, keep as note-only (no dates)
    return out


# ----------------------------
# Event building
# ----------------------------

def build_general_events(
    general_periods: Dict[str, List[Tuple[str, str]]],
    seasons: List[int],
    species_meta_idx: Dict[str, SpeciesMeta],
    calendar_name: str,
    source_url: str,
) -> List[str]:
    """
    Returns ICS VEVENT blocks as strings.
    """
    events = []

    for species, ranges in general_periods.items():
        meta = species_meta_idx.get(species.lower())
        shoot_txt = meta.shooting_time_text if meta else ""
        img = meta.image_url if meta else ""

        for season_year in seasons:
            for (r1, r2) in ranges:
                d1, m1 = map(int, r1.split("."))
                d2, m2 = map(int, r2.split("."))
                start, end = pick_year_for_range(season_year, m1, d1, m2, d2)

                summary = f"{species} (Generel)"
                desc_parts = [
                    f"Skydetid: (se generelle regler / evt. artsnote)",
                    f"Periode: {r1} til {r2}",
                    f"Kilde: {source_url}",
                ]
                if shoot_txt:
                    desc_parts.insert(0, f"Skydetid: {shoot_txt}")
                if img:
                    desc_parts.append(f"Billede: {img}")

                description = "\n".join(desc_parts)

                uid = uid_for("general", species, start, end)
                events.append(make_vevent(uid, summary, description, start, end))

    return events


def build_local_events(
    local_rules: List[LocalRule],
    general_periods: Dict[str, List[Tuple[str, str]]],
    seasons: List[int],
    species_meta_idx: Dict[str, SpeciesMeta],
    calendar_cfg: dict,
    master: dict,
) -> List[str]:
    """
    local calendar:
      - include/exclude areas
      - create events for local date ranges and special saturday rules
      - if emit_no_hunting_events: create "INGEN JAGTTID" for species where local says so,
        but only during each general period for that species.
    """
    inc = [s.lower() for s in calendar_cfg["filters"].get("include_area_keywords", [])]
    exc = [s.lower() for s in calendar_cfg["filters"].get("exclude_area_keywords", [])]
    emit_no = bool(calendar_cfg.get("local_rules", {}).get("emit_no_hunting_events", False))

    region_map = master.get("defaults", {}).get("region_map_image_url", "")
    local_url = master.get("sources", {}).get("local_url", "")

    events = []

    def area_allowed(text: str) -> bool:
        t = text.lower()
        if inc:
            ok = any(k in t for k in inc)
            if not ok:
                return False
        if exc:
            if any(k in t for k in exc):
                return False
        return True

    for rule in local_rules:
        area_key = f"{rule.region} | {rule.area}"
        if not area_allowed(area_key):
            continue

        meta = species_meta_idx.get(rule.species.lower())
        img = meta.image_url if meta else ""
        shoot_txt = meta.shooting_time_text if meta else ""

        # NO HUNTING
        if NO_HUNTING_RE.search(rule.rule_text):
            if not emit_no:
                continue

            # Only during general periods for that species
            gen_ranges = general_periods.get(rule.species, [])
            if not gen_ranges:
                # If we can't find general periods, we skip (avoids "whole year" spam)
                continue

            for season_year in seasons:
                for (r1, r2) in gen_ranges:
                    d1, m1 = map(int, r1.split("."))
                    d2, m2 = map(int, r2.split("."))
                    start, end = pick_year_for_range(season_year, m1, d1, m2, d2)

                    summary = f"{rule.species} — INGEN JAGTTID ({rule.area})"
                    desc_parts = [
                        f"LOKAL REGEL: ingen jagttid i dette område.",
                        f"Område: {rule.region} / {rule.area}",
                        f"Gælder kun i perioden hvor arten ellers har generel jagttid: {r1} til {r2}",
                        f"Kilde (lokal): {local_url}",
                    ]
                    if shoot_txt:
                        desc_parts.insert(0, f"Skydetid (art): {shoot_txt}")
                    if region_map:
                        desc_parts.append(f"Regionkort: {region_map}")
                    if img:
                        desc_parts.append(f"Billede: {img}")

                    uid = uid_for("nohunt", rule.species, start, end, area_key)
                    events.append(make_vevent(uid, summary, "\n".join(desc_parts), start, end))
            continue

        # Normal date ranges + saturday rules
        for season_year in seasons:
            expanded = expand_local_rule_to_dates(rule.rule_text, season_year)

            # If this rule is only text and didn't parse, we skip (or you can choose to make note events later)
            if not expanded:
                continue

            for (start, end, note) in expanded:
                # title
                if start == end:
                    when = start.strftime("%d.%m.%Y")
                else:
                    when = f"{start.strftime('%d.%m.%Y')} - {end.strftime('%d.%m.%Y')}"
                summary = f"{rule.species} (Lokalt: {rule.area})"

                desc_parts = [
                    f"Skydetid: (lokale regler kan gælde)",
                    f"Periode/regel: {rule.rule_text}",
                    f"Område: {rule.region} / {rule.area}",
                    f"Kilde (lokal): {local_url}",
                ]
                if shoot_txt:
                    desc_parts.insert(0, f"Skydetid (art): {shoot_txt}")
                if note:
                    desc_parts.append(f"Note: {note}")
                if region_map:
                    desc_parts.append(f"Regionkort: {region_map}")
                if img:
                    desc_parts.append(f"Billede: {img}")

                uid = uid_for("local", rule.species, start, end, area_key + "|" + rule.rule_text)
                events.append(make_vevent(uid, summary, "\n".join(desc_parts), start, end))

    return events


def make_vevent(uid: str, summary: str, description: str, start: date, end: date) -> str:
    """
    All-day event, inclusive end.
    In ICS, DTEND is non-inclusive, so we add +1 day.
    """
    dtend = end + timedelta(days=1)

    lines = [
        "BEGIN:VEVENT",
        f"UID:{ics_escape(uid)}",
        f"DTSTAMP:{dtstamp_utc()}",
        f"SUMMARY:{ics_escape(summary)}",
        f"DESCRIPTION:{ics_escape(description)}",
        f"DTSTART;VALUE=DATE:{ymd(start)}",
        f"DTEND;VALUE=DATE:{ymd(dtend)}",
        "END:VEVENT",
    ]
    return "\r\n".join(fold_ics_line(l) for l in lines)


def write_ics(path: str, calname: str, events: List[str]) -> None:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//luka2945//Jagttider ICS//DA",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{ics_escape(calname)}",
    ]
    lines.extend(events)
    lines.append("END:VCALENDAR")

    data = "\r\n".join(lines) + "\r\n"

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(data)


# ----------------------------
# Main
# ----------------------------

def main() -> int:
    master = load_master()
    species_idx = build_species_index(master)
    calendars = load_calendar_configs()

    general_url = master["sources"]["general_url"]
    local_url = master["sources"]["local_url"]

    # Fetch + parse pages
    general_lines = soup_main_text(http_get(general_url))
    local_lines = soup_main_text(http_get(local_url))

    general_periods = parse_general_periods(general_lines)
    local_rules = parse_local_rules(local_lines)

    if len(general_periods) < 10:
        raise RuntimeError(
            f"Parsed general species is too low ({len(general_periods)}). HTML changed or fetch failed."
        )

    if len(local_rules) < 5:
        raise RuntimeError(
            f"Parsed local rules is too low ({len(local_rules)}). HTML changed or fetch failed."
        )

    for cfg in calendars:
        calname = cfg["calendar_name"]
        seasons_ahead = int(cfg.get("seasons_ahead", 2))
        seasons = expand_seasons(seasons_ahead)

        events: List[str] = []

        if cfg.get("use_local", False):
            events = build_local_events(
                local_rules=local_rules,
                general_periods=general_periods,
                seasons=seasons,
                species_meta_idx=species_idx,
                calendar_cfg=cfg,
                master=master,
            )
        else:
            events = build_general_events(
                general_periods=general_periods,
                seasons=seasons,
                species_meta_idx=species_idx,
                calendar_name=calname,
                source_url=general_url,
            )

        out_path = os.path.join(OUT_DIR, cfg["output_filename"])
        write_ics(out_path, calname, events)

        print(f"Wrote {out_path} with {len(events)} events")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        raise
