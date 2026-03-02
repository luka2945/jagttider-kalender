import json
import re
import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup


# ----------------------------
# Helpers
# ----------------------------

def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def save_text(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")

def ics_escape(text: str) -> str:
    return (text.replace("\\", "\\\\")
                .replace("\n", "\\n")
                .replace(",", "\\,")
                .replace(";", "\\;"))

def ymd(d: date) -> str:
    return d.strftime("%Y%m%d")

def uid_hash(*parts: str) -> str:
    raw = "|".join(parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

def looks_like_blocked(html: str) -> bool:
    low = html.lower()
    # typiske tegn på at du får login/cookie/andet i stedet for jagttider
    bad = ["logge ind", "cookie", "consent", "access denied", "forbidden"]
    return any(b in low for b in bad) and ("jagttid" not in low)

def fetch_html(url: str, debug_dir: Path, debug_name: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/123 Safari/537.36",
        "Accept-Language": "da-DK,da;q=0.9,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    r = requests.get(url, headers=headers, timeout=30)
    r.raise_for_status()
    html = r.text

    # gem altid det runneren fik (så du kan debugge)
    save_text(debug_dir / f"{debug_name}.html", html)

    if looks_like_blocked(html):
        raise RuntimeError(
            f"Forkert/afvist HTML fra {url}. "
            f"Se debug-html/{debug_name}.html i repo for præcis indhold."
        )

    return html


DATE_RANGE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\s*[-–]\s*(\d{1,2})\.(\d{1,2})")

def parse_date_range(text: str) -> Optional[Tuple[Tuple[int,int], Tuple[int,int]]]:
    m = DATE_RANGE_RE.search(text.replace(" ", ""))
    if not m:
        return None
    sday, smon, eday, emon = map(int, m.groups())
    return (sday, smon), (eday, emon)

def season_dates(start_year: int, start_dm: Tuple[int,int], end_dm: Tuple[int,int]) -> Tuple[date, date]:
    """
    Return start_date, end_date_inclusive.
    Handles seasons that cross New Year automatically.
    """
    sday, smon = start_dm
    eday, emon = end_dm
    start = date(start_year, smon, sday)
    end = date(start_year, emon, eday)
    if end < start:
        end = date(start_year + 1, emon, eday)
    return start, end

def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()

def norm_key(s: str) -> str:
    return norm(s).lower()

def species_all_names(species: str, species_meta: dict) -> List[str]:
    names = [species]
    meta = species_meta.get(species, {})
    for a in meta.get("aliases", []):
        if a and a not in names:
            names.append(a)
    return names


# ----------------------------
# Data models
# ----------------------------

@dataclass
class GeneralRow:
    species: str
    start_dm: Tuple[int,int]
    end_dm: Tuple[int,int]

@dataclass
class LocalRow:
    area: str
    species: str
    raw_period: str
    start_dm: Optional[Tuple[int,int]]
    end_dm: Optional[Tuple[int,int]]
    is_no_hunting: bool
    special_text: Optional[str]


# ----------------------------
# Parsing
# ----------------------------

def parse_general(html: str) -> Dict[str, GeneralRow]:
    """
    Very robust-ish approach:
    Find text nodes that contain a date-range, then take the preceding text in the same parent as species.
    """
    soup = BeautifulSoup(html, "html.parser")
    general: Dict[str, GeneralRow] = {}

    candidates = soup.find_all(string=lambda t: t and DATE_RANGE_RE.search(str(t)) is not None)
    for t in candidates:
        parent = t.parent
        if not parent:
            continue

        row_text = norm(parent.get_text(" ", strip=True))
        rng = parse_date_range(row_text)
        if not rng:
            continue

        # species = text before first date-range in that row_text
        before = re.split(r"\d{1,2}\.\d{1,2}\s*[-–]\s*\d{1,2}\.\d{1,2}", row_text, maxsplit=1)[0]
        species = norm(before)
        if len(species) < 2:
            continue

        (sday, smon), (eday, emon) = rng
        general[species] = GeneralRow(species=species, start_dm=(sday, smon), end_dm=(eday, emon))

    return general


def parse_local(html: str) -> List[LocalRow]:
    soup = BeautifulSoup(html, "html.parser")
    rows: List[LocalRow] = []

    for tr in soup.find_all("tr"):
        cols = [norm(td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])]
        if len(cols) < 2:
            continue

        species = norm(cols[0])
        period = norm(cols[1])
        if not species or not period:
            continue

        # find nearest previous heading for area
        area = "Lokalt"
        prev = tr.find_previous(["h2", "h3", "h4"])
        if prev:
            area = norm(prev.get_text(" ", strip=True)).replace(" - lokale jagttider", "").strip()

        is_no = "ingen jagttid" in period.lower()
        rng = parse_date_range(period)

        start_dm = end_dm = None
        special_text = None

        if rng:
            start_dm, end_dm = rng
        else:
            # fx "1. og 2. lørdag i november"
            special_text = period

        rows.append(LocalRow(
            area=area,
            species=species,
            raw_period=period,
            start_dm=start_dm,
            end_dm=end_dm,
            is_no_hunting=is_no,
            special_text=special_text
        ))

    return rows


# ----------------------------
# Matching & filters
# ----------------------------

def keyword_match_any(text: str, keywords: List[str]) -> bool:
    t = text.lower()
    return any(k.lower() in t for k in keywords)

def should_include(cfg: dict, species: str, area: str) -> bool:
    if cfg.get("exclude_species_keywords") and keyword_match_any(species, cfg["exclude_species_keywords"]):
        return False
    if cfg.get("exclude_area_keywords") and keyword_match_any(area, cfg["exclude_area_keywords"]):
        return False

    include_area = cfg.get("include_area_keywords", [])
    if include_area:
        return keyword_match_any(area, include_area)

    return True

def build_general_lookup(general: Dict[str, GeneralRow], species_meta: dict) -> Dict[str, GeneralRow]:
    """
    Build lookup so both the canonical name and aliases can find the same GeneralRow.
    """
    lookup: Dict[str, GeneralRow] = {}

    # canonical names
    for sp, row in general.items():
        lookup[norm_key(sp)] = row

    # aliases
    for sp, meta in species_meta.items():
        row = general.get(sp)
        if not row:
            # maybe general uses a different spelling; try to find by key match
            row = general.get(sp) or lookup.get(norm_key(sp))
        if not row:
            continue
        for a in meta.get("aliases", []):
            lookup[norm_key(a)] = row

    return lookup


# ----------------------------
# ICS
# ----------------------------

def event_all_day(summary: str, description: str, start: date, end_inclusive: date, uid: str) -> str:
    dtend_excl = end_inclusive + timedelta(days=1)
    return "\n".join([
        "BEGIN:VEVENT",
        f"UID:{ics_escape(uid)}",
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{ics_escape(summary)}",
        f"DTSTART;VALUE=DATE:{ymd(start)}",
        f"DTEND;VALUE=DATE:{ymd(dtend_excl)}",
        f"DESCRIPTION:{ics_escape(description)}",
        "END:VEVENT",
    ])

def calendar_ics(cal_name: str, prodid: str, events: List[str]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{prodid}",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{ics_escape(cal_name)}",
        *events,
        "END:VCALENDAR",
        ""
    ]
    return "\n".join(lines)


# ----------------------------
# Main
# ----------------------------

def merge_cfg(master: dict, cal: dict) -> dict:
    cfg = {}
    cfg.update(master.get("defaults", {}))
    cfg.update(cal)
    cfg["sources"] = master["sources"]
    cfg["output_dir"] = master.get("output_dir", "Jagttids-Kalender")
    cfg["debug_html_dir"] = master.get("debug_html_dir", "debug-html")
    cfg["season_start_years"] = master.get("season_start_years", [])
    cfg["species_meta"] = master.get("species_meta", {})
    cfg["prodid"] = master.get("prodid", "-//luka2945//Jagttider ICS//DA")
    return cfg

def species_shooting_time(species: str, species_meta: dict) -> str:
    # find exact or alias match
    if species in species_meta and species_meta[species].get("shooting_time"):
        return species_meta[species]["shooting_time"]

    # alias match
    s_key = norm_key(species)
    for sp, meta in species_meta.items():
        if s_key == norm_key(sp):
            return meta.get("shooting_time", "")
        for a in meta.get("aliases", []):
            if s_key == norm_key(a):
                return meta.get("shooting_time", "")
    return ""

def main():
    root = Path(__file__).resolve().parent

    master = load_json(root / "configs" / "master.json")
    calendars_dir = root / "configs" / "calendars"

    output_dir = root / master.get("output_dir", "Jagttids-Kalender")
    debug_dir = root / master.get("debug_html_dir", "debug-html")
    output_dir.mkdir(parents=True, exist_ok=True)
    debug_dir.mkdir(parents=True, exist_ok=True)

    general_html = fetch_html(master["sources"]["general_url"], debug_dir, "general")
    local_html = fetch_html(master["sources"]["local_url"], debug_dir, "local")

    general = parse_general(general_html)
    local_rows = parse_local(local_html)

    if len(general) < 5:
        raise RuntimeError(
            f"Parsed general species is too low ({len(general)}). "
            f"Se debug-html/general.html"
        )

    species_meta = master.get("species_meta", {})
    general_lookup = build_general_lookup(general, species_meta)

    cal_files = sorted([p for p in calendars_dir.glob("*.json") if p.name != "master.json"])
    if not cal_files:
        raise RuntimeError("Ingen kalender-configs fundet i configs/calendars/")

    season_years = master.get("season_start_years", [])
    if not season_years:
        # hvis du glemmer at sætte dem, så laver vi 3 sæsoner som fallback
        y = date.today().year
        season_years = [y, y + 1, y + 2]

    for cal_path in cal_files:
        cal_raw = load_json(cal_path)
        cfg = merge_cfg(master, cal_raw)

        cal_id = cfg["id"]
        cal_name = cfg.get("calendar_name", cal_id)
        include_local = bool(cfg.get("include_local", False))

        events: List[str] = []
        source_general = master["sources"]["general_url"]
        source_local = master["sources"]["local_url"]

        region_map_url = cfg.get("region_map_image_url", "")
        emit_no = bool(cfg.get("emit_no_hunting_events", True))

        # ----------------
        # GENEREL KALENDER
        # ----------------
        if not include_local:
            for sp, row in general.items():
                if cfg.get("exclude_species_keywords") and keyword_match_any(sp, cfg["exclude_species_keywords"]):
                    continue

                for sy in season_years:
                    start, end_inc = season_dates(sy, row.start_dm, row.end_dm)

                    shoot = species_shooting_time(sp, species_meta)
                    desc_lines = []
                    if shoot:
                        desc_lines.append(shoot)
                    desc_lines.append(f"Periode: {start.strftime('%d.%m')} til {end_inc.strftime('%d.%m')}")
                    desc_lines.append("")
                    desc_lines.append(f"{cfg.get('source_label','Kilde')}: {source_general}")

                    uid = f"gen-{uid_hash(cal_id, sp, str(sy), 'general')}"
                    events.append(event_all_day(
                        summary=f"{sp} – Generel jagttid",
                        description="\n".join(desc_lines),
                        start=start,
                        end_inclusive=end_inc,
                        uid=uid
                    ))

        # ---------------
        # LOKALE KALENDRE
        # ---------------
        if include_local:
            for lr in local_rows:
                if not should_include(cfg, lr.species, lr.area):
                    continue

                # 1) Ingen jagttid -> event med varighed som generel for arten
                if lr.is_no_hunting:
                    if not emit_no:
                        continue

                    gen = general_lookup.get(norm_key(lr.species))
                    if not gen:
                        continue

                    for sy in season_years:
                        start, end_inc = season_dates(sy, gen.start_dm, gen.end_dm)

                        desc_lines = [
                            "Ingen jagttid (lokal fredning/undtagelse).",
                            f"Område: {lr.area}",
                            f"Periode: {start.strftime('%d.%m')} til {end_inc.strftime('%d.%m')}",
                        ]
                        if region_map_url:
                            desc_lines.append("")
                            desc_lines.append(f"Regionkort: {region_map_url}")

                        desc_lines.append("")
                        desc_lines.append(f"{cfg.get('source_label','Kilde')}: {source_local}")

                        uid = f"no-{uid_hash(cal_id, lr.species, lr.area, str(sy))}"
                        events.append(event_all_day(
                            summary=f"{lr.species} – Ingen jagttid ({lr.area})",
                            description="\n".join(desc_lines),
                            start=start,
                            end_inclusive=end_inc,
                            uid=uid
                        ))
                    continue

                # 2) Normal lokal dato-range
                if lr.start_dm and lr.end_dm:
                    for sy in season_years:
                        start, end_inc = season_dates(sy, lr.start_dm, lr.end_dm)

                        shoot = species_shooting_time(lr.species, species_meta)
                        desc_lines = []
                        if shoot:
                            desc_lines.append(shoot)
                        desc_lines.append(f"Område: {lr.area}")
                        desc_lines.append(f"Periode: {start.strftime('%d.%m')} til {end_inc.strftime('%d.%m')}")

                        if region_map_url:
                            desc_lines.append("")
                            desc_lines.append(f"Regionkort: {region_map_url}")

                        desc_lines.append("")
                        desc_lines.append(f"{cfg.get('source_label','Kilde')}: {source_local}")

                        uid = f"loc-{uid_hash(cal_id, lr.species, lr.area, str(sy), 'range')}"
                        events.append(event_all_day(
                            summary=f"{lr.species} – Lokal jagttid ({lr.area})",
                            description="\n".join(desc_lines),
                            start=start,
                            end_inclusive=end_inc,
                            uid=uid
                        ))
                    continue

                # 3) Special tekst (lørdage osv.) -> note-event i den generelle sæson
                gen = general_lookup.get(norm_key(lr.species))
                if not gen:
                    continue

                for sy in season_years:
                    start, end_inc = season_dates(sy, gen.start_dm, gen.end_dm)

                    shoot = species_shooting_time(lr.species, species_meta)
                    desc_lines = []
                    if shoot:
                        desc_lines.append(shoot)
                    desc_lines.append(f"Område: {lr.area}")
                    desc_lines.append(f"Lokal regel: {lr.special_text or lr.raw_period}")
                    desc_lines.append(f"Periode (ramme): {start.strftime('%d.%m')} til {end_inc.strftime('%d.%m')}")

                    if region_map_url:
                        desc_lines.append("")
                        desc_lines.append(f"Regionkort: {region_map_url}")

                    desc_lines.append("")
                    desc_lines.append(f"{cfg.get('source_label','Kilde')}: {source_local}")

                    uid = f"rule-{uid_hash(cal_id, lr.species, lr.area, str(sy), 'special')}"
                    events.append(event_all_day(
                        summary=f"{lr.species} – Lokal regel ({lr.area})",
                        description="\n".join(desc_lines),
                        start=start,
                        end_inclusive=end_inc,
                        uid=uid
                    ))

        ics = calendar_ics(cal_name, cfg["prodid"], events)
        out_path = output_dir / f"{cal_id}.ics"
        save_text(out_path, ics)
        print(f"Wrote {out_path} ({len(events)} events)")

if __name__ == "__main__":
    main()
