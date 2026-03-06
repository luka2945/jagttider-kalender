import json
import re
from dataclasses import dataclass
from pathlib import Path
from datetime import date, datetime, timedelta
import calendar as calmod

import requests
from bs4 import BeautifulSoup

# -----------------------
# Paths / folders
# -----------------------
MASTER_PATH = Path("configs/master.json")
CALENDAR_CONFIG_DIR = Path("configs/calendars")
OUT_DIR = Path("Jagttids-Kalender")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# -----------------------
# Sources
# -----------------------
DEFAULT_GENERAL_URL = "https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/"
DEFAULT_LOCAL_URL   = "https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/lokale-jagttider/"

DEFAULT_USER_AGENT = "Mozilla/5.0 (JagttiderICSBot; +https://github.com/luka2945/jagttider-kalender)"
DEFAULT_LANG = "da-DK,da;q=0.9,en-US;q=0.7,en;q=0.6"

# -----------------------
# Danish months (for special rules)
# -----------------------
DK_MONTHS = {
    "januar": 1, "februar": 2, "marts": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "december": 12
}

RANGE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\s*[-–]\s*(\d{1,2})\.(\d{1,2})")

NTH_SAT_RE = re.compile(r"(\d)\.\s*og\s*(\d)\.\s*lørdag i ([a-zæøå]+)", re.I)
ALL_SAT_RE = re.compile(r"alle\s+lørdage\s+i\s+([a-zæøå]+)", re.I)

# -----------------------
# Data models
# -----------------------
@dataclass(frozen=True)
class SeasonRange:
    species: str
    start: date
    end_inclusive: date
    kind: str                 # "generel" or "lokal"
    area: str | None = None   # for local

@dataclass(frozen=True)
class NoHuntingMarker:
    species: str
    area: str

@dataclass(frozen=True)
class SpecialDayRule:
    species: str
    area: str
    dates: list[date]         # one-day events

# -----------------------
# Config loading
# -----------------------
def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def load_master() -> dict:
    if MASTER_PATH.exists():
        return load_json(MASTER_PATH)
    return {}

def list_calendar_configs() -> list[dict]:
    if not CALENDAR_CONFIG_DIR.exists():
        return []
    configs = []
    for p in sorted(CALENDAR_CONFIG_DIR.glob("*.json")):
        configs.append(load_json(p))
    return configs

def compute_season_year_auto(today: date | None = None) -> int:
    # Season year = year where season starts (typisk Jul-Dec)
    t = today or date.today()
    return t.year if t.month >= 7 else (t.year - 1)

def season_date(season_year: int, day: int, month: int) -> date | None:
    # Jul-Dec = season_year; Jan-Jun = season_year + 1
    y = season_year if month >= 7 else (season_year + 1)
    try:
        return date(y, month, day)
    except ValueError:
        return None

# -----------------------
# ICS helpers
# -----------------------
def ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

def ics_date(d: date) -> str:
    return d.strftime("%Y%m%d")

def build_event(uid: str, summary: str, start: date, end_inclusive: date, description: str, url: str | None = None) -> str:
    # DTEND is exclusive for all-day
    end_exclusive = end_inclusive + timedelta(days=1)
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{ics_escape(summary)}",
        f"DTSTART;VALUE=DATE:{ics_date(start)}",
        f"DTEND;VALUE=DATE:{ics_date(end_exclusive)}",
        f"DESCRIPTION:{ics_escape(description)}",
    ]
    if url:
        lines.append(f"URL:{ics_escape(url)}")
    lines.append("END:VEVENT")
    return "\n".join(lines)

def build_calendar(cal_name: str, events: list[str]) -> str:
    return "\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//luka2945//Jagttider ICS//DA",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{ics_escape(cal_name)}",
        *events,
        "END:VCALENDAR",
        ""
    ])

# -----------------------
# Fetch + soup
# -----------------------
def fetch_html(url: str, ua: str) -> str:
    r = requests.get(
        url,
        headers={
            "User-Agent": ua,
            "Accept-Language": DEFAULT_LANG,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
        timeout=30
    )
    r.raise_for_status()
    return r.text

def ensure_not_login_page(html: str) -> None:
    # DJ siden kan nogle gange returnere “du skal logge ind…”
    low = html.lower()
    if "du skal logge ind" in low or "logge ind" in low and "jagttider" in low and "<table" not in low:
        raise RuntimeError("Fik en 'log ind' side i stedet for jagttider-tabellen (kilde blokerer bot/kræver login).")

# -----------------------
# Parsing: GENERAL
# -----------------------
def parse_general(html: str, season_year: int) -> list[SeasonRange]:
    ensure_not_login_page(html)
    soup = BeautifulSoup(html, "lxml")

    ranges: list[SeasonRange] = []

    # Find alle tabeller og prøv at læse rækker med mindst 2 kolonner (art + periode)
    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) < 2:
                continue

            species = " ".join(tds[0].get_text(" ", strip=True).split())
            period_text = " ".join(tds[1].get_text(" ", strip=True).split())

            if not species or not period_text:
                continue

            # skip headings
            if species.lower() in ("vildtart", "art", "vildt"):
                continue

            # ignore “ingen jagttid” in general (du ønskede: ikke vise i generel)
            if "ingen jagttid" in period_text.lower():
                continue

            # Extract all numeric ranges in period cell
            for (d1, m1, d2, m2) in RANGE_RE.findall(period_text.replace("–", "-")):
                start = season_date(season_year, int(d1), int(m1))
                end = season_date(season_year, int(d2), int(m2))
                if start and end:
                    ranges.append(SeasonRange(species=species, start=start, end_inclusive=end, kind="generel"))

    # Deduplicate
    uniq = []
    seen = set()
    for r in ranges:
        key = (r.species.lower(), r.start, r.end_inclusive, r.kind, r.area)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq

# -----------------------
# Parsing: LOCAL (tables + “ingen jagttid” + special Saturdays)
# -----------------------
def nth_saturday(year: int, month: int, n: int) -> date | None:
    # Find nth Saturday (n=1..)
    c = calmod.Calendar(firstweekday=calmod.MONDAY)
    sats = [d for d in c.itermonthdates(year, month) if d.month == month and d.weekday() == 5]
    return sats[n - 1] if 1 <= n <= len(sats) else None

def all_saturdays(year: int, month: int) -> list[date]:
    c = calmod.Calendar(firstweekday=calmod.MONDAY)
    return [d for d in c.itermonthdates(year, month) if d.month == month and d.weekday() == 5]

def parse_special_text_to_dates(text: str, season_year: int) -> list[date]:
    # Returns list of one-day dates
    low = text.strip().lower()

    dates: list[date] = []

    # "1. og 2. lørdag i november"
    for m in NTH_SAT_RE.finditer(low):
        n1 = int(m.group(1))
        n2 = int(m.group(2))
        month_name = m.group(3).lower()
        if month_name not in DK_MONTHS:
            continue
        month = DK_MONTHS[month_name]
        year = season_year if month >= 7 else (season_year + 1)
        d_a = nth_saturday(year, month, n1)
        d_b = nth_saturday(year, month, n2)
        if d_a: dates.append(d_a)
        if d_b: dates.append(d_b)

    # "alle lørdage i december"
    for m in ALL_SAT_RE.finditer(low):
        month_name = m.group(1).lower()
        if month_name not in DK_MONTHS:
            continue
        month = DK_MONTHS[month_name]
        year = season_year if month >= 7 else (season_year + 1)
        dates.extend(all_saturdays(year, month))

    # Remove duplicates + sort
    return sorted(set(dates))

def parse_local(html: str, season_year: int) -> tuple[list[SeasonRange], list[NoHuntingMarker], list[SpecialDayRule]]:
    ensure_not_login_page(html)
    soup = BeautifulSoup(html, "lxml")

    local_ranges: list[SeasonRange] = []
    no_hunting: list[NoHuntingMarker] = []
    specials: list[SpecialDayRule] = []

    # Strategy:
    # 1) Find “accordion-ish” sections and treat headings as area context.
    # 2) Inside each section, parse tables rows (species + rule text).
    #
    # If the site changes, this is the least-bad generic approach:
    # - area headings: h2/h3/h4 or elements with class containing "accordion" / "title"
    # - tables inside those blocks

    # Collect candidate area blocks:
    headings = soup.find_all(["h2", "h3", "h4"])
    for h in headings:
        area = " ".join(h.get_text(" ", strip=True).split())
        if not area:
            continue

        # Heuristic: area headings often contain “Øen”, “Region”, “Kommune” etc.
        alow = area.lower()
        if not (alow.startswith("øen") or "region" in alow or "kommune" in alow or "på " in alow):
            continue

        # Look for the next table after heading
        table = h.find_next("table")
        if not table:
            continue

        for tr in table.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) < 2:
                continue

            species = " ".join(tds[0].get_text(" ", strip=True).split())
            rule_text = " ".join(tds[1].get_text(" ", strip=True).split())

            if not species or not rule_text:
                continue

            # skip table headers
            if species.lower() in ("vildtart", "art", "vildt"):
                continue

            low = rule_text.lower()

            if "ingen jagttid" in low:
                no_hunting.append(NoHuntingMarker(species=species, area=area))
                continue

            # Numeric date ranges
            found_any_range = False
            for (d1, m1, d2, m2) in RANGE_RE.findall(rule_text.replace("–", "-")):
                start = season_date(season_year, int(d1), int(m1))
                end = season_date(season_year, int(d2), int(m2))
                if start and end:
                    found_any_range = True
                    local_ranges.append(SeasonRange(species=species, start=start, end_inclusive=end, kind="lokal", area=area))

            if found_any_range:
                continue

            # Special saturday rules (your “snyder”)
            special_dates = parse_special_text_to_dates(rule_text, season_year)
            if special_dates:
                specials.append(SpecialDayRule(species=species, area=area, dates=special_dates))

    # Dedup
    def dedup_list(items):
        out, seen = [], set()
        for x in items:
            if x not in seen:
                seen.add(x)
                out.append(x)
        return out

    local_ranges = dedup_list(local_ranges)
    no_hunting = dedup_list(no_hunting)
    specials = dedup_list(specials)

    return local_ranges, no_hunting, specials

# -----------------------
# Filtering
# -----------------------
def contains_any(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any((k or "").strip().lower() in t for k in keywords if k and k.strip())

def area_allowed(area: str | None, include_kw: list[str], exclude_kw: list[str]) -> bool:
    a = area or ""
    if exclude_kw and contains_any(a, exclude_kw):
        return False
    if include_kw:
        return contains_any(a, include_kw)
    return True

# -----------------------
# Build calendars
# -----------------------
def normalize_species(s: str) -> str:
    return " ".join((s or "").strip().lower().split())

def main() -> None:
    master = load_master()
    general_url = master.get("general_url", DEFAULT_GENERAL_URL)
    local_url = master.get("local_url", DEFAULT_LOCAL_URL)
    ua = master.get("user_agent", DEFAULT_USER_AGENT)

    seasons_ahead_default = int(master.get("seasons_ahead", 2))  # IMPORTANT: heltal
    local_map_image_url = master.get("local_map_image_url", "")

    species_meta = master.get("species_meta", {})  # {"ræv": {"image_url": "...", "notes": "..."}}

    calendar_cfgs = list_calendar_configs()
    if not calendar_cfgs:
        raise RuntimeError("Ingen configs fundet i configs/calendars/*.json")

    # Pre-fetch HTML once per run
    general_html = fetch_html(general_url, ua)
    local_html = fetch_html(local_url, ua)

    for cfg in calendar_cfgs:
        cal_name = cfg["calendar_name"]
        out_name = cfg["output_filename"]
        use_local = bool(cfg.get("use_local", False))

        filters = cfg.get("filters", {})
        include_area = filters.get("include_area_keywords", [])
        exclude_area = filters.get("exclude_area_keywords", [])

        local_rules = cfg.get("local_rules", {})
        emit_no_hunting = bool(local_rules.get("emit_no_hunting_events", False))

        seasons_ahead = int(cfg.get("seasons_ahead", seasons_ahead_default))

        events: list[str] = []
        uid_counter = 0

        # For each season year, generate events
        base_season_year = compute_season_year_auto()

        for i in range(seasons_ahead):
            season_year = base_season_year + i

            general_ranges = parse_general(general_html, season_year)

            # Index general by species for no-hunting matching
            general_by_species: dict[str, list[SeasonRange]] = {}
            for gr in general_ranges:
                general_by_species.setdefault(normalize_species(gr.species), []).append(gr)

            # Always include general events in ALL calendars?
            # Din plan: generel-kalender = kun generel.
            # Lokal-kalendere = (1) generel + (2) lokale overrides + (3) “ingen jagttid” markers
            #
            # Her gør vi:
            # - generel calendar (use_local=False) => kun generel
            # - lokal calendar (use_local=True) => generel + lokal + ingen-jagttid + special-days

            # Add general events (filtered by area? nej, general har ingen area)
            if not use_local:
                for r in general_ranges:
                    uid_counter += 1
                    meta = species_meta.get(normalize_species(r.species), {})
                    img = meta.get("image_url", "")
                    notes = meta.get("notes", "")

                    desc = f"Kilde: {general_url}"
                    if notes:
                        desc = f"{notes}\n{desc}"
                    if img:
                        desc = f"{desc}\nBillede: {img}"

                    uid = f"jagttid-{season_year}-gen-{uid_counter}@luka2945"
                    events.append(build_event(
                        uid=uid,
                        summary=f"{r.species} (Generel)",
                        start=r.start,
                        end_inclusive=r.end_inclusive,
                        description=desc,
                        url=general_url
                    ))
            else:
                # Local mode: parse local too
                local_ranges, no_hunting, specials = parse_local(local_html, season_year)

                # 1) General base
                for r in general_ranges:
                    uid_counter += 1
                    meta = species_meta.get(normalize_species(r.species), {})
                    img = meta.get("image_url", "")
                    notes = meta.get("notes", "")

                    desc = f"Kilde: {general_url}"
                    if notes:
                        desc = f"{notes}\n{desc}"
                    if img:
                        desc = f"{desc}\nBillede: {img}"
                    if local_map_image_url:
                        desc = f"{desc}\nRegionskort: {local_map_image_url}"

                    uid = f"jagttid-{season_year}-base-{uid_counter}@luka2945"
                    events.append(build_event(
                        uid=uid,
                        summary=f"{r.species} (Generel)",
                        start=r.start,
                        end_inclusive=r.end_inclusive,
                        description=desc,
                        url=general_url
                    ))

                # 2) Local ranges (only those matching include/exclude area keywords)
                for r in local_ranges:
                    if not area_allowed(r.area, include_area, exclude_area):
                        continue

                    uid_counter += 1
                    meta = species_meta.get(normalize_species(r.species), {})
                    img = meta.get("image_url", "")
                    notes = meta.get("notes", "")

                    desc = f"Område: {r.area}\nKilde: {local_url}"
                    if notes:
                        desc = f"{notes}\n{desc}"
                    if img:
                        desc = f"{desc}\nBillede: {img}"
                    if local_map_image_url:
                        desc = f"{desc}\nRegionskort: {local_map_image_url}"

                    uid = f"jagttid-{season_year}-lok-{uid_counter}@luka2945"
                    events.append(build_event(
                        uid=uid,
                        summary=f"{r.species} (Lokalt)",
                        start=r.start,
                        end_inclusive=r.end_inclusive,
                        description=desc,
                        url=local_url
                    ))

                # 3) Special one-day rules (lørdage) (also filter on area)
                for sp in specials:
                    if not area_allowed(sp.area, include_area, exclude_area):
                        continue
                    for d in sp.dates:
                        uid_counter += 1
                        meta = species_meta.get(normalize_species(sp.species), {})
                        img = meta.get("image_url", "")
                        notes = meta.get("notes", "")

                        desc = f"Område: {sp.area}\nKilde: {local_url}"
                        if notes:
                            desc = f"{notes}\n{desc}"
                        if img:
                            desc = f"{desc}\nBillede: {img}"
                        if local_map_image_url:
                            desc = f"{desc}\nRegionskort: {local_map_image_url}"

                        uid = f"jagttid-{season_year}-spec-{uid_counter}@luka2945"
                        events.append(build_event(
                            uid=uid,
                            summary=f"{sp.species} (Lokalt – særlige dage)",
                            start=d,
                            end_inclusive=d,
                            description=desc,
                            url=local_url
                        ))

                # 4) Emit “INGEN jagttid” events (same duration as general)
                if emit_no_hunting:
                    for nh in no_hunting:
                        if not area_allowed(nh.area, include_area, exclude_area):
                            continue

                        general_list = general_by_species.get(normalize_species(nh.species), [])
                        if not general_list:
                            # no general season found => cannot match duration; skip
                            continue

                        for gr in general_list:
                            uid_counter += 1
                            meta = species_meta.get(normalize_species(nh.species), {})
                            img = meta.get("image_url", "")
                            notes = meta.get("notes", "")

                            desc = (
                                f"Område: {nh.area}\n"
                                f"Dette område har 'ingen jagttid' for arten i perioden,\n"
                                f"og perioden er sat til samme varighed som den generelle jagttid.\n"
                                f"Kilde (lokal): {local_url}\n"
                                f"Kilde (generel): {general_url}"
                            )
                            if notes:
                                desc = f"{notes}\n{desc}"
                            if img:
                                desc = f"{desc}\nBillede: {img}"
                            if local_map_image_url:
                                desc = f"{desc}\nRegionskort: {local_map_image_url}"

                            uid = f"jagttid-{season_year}-nohunt-{uid_counter}@luka2945"
                            events.append(build_event(
                                uid=uid,
                                summary=f"INGEN jagttid: {nh.species} (Lokalt)",
                                start=gr.start,
                                end_inclusive=gr.end_inclusive,
                                description=desc,
                                url=local_url
                            ))

        ics = build_calendar(cal_name, events)
        out_path = OUT_DIR / out_name
        out_path.write_text(ics, encoding="utf-8")
        print(f"Wrote: {out_path}  events={len(events)}")

if __name__ == "__main__":
    main()
