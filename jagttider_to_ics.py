from __future__ import annotations

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
# Defaults
# -----------------------
DEFAULT_GENERAL_URL = "https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/"
DEFAULT_LOCAL_URL = "https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/lokale-jagttider/"
DEFAULT_USER_AGENT = "Mozilla/5.0 (JagttiderICSBot; +https://github.com/luka2945/jagttider-kalender)"
DEFAULT_LANG = "da-DK,da;q=0.9,en-US;q=0.7,en;q=0.6"

# -----------------------
# Danish months
# -----------------------
DK_MONTHS = {
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

RANGE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\s*[-–]\s*(\d{1,2})\.(\d{1,2})")

NTH_SAT_RE = re.compile(
    r"(\d)\.\s*og\s*(\d)\.\s*lørdag i ([a-zæøå]+)",
    flags=re.I,
)

ALL_SAT_RE = re.compile(
    r"alle\s+lørdage?\s+i\s+([a-zæøå]+)",
    flags=re.I,
)

# -----------------------
# Data models
# -----------------------
@dataclass(frozen=True)
class SeasonRange:
    species: str
    start: date
    end_inclusive: date
    kind: str
    area: str | None = None


@dataclass(frozen=True)
class NoHuntingMarker:
    species: str
    area: str


@dataclass(frozen=True)
class SpecialDayRule:
    species: str
    area: str
    dates: tuple[date, ...]


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
    t = today or date.today()
    return t.year if t.month >= 7 else (t.year - 1)


# -----------------------
# Date helpers
# -----------------------
def season_range_dates(season_year: int, d1: int, m1: int, d2: int, m2: int) -> tuple[date | None, date | None]:
    """
    season_year = året hvor sæsonen starter.

    Regler:
    - Hvis startmåned er Jul-Dec, ligger start i season_year
    - Hvis startmåned er Jan-Jun, ligger start i season_year + 1
    - Hvis slutdato ligger før startdato, går perioden over nytår
    """
    start_year = season_year if m1 >= 7 else (season_year + 1)
    end_year = start_year

    if (m2, d2) < (m1, d1):
        end_year += 1

    try:
        start = date(start_year, m1, d1)
        end = date(end_year, m2, d2)
        return start, end
    except ValueError:
        return None, None


# -----------------------
# ICS helpers
# -----------------------
def ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def ics_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def build_event(
    uid: str,
    summary: str,
    start: date,
    end_inclusive: date,
    description: str,
    url: str | None = None,
) -> str:
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
# Fetch
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
    low = html.lower()
    if "du skal logge ind" in low or ("logge ind" in low and "jagttider" in low and "<table" not in low):
        raise RuntimeError("Fik en 'log ind' side i stedet for jagttider-tabellen.")


# -----------------------
# Species helpers
# -----------------------
def normalize_species(s: str) -> str:
    return " ".join((s or "").strip().lower().split())


def clean_species_name(s: str) -> str:
    s = " ".join((s or "").strip().split())
    s = re.sub(r"\s*\*+\s*$", "", s).strip()
    return s


def get_species_meta(species_name: str, species_meta: dict) -> dict:
    key = normalize_species(species_name)

    if key in species_meta:
        return species_meta[key]

    for meta_key, meta_val in species_meta.items():
        mk = normalize_species(meta_key)
        if mk and mk in key:
            return meta_val

    return {}


# -----------------------
# Area helpers
# -----------------------
def split_area(area: str | None) -> tuple[str, str | None]:
    if not area:
        return "", None
    if " | " in area:
        region, sub = area.split(" | ", 1)
        return region.strip(), sub.strip()
    return area.strip(), None


def display_area(area: str | None) -> str:
    region, sub = split_area(area)
    if sub:
        return f"{sub} {region}".strip()
    return region


# -----------------------
# HTML -> text lines
# -----------------------
def html_to_lines(html: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n")
    lines = []
    for raw in text.splitlines():
        s = " ".join(raw.replace("\xa0", " ").split()).strip()
        if s:
            lines.append(s)
    return lines


# -----------------------
# Parsing helpers
# -----------------------
def split_species_and_rule(line: str) -> tuple[str | None, str | None]:
    txt = line.strip()
    if not txt:
        return None, None

    m = RANGE_RE.search(txt.replace("–", "-"))
    if m:
        species = clean_species_name(txt[:m.start()].strip())
        rule = txt[m.start():].strip()
        return (species or None), (rule or None)

    low = txt.lower()
    idx = low.find("ingen jagttid")
    if idx != -1:
        species = clean_species_name(txt[:idx].strip())
        rule = txt[idx:].strip()
        return (species or None), (rule or None)

    if "lørdag" in low:
        m_num = re.search(r"\d\.", txt)
        idx = m_num.start() if m_num else low.find("alle")
        if idx != -1:
            species = clean_species_name(txt[:idx].strip())
            rule = txt[idx:].strip()
            return (species or None), (rule or None)

    return None, None


def is_region_heading(line: str) -> bool:
    low = line.lower()
    return "region" in low and "lokale jagttider" in low


def is_subarea_heading(line: str) -> bool:
    low = line.lower()
    return (
        low.startswith("øen ")
        or (low.startswith("region ") and "undtagen" in low)
        or "hele regionen" in low
    )


# -----------------------
# Parsing: GENERAL
# -----------------------
def parse_general(html: str, season_year: int) -> list[SeasonRange]:
    ensure_not_login_page(html)
    soup = BeautifulSoup(html, "lxml")

    ranges: list[SeasonRange] = []

    for table in soup.find_all("table"):
        for tr in table.find_all("tr"):
            tds = tr.find_all(["td", "th"])
            if len(tds) < 2:
                continue

            species = clean_species_name(" ".join(tds[0].get_text(" ", strip=True).split()))
            period_text = " ".join(tds[1].get_text(" ", strip=True).split())

            if not species or not period_text:
                continue

            if species.lower() in ("vildtart", "art", "vildt"):
                continue

            if "ingen jagttid" in period_text.lower():
                continue

            for (d1, m1, d2, m2) in RANGE_RE.findall(period_text.replace("–", "-")):
                start, end = season_range_dates(season_year, int(d1), int(m1), int(d2), int(m2))
                if start and end:
                    ranges.append(
                        SeasonRange(
                            species=species,
                            start=start,
                            end_inclusive=end,
                            kind="generel"
                        )
                    )

    uniq = []
    seen = set()
    for r in ranges:
        key = (r.species.lower(), r.start, r.end_inclusive, r.kind, r.area)
        if key not in seen:
            seen.add(key)
            uniq.append(r)
    return uniq


# -----------------------
# Parsing: LOCAL
# -----------------------
def nth_saturday(year: int, month: int, n: int) -> date | None:
    c = calmod.Calendar(firstweekday=calmod.MONDAY)
    sats = [d for d in c.itermonthdates(year, month) if d.month == month and d.weekday() == 5]
    return sats[n - 1] if 1 <= n <= len(sats) else None


def all_saturdays(year: int, month: int) -> list[date]:
    c = calmod.Calendar(firstweekday=calmod.MONDAY)
    return [d for d in c.itermonthdates(year, month) if d.month == month and d.weekday() == 5]


def parse_special_text_to_dates(text: str, season_year: int) -> list[date]:
    low = text.strip().lower()
    dates: list[date] = []

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
        if d_a:
            dates.append(d_a)
        if d_b:
            dates.append(d_b)

    for m in ALL_SAT_RE.finditer(low):
        month_name = m.group(1).lower()
        if month_name not in DK_MONTHS:
            continue
        month = DK_MONTHS[month_name]
        year = season_year if month >= 7 else (season_year + 1)
        dates.extend(all_saturdays(year, month))

    return sorted(set(dates))


def parse_local(html: str, season_year: int) -> tuple[list[SeasonRange], list[NoHuntingMarker], list[SpecialDayRule]]:
    ensure_not_login_page(html)
    lines = html_to_lines(html)

    local_ranges: list[SeasonRange] = []
    no_hunting: list[NoHuntingMarker] = []
    specials: list[SpecialDayRule] = []

    current_region = ""
    current_subarea = ""
    pending_species = ""

    for line in lines:
        txt = line.strip()
        low = txt.lower()

        if not txt:
            continue

        if is_region_heading(txt):
            current_region = re.sub(r"\s*-\s*lokale jagttider\s*$", "", txt, flags=re.I).strip()
            current_subarea = current_region
            pending_species = ""
            continue

        if not current_region:
            continue

        if is_subarea_heading(txt):
            if "hele regionen" in low:
                current_subarea = current_region
            else:
                current_subarea = txt
            pending_species = ""
            continue

        species_only, rule_only = split_species_and_rule(txt)
        if species_only and rule_only:
            species = species_only
            rule_text = rule_only
        else:
            if not RANGE_RE.search(txt.replace("–", "-")) and "ingen jagttid" not in low and "lørdag" not in low:
                pending_species = clean_species_name(txt)
                continue

            if pending_species:
                species = pending_species
                rule_text = txt
                pending_species = ""
            else:
                continue

        if not species or not rule_text:
            continue

        if species.lower() in ("vildtart", "art", "vildt"):
            continue

        if current_subarea == current_region:
            area = current_region
        else:
            area = f"{current_region} | {current_subarea}"

        rule_low = rule_text.lower()

        if "ingen jagttid" in rule_low:
            no_hunting.append(NoHuntingMarker(species=species, area=area))
            continue

        found_any_range = False
        for (d1, m1, d2, m2) in RANGE_RE.findall(rule_text.replace("–", "-")):
            start, end = season_range_dates(season_year, int(d1), int(m1), int(d2), int(m2))
            if start and end:
                found_any_range = True
                local_ranges.append(
                    SeasonRange(
                        species=species,
                        start=start,
                        end_inclusive=end,
                        kind="lokal",
                        area=area
                    )
                )

        if found_any_range:
            continue

        special_dates = parse_special_text_to_dates(rule_text, season_year)
        if special_dates:
            specials.append(
                SpecialDayRule(
                    species=species,
                    area=area,
                    dates=tuple(special_dates)
                )
            )

    uniq_local = []
    seen_local = set()
    for r in local_ranges:
        key = (r.species.lower(), r.start, r.end_inclusive, r.kind, r.area)
        if key not in seen_local:
            seen_local.add(key)
            uniq_local.append(r)

    uniq_no = []
    seen_no = set()
    for r in no_hunting:
        key = (r.species.lower(), r.area.lower())
        if key not in seen_no:
            seen_no.add(key)
            uniq_no.append(r)

    uniq_sp = []
    seen_sp = set()
    for r in specials:
        key = (r.species.lower(), r.area.lower(), tuple(r.dates))
        if key not in seen_sp:
            seen_sp.add(key)
            uniq_sp.append(r)

    print(f"LOCAL DEBUG season {season_year}: ranges={len(uniq_local)} no_hunting={len(uniq_no)} specials={len(uniq_sp)}")

    return uniq_local, uniq_no, uniq_sp


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
# Main
# -----------------------
def main() -> None:
    master = load_master()
    general_url = master.get("general_url", DEFAULT_GENERAL_URL)
    local_url = master.get("local_url", DEFAULT_LOCAL_URL)
    ua = master.get("user_agent", DEFAULT_USER_AGENT)

    seasons_ahead_default = int(master.get("seasons_ahead", 2))
    local_map_image_url = master.get("local_map_image_url", "")
    species_meta = master.get("species_meta", {})

    calendar_cfgs = list_calendar_configs()
    if not calendar_cfgs:
        raise RuntimeError("Ingen configs fundet i configs/calendars/*.json")

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
        base_season_year = compute_season_year_auto()

        for i in range(seasons_ahead):
            season_year = base_season_year + i

            general_ranges = parse_general(general_html, season_year)
            local_ranges, no_hunting, specials = parse_local(local_html, season_year)

            general_by_species: dict[str, list[SeasonRange]] = {}
            for gr in general_ranges:
                general_by_species.setdefault(normalize_species(gr.species), []).append(gr)

            if not use_local:
                for r in general_ranges:
                    uid_counter += 1
                    meta = get_species_meta(r.species, species_meta)
                    img = meta.get("image_url", "")
                    notes = meta.get("notes", "")

                    desc_parts = []
                    if notes:
                        desc_parts.append(notes)
                    desc_parts.append(f"Kilde: {general_url}")
                    if img:
                        desc_parts.append(f"Billede: {img}")

                    uid = f"jagttid-{season_year}-gen-{uid_counter}@luka2945"
                    events.append(build_event(
                        uid=uid,
                        summary=f"{r.species} - Jagttid",
                        start=r.start,
                        end_inclusive=r.end_inclusive,
                        description="\n".join(desc_parts),
                        url=general_url
                    ))

            else:
                for r in local_ranges:
                    if not area_allowed(r.area, include_area, exclude_area):
                        continue

                    uid_counter += 1
                    meta = get_species_meta(r.species, species_meta)
                    img = meta.get("image_url", "")
                    notes = meta.get("notes", "")

                    desc_parts = []
                    if notes:
                        desc_parts.append(notes)
                    if r.area:
                        desc_parts.append(f"Område: {display_area(r.area)}")
                    desc_parts.append(f"Kilde: {local_url}")
                    if img:
                        desc_parts.append(f"Billede: {img}")
                    if local_map_image_url:
                        desc_parts.append(f"Regionskort: {local_map_image_url}")

                    uid = f"jagttid-{season_year}-lok-{uid_counter}@luka2945"
                    events.append(build_event(
                        uid=uid,
                        summary=f"{r.species} - Lokal jagttid",
                        start=r.start,
                        end_inclusive=r.end_inclusive,
                        description="\n".join(desc_parts),
                        url=local_url
                    ))

                for sp in specials:
                    if not area_allowed(sp.area, include_area, exclude_area):
                        continue

                    for d in sp.dates:
                        uid_counter += 1
                        meta = get_species_meta(sp.species, species_meta)
                        img = meta.get("image_url", "")
                        notes = meta.get("notes", "")

                        desc_parts = []
                        if notes:
                            desc_parts.append(notes)
                        desc_parts.append(f"Område: {display_area(sp.area)}")
                        desc_parts.append(f"Kilde: {local_url}")
                        if img:
                            desc_parts.append(f"Billede: {img}")
                        if local_map_image_url:
                            desc_parts.append(f"Regionskort: {local_map_image_url}")

                        uid = f"jagttid-{season_year}-spec-{uid_counter}@luka2945"
                        events.append(build_event(
                            uid=uid,
                            summary=f"{sp.species} - Lokal jagttid",
                            start=d,
                            end_inclusive=d,
                            description="\n".join(desc_parts),
                            url=local_url
                        ))

                if emit_no_hunting:
                    for nh in no_hunting:
                        if not area_allowed(nh.area, include_area, exclude_area):
                            continue

                        general_list = general_by_species.get(normalize_species(nh.species), [])
                        if not general_list:
                            continue

                        for gr in general_list:
                            uid_counter += 1
                            meta = get_species_meta(nh.species, species_meta)
                            img = meta.get("image_url", "")
                            notes = meta.get("notes", "")

                            desc_parts = []
                            if notes:
                                desc_parts.append(notes)
                            desc_parts.append(f"Område: {display_area(nh.area)}")
                            desc_parts.append("Lokal regel: ingen jagttid")
                            desc_parts.append("Varighed hentet fra generel jagttid for samme dyr")
                            desc_parts.append(f"Kilde (lokal): {local_url}")
                            desc_parts.append(f"Kilde (generel): {general_url}")
                            if img:
                                desc_parts.append(f"Billede: {img}")
                            if local_map_image_url:
                                desc_parts.append(f"Regionskort: {local_map_image_url}")

                            uid = f"jagttid-{season_year}-nohunt-{uid_counter}@luka2945"
                            events.append(build_event(
                                uid=uid,
                                summary=f"{nh.species} - Ingen jagttid",
                                start=gr.start,
                                end_inclusive=gr.end_inclusive,
                                description="\n".join(desc_parts),
                                url=local_url
                            ))

        ics = build_calendar(cal_name, events)
        out_path = OUT_DIR / out_name
        out_path.write_text(ics, encoding="utf-8")
        print(f"Wrote: {out_path}  events={len(events)}")


if __name__ == "__main__":
    main()
