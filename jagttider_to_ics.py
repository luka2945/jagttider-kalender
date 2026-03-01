import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


GENERAL_URL = "https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/"
LOCAL_URL = "https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/lokale-jagttider/"

USER_AGENT = "jagttider-ics-bot/1.0 (+github actions)"


# ----------------------------
# Helpers: dates / parsing
# ----------------------------
MONTHS_DA = {
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

WEEKDAYS_DA = {
    "mandag": 0,
    "tirsdag": 1,
    "onsdag": 2,
    "torsdag": 3,
    "fredag": 4,
    "lørdag": 5,
    "søndag": 6,
}


def season_year_auto(today: Optional[date] = None) -> int:
    """
    Jagtsæsonen vi vil vise som standard:
    - Fra marts og frem: næste sæson starter samme år (Sep->Jan/Feb), så brug current year
    - Jan/Feb: vi er stadig i "sidste års" sæson, så brug previous year
    """
    today = today or date.today()
    if today.month >= 3:
        return today.year
    return today.year - 1


def parse_day_month(s: str) -> Tuple[int, int]:
    """
    Parse '01.09' / '1.9' into (day, month) – ALWAYS day first.
    """
    s = s.strip()
    s = s.replace(" ", "")
    m = re.match(r"^(\d{1,2})\.(\d{1,2})$", s)
    if not m:
        raise ValueError(f"Invalid day.month: {s}")
    d = int(m.group(1))
    mo = int(m.group(2))
    return d, mo


def safe_date(y: int, m: int, d: int) -> date:
    return date(y, m, d)  # will throw if invalid (good)


def range_to_season_dates(start_dm: Tuple[int, int], end_dm: Tuple[int, int], season_year: int) -> Tuple[date, date]:
    """
    Convert dd.mm-dd.mm into actual dates across season boundary.
    Rule: season_year is the year where Sep-Dec happen.
    If month in [1,2] => belongs to season_year+1 for typical seasons.
    Also supports ranges fully inside same year.
    """
    sd, sm = start_dm
    ed, em = end_dm

    sy = season_year if sm >= 3 else season_year + 1  # e.g. start 01.02 in season => next year
    ey = season_year if em >= 3 else season_year + 1

    # Most general hunting seasons are Sep->Jan/Feb, but local can be Oct->Oct etc.
    # If start month is >=3 and end month is <3, end is next year. This already happens above.
    # If local has e.g. 16.05-15.07 => both >=3 => same season_year.
    start = safe_date(sy, sm, sd)
    end = safe_date(ey, em, ed)
    return start, end


def parse_date_ranges(text: str) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """
    Parse things like:
    - '01.09 - 31.01'
    - '16.05 - 15.07 og 01.10 - 31.01'
    - '16.05-15.07 og 01.10-31.01'
    into list of ((d,m),(d,m))
    """
    t = text.strip()
    t = t.replace("–", "-")
    t = re.sub(r"\s+", " ", t)

    # split by ' og ' (and)
    parts = [p.strip() for p in re.split(r"\s+og\s+", t) if p.strip()]
    ranges = []
    for p in parts:
        p = p.replace(" ", "")
        m = re.match(r"^(\d{1,2}\.\d{1,2})-(\d{1,2}\.\d{1,2})$", p)
        if not m:
            # allow "01.09-31.01" (already) else try with spaces
            m = re.match(r"^(\d{1,2}\.\d{1,2})\s*-\s*(\d{1,2}\.\d{1,2})$", p)
        if not m:
            raise ValueError(f"Could not parse range: {text}")
        start_dm = parse_day_month(m.group(1))
        end_dm = parse_day_month(m.group(2))
        ranges.append((start_dm, end_dm))
    return ranges


def nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> date:
    """
    e.g. 1st Saturday of November.
    weekday: Monday=0 ... Sunday=6
    """
    d = date(year, month, 1)
    # shift to first weekday
    shift = (weekday - d.weekday()) % 7
    d = d + timedelta(days=shift)
    # nth occurrence
    d = d + timedelta(days=7 * (n - 1))
    return d


def all_weekdays_in_month(year: int, month: int, weekday: int) -> List[date]:
    d = date(year, month, 1)
    res = []
    shift = (weekday - d.weekday()) % 7
    d = d + timedelta(days=shift)
    while d.month == month:
        res.append(d)
        d = d + timedelta(days=7)
    return res


def clean_text(s: str) -> str:
    # handle weird zero-width chars on the site
    s = s.replace("\u200c", "").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", s).strip()


# ----------------------------
# Data models
# ----------------------------
@dataclass
class HuntingRule:
    species: str
    area: str  # "(generel)" or "Region ..." or "Øen ..."
    kind: str  # "period" | "none" | "special_days"
    raw: str   # raw text from site
    ranges_dm: Optional[List[Tuple[Tuple[int, int], Tuple[int, int]]]] = None
    special_dates: Optional[List[date]] = None


# ----------------------------
# Fetch and parse pages
# ----------------------------
def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.text


def parse_general_rules(html: str) -> Tuple[List[HuntingRule], Dict[str, str]]:
    """
    Returns:
      - general hunting period rules (species -> date ranges)
      - shooting-time notes mapping (species or group keywords -> note)
    The page is semi-structured, so we do robust text parsing.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = clean_text(soup.get_text("\n"))

    # Extract lines that look like: "<species> 01.09 - 31.01" or "... ingen jagttid"
    lines = [clean_text(l) for l in text.split("\n") if clean_text(l)]
    rules: List[HuntingRule] = []

    # Heuristic: after a species name, there is often a date range.
    # We detect by presence of dd.mm and '-' in same line.
    for line in lines:
        # skip obvious non-data
        if "Del denne side" in line or "Tilmeld" in line:
            continue

        m = re.search(r"(\d{1,2}\.\d{1,2})\s*-\s*(\d{1,2}\.\d{1,2})", line)
        if m:
            # split by last occurrence of date start (species part before it)
            parts = re.split(r"(\d{1,2}\.\d{1,2}\s*-\s*\d{1,2}\.\d{1,2}.*)$", line, maxsplit=1)
            if len(parts) >= 2:
                species = clean_text(parts[0]).rstrip("*").strip()
                date_part = clean_text(parts[1])
                if species:
                    try:
                        ranges_dm = parse_date_ranges(date_part)
                        rules.append(HuntingRule(
                            species=species,
                            area="(generel)",
                            kind="period",
                            raw=date_part,
                            ranges_dm=ranges_dm
                        ))
                    except Exception:
                        # ignore weird lines
                        pass
            continue

        if re.search(r"\bingen jagttid\b", line, flags=re.IGNORECASE):
            # sometimes appears in general tables; usually we don't need to emit events for general "none"
            # but we record it anyway
            parts = re.split(r"\bingen jagttid\b", line, flags=re.IGNORECASE)
            species = clean_text(parts[0]).rstrip("*").strip()
            if species:
                rules.append(HuntingRule(
                    species=species,
                    area="(generel)",
                    kind="none",
                    raw="ingen jagttid"
                ))

    # Shooting time notes: look for sentences like "... må jages i tiden fra ..."
    # We'll map by species tokens included in sentence.
    notes: Dict[str, str] = {}
    for line in lines:
        if "må jages i tiden fra" in line and "solopgang" in line:
            # Example from page:
            # "Ænder og gæs må jages i tiden fra 1½ time før solopgang til 1½ time efter solnedgang"
            # We'll store with key = left side before "må jages"
            left = line.split("må jages", 1)[0].strip().rstrip(":").strip()
            note = line.strip()
            if left:
                notes[left] = note

    return rules, notes


def parse_local_rules(html: str) -> List[HuntingRule]:
    """
    Local page has nice headings in the text:
      ## Region X - lokale jagttider
        * ### <area>
          Species <rule>
    We parse by walking through text lines.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = clean_text(soup.get_text("\n"))
    lines = [clean_text(l) for l in text.split("\n") if clean_text(l)]

    region = None
    area = None
    rules: List[HuntingRule] = []

    for line in lines:
        # region headings
        m_reg = re.match(r"^Region\s+(.+?)\s+-\s+lokale jagttider$", line, flags=re.IGNORECASE)
        if m_reg:
            region = clean_text(m_reg.group(1))
            area = None
            continue

        # area headings (often prefixed with bullet in HTML; text output removes bullet but keeps heading)
        # It may appear as: "Øen Fejø" or "Hele regionen" or "Bornholms Kommune" etc
        if line in ("Hele regionen",) or line.startswith("Øen ") or line.endswith(" Kommune") or "Region" in line and "undtagen" in line:
            # only if we are inside a region section
            if region:
                area = line
            continue

        if not region or not area:
            continue

        # Ignore navigation links
        if line.startswith("Leder du efter"):
            continue

        # Species lines:
        # "Hare 01.11-15.01"
        # "Ræv ingen jagttid"
        # "Fasanhane 1. og 2. lørdag i oktober, ... "
        parts = line.split(" ", 1)
        if len(parts) != 2:
            continue
        species = clean_text(parts[0]).rstrip("*").strip()
        rule_text = clean_text(parts[1])

        if not species or not rule_text:
            continue

        # none
        if re.search(r"\bingen jagttid\b", rule_text, flags=re.IGNORECASE):
            rules.append(HuntingRule(
                species=species,
                area=f"{region} | {area}",
                kind="none",
                raw=rule_text
            ))
            continue

        # special weekday rules (lørdag)
        if "lørdag" in rule_text.lower():
            # normalize weird spacing
            rt = rule_text.lower()
            rt = clean_text(rt)
            special_dates: List[date] = []

            # examples:
            # "1. og 2. lørdag i november"
            # "1. og 2. lørdag i oktober, 1. og 2. lørdag i november, samt alle lørdag i december"
            # We'll parse all occurrences:
            # - "1. og 2. lørdag i <month>"
            # - "alle lørdag i <month>"
            # Note: these are in autumn/winter => use season_year (Sep-Dec) for Oct/Nov/Dec
            # We'll store raw and resolve later per config/season_year.
            rules.append(HuntingRule(
                species=species,
                area=f"{region} | {area}",
                kind="special_days",
                raw=rule_text,
                special_dates=None
            ))
            continue

        # period(s)
        try:
            ranges_dm = parse_date_ranges(rule_text)
            rules.append(HuntingRule(
                species=species,
                area=f"{region} | {area}",
                kind="period",
                raw=rule_text,
                ranges_dm=ranges_dm
            ))
        except Exception:
            # unknown format => skip
            continue

    return rules


def resolve_special_days(raw: str, season_year: int) -> List[date]:
    """
    Resolve Danish text describing Saturdays into concrete dates.
    Supported:
      - "1. og 2. lørdag i <month>"
      - multiple segments separated by comma and/or "samt"
      - "alle lørdag i <month>"
    """
    t = clean_text(raw.lower())
    t = t.replace("–", "-")

    results: List[date] = []

    # find patterns like "1. og 2. lørdag i november"
    for m in re.finditer(r"1\.\s*og\s*2\.\s*lørdag\s*i\s*([a-zæøå]+)", t):
        mon_name = m.group(1)
        mon = MONTHS_DA.get(mon_name)
        if not mon:
            continue
        year = season_year if mon >= 3 else season_year + 1
        d1 = nth_weekday_of_month(year, mon, WEEKDAYS_DA["lørdag"], 1)
        d2 = nth_weekday_of_month(year, mon, WEEKDAYS_DA["lørdag"], 2)
        results.extend([d1, d2])

    # find patterns like "alle lørdag i december"
    for m in re.finditer(r"alle\s*lørdag\s*i\s*([a-zæøå]+)", t):
        mon_name = m.group(1)
        mon = MONTHS_DA.get(mon_name)
        if not mon:
            continue
        year = season_year if mon >= 3 else season_year + 1
        results.extend(all_weekdays_in_month(year, mon, WEEKDAYS_DA["lørdag"]))

    # de-dup + sort
    results = sorted(set(results))
    return results


# ----------------------------
# ICS generation
# ----------------------------
def ics_escape(s: str) -> str:
    s = s.replace("\\", "\\\\")
    s = s.replace("\n", "\\n")
    s = s.replace(",", "\\,")
    s = s.replace(";", "\\;")
    return s


def ics_dtstamp() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def ics_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def build_description(skydetid: Optional[str], periode: str, extra_lines: List[str], source_url: str) -> str:
    lines = []
    if skydetid:
        lines.append(f"Skydetid: {skydetid}")
    lines.append(f"Periode: {periode}")
    if extra_lines:
        lines.append("")  # blank line
        lines.extend(extra_lines)
    lines.append("")
    lines.append(f"Kilde: {source_url}")
    return "\n".join(lines).strip()


def write_ics(filename: str, events: List[str], cal_name: str) -> None:
    out = []
    out.append("BEGIN:VCALENDAR")
    out.append("VERSION:2.0")
    out.append(f"PRODID:{ics_escape('-//luka2945//Jagttider ICS//DA')}")
    out.append("CALSCALE:GREGORIAN")
    out.append(f"X-WR-CALNAME:{ics_escape(cal_name)}")
    out.extend(events)
    out.append("END:VCALENDAR")
    Path(filename).write_text("\n".join(out) + "\n", encoding="utf-8")


def make_event_all_day(summary: str, start: date, end_exclusive: date, description: str, attach_url: Optional[str] = None) -> str:
    uid = str(uuid.uuid4())
    lines = []
    lines.append("BEGIN:VEVENT")
    lines.append(f"UID:{uid}")
    lines.append(f"DTSTAMP:{ics_dtstamp()}")
    lines.append(f"SUMMARY:{ics_escape(summary)}")
    lines.append(f"DTSTART;VALUE=DATE:{ics_date(start)}")
    lines.append(f"DTEND;VALUE=DATE:{ics_date(end_exclusive)}")
    lines.append(f"DESCRIPTION:{ics_escape(description)}")
    if attach_url:
        # Many clients (incl Apple) will show this as an attachment/preview sometimes
        lines.append(f"ATTACH:{ics_escape(attach_url)}")
    lines.append("END:VEVENT")
    return "\n".join(lines)


# ----------------------------
# Config + filtering
# ----------------------------
def load_configs() -> List[dict]:
    cfg_dir = Path("configs")
    if not cfg_dir.exists():
        raise RuntimeError("Missing configs/ folder")
    cfgs = []
    for p in sorted(cfg_dir.glob("*.json")):
        cfgs.append(json.loads(p.read_text(encoding="utf-8")))
    return cfgs


def contains_any(haystack: str, keywords: List[str]) -> bool:
    h = haystack.lower()
    for k in keywords:
        if k.lower() in h:
            return True
    return False


def normalize_area_label(area: str) -> Tuple[str, str]:
    """
    area stored as "RegionName | AreaName"
    returns (region, area)
    """
    if " | " in area:
        r, a = area.split(" | ", 1)
        return r.strip(), a.strip()
    return "", area.strip()


def pick_skydetid_for_species(species: str, notes: Dict[str, str]) -> Optional[str]:
    """
    The notes are keyed by group label like "Ænder og gæs" or specific combos.
    We do best-effort mapping by keyword membership.
    """
    sp = species.lower()

    # If the note key contains multiple species or group names, we match by substring
    for key, note in notes.items():
        k = key.lower()
        # simple heuristics:
        if sp in k:
            return note.split("må jages i tiden fra", 1)[-1].strip()
        if "ænder" in k and ("and" in sp or sp.endswith("and")):
            return note.split("må jages i tiden fra", 1)[-1].strip()
        if "gæs" in k and ("gås" in sp or "gæs" in sp):
            return note.split("må jages i tiden fra", 1)[-1].strip()
        if "krage" in k and ("krage" in sp or "husskade" in sp):
            return note.split("må jages i tiden fra", 1)[-1].strip()

    return None


def build_calendars() -> None:
    general_html = fetch_html(GENERAL_URL)
    local_html = fetch_html(LOCAL_URL)

    general_rules, shooting_notes = parse_general_rules(general_html)
    local_rules = parse_local_rules(local_html)

    # Build lookup: species -> list of ranges (dm) from general
    general_ranges_dm: Dict[str, List[Tuple[Tuple[int, int], Tuple[int, int]]]] = {}
    for r in general_rules:
        if r.kind == "period" and r.ranges_dm:
            general_ranges_dm.setdefault(r.species, []).extend(r.ranges_dm)

    configs = load_configs()

    for cfg in configs:
        cal_type = cfg.get("type")
        output = cfg["output"]
        cal_name = cfg.get("calendar_name", output.replace(".ics", ""))
        season = cfg.get("season_year", "auto")
        season_y = season_year_auto() if season == "auto" else int(season)

        include_species = cfg.get("include_species_keywords", [])
        exclude_species = cfg.get("exclude_species_keywords", [])
        include_area = cfg.get("include_area_keywords", [])
        exclude_area = cfg.get("exclude_area_keywords", [])

        include_no_hunting = bool(cfg.get("include_no_hunting_events", False))
        exclude_islands = bool(cfg.get("exclude_islands", False))

        # optional attachments (URLs)
        species_images = cfg.get("species_image_urls", {})  # { "Gråand": "https://..." }
        area_images = cfg.get("area_image_urls", {})        # { "Region Nordjylland": "https://..." }
        attach_mode = cfg.get("attach_mode", "none")        # "none" | "species" | "area" | "both"

        events: List[str] = []

        if cal_type == "general":
            # Only general period events (skip "ingen jagttid" in general calendar)
            for r in general_rules:
                if r.kind != "period" or not r.ranges_dm:
                    continue

                if include_species and not contains_any(r.species, include_species):
                    continue
                if exclude_species and contains_any(r.species, exclude_species):
                    continue

                # Create events for each range
                for dm_range in r.ranges_dm:
                    start, end = range_to_season_dates(dm_range[0], dm_range[1], season_y)
                    # ICS DTEND is exclusive => add 1 day
                    end_excl = end + timedelta(days=1)

                    skyd = pick_skydetid_for_species(r.species, shooting_notes)
                    periode_txt = f"{dm_range[0][0]:02d}.{dm_range[0][1]:02d} til {dm_range[1][0]:02d}.{dm_range[1][1]:02d}"
                    desc = build_description(
                        skydetid=skyd,
                        periode=periode_txt,
                        extra_lines=[],
                        source_url=GENERAL_URL
                    )
                    summary = f"{r.species} – generel jagttid"

                    attach = None
                    if attach_mode in ("species", "both"):
                        attach = species_images.get(r.species)
                    events.append(make_event_all_day(summary, start, end_excl, desc, attach_url=attach))

        elif cal_type == "local":
            # Filter local by region/area keywords
            for r in local_rules:
                region, area = normalize_area_label(r.area)

                # exclude islands if requested (anything starting with "Øen ")
                if exclude_islands and area.lower().startswith("øen "):
                    continue

                # area filters match against "Region | Area"
                full_area = f"{region} {area}".strip()
                if include_area and not contains_any(full_area, include_area):
                    continue
                if exclude_area and contains_any(full_area, exclude_area):
                    continue

                if include_species and not contains_any(r.species, include_species):
                    continue
                if exclude_species and contains_any(r.species, exclude_species):
                    continue

                # Summary formatting:
                # - local: "Gråand – lokal tid (Nordfyn Kommune)" etc
                # We use the area name after "|"
                area_label = area

                # Attachment: region map or species, depending on config
                attach = None
                if attach_mode in ("area", "both"):
                    attach = area_images.get(region) or area_images.get(area_label)
                if attach_mode in ("species", "both"):
                    attach = attach or species_images.get(r.species)

                # Handle kinds
                if r.kind == "period" and r.ranges_dm:
                    for dm_range in r.ranges_dm:
                        start, end = range_to_season_dates(dm_range[0], dm_range[1], season_y)
                        end_excl = end + timedelta(days=1)

                        skyd = pick_skydetid_for_species(r.species, shooting_notes)
                        periode_txt = f"{dm_range[0][0]:02d}.{dm_range[0][1]:02d} til {dm_range[1][0]:02d}.{dm_range[1][1]:02d}"
                        extra = [f"Lokalområde: {area_label}", f"Region: {region}"]
                        desc = build_description(
                            skydetid=skyd,
                            periode=periode_txt,
                            extra_lines=extra,
                            source_url=LOCAL_URL
                        )
                        summary = f"{r.species} – lokal tid ({area_label})"
                        events.append(make_event_all_day(summary, start, end_excl, desc, attach_url=attach))

                elif r.kind == "none":
                    # Only emit "ingen jagttid" events if enabled.
                    if not include_no_hunting:
                        continue

                    # We want "ingen jagttid" to cover ONLY the period where general would otherwise allow hunting.
                    gen_ranges = general_ranges_dm.get(r.species, [])
                    if not gen_ranges:
                        # fallback: skip if we can't map to general
                        continue

                    for dm_range in gen_ranges:
                        start, end = range_to_season_dates(dm_range[0], dm_range[1], season_y)
                        end_excl = end + timedelta(days=1)

                        periode_txt = f"{dm_range[0][0]:02d}.{dm_range[0][1]:02d} til {dm_range[1][0]:02d}.{dm_range[1][1]:02d}"
                        extra = [f"Lokalområde: {area_label}", f"Region: {region}", "Bemærkning: Ingen jagttid i dette område."]
                        desc = build_description(
                            skydetid=None,
                            periode=periode_txt,
                            extra_lines=extra,
                            source_url=LOCAL_URL
                        )
                        summary = f"{r.species} – INGEN jagttid ({area_label})"
                        events.append(make_event_all_day(summary, start, end_excl, desc, attach_url=attach))

                elif r.kind == "special_days":
                    # Convert text to specific dates (all-day single dates)
                    dates = resolve_special_days(r.raw, season_y)
                    if not dates:
                        continue

                    skyd = pick_skydetid_for_species(r.species, shooting_notes)
                    extra = [f"Lokalområde: {area_label}", f"Region: {region}", f"Bemærkning: {r.raw}"]
                    desc = build_description(
                        skydetid=skyd,
                        periode=r.raw,
                        extra_lines=extra,
                        source_url=LOCAL_URL
                    )
                    summary = f"{r.species} – lokal tid ({area_label})"
                    for d in dates:
                        events.append(make_event_all_day(summary, d, d + timedelta(days=1), desc, attach_url=attach))

        else:
            raise RuntimeError(f"Unknown config type: {cal_type}")

        # Sort events by DTSTART for stable diffs (optional but nice)
        # Since we only have strings, we do a simple extract:
        def dtstart_key(ev: str) -> str:
            m = re.search(r"DTSTART;VALUE=DATE:(\d{8})", ev)
            return m.group(1) if m else "99999999"

        events_sorted = sorted(events, key=dtstart_key)
        write_ics(output, events_sorted, cal_name)


def main():
    # Generate all calendars from configs/*.json
    build_calendars()
    print("Done. Generated .ics files.")


if __name__ == "__main__":
    main()
