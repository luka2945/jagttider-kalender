import json
import re
from pathlib import Path
from datetime import date, datetime, timedelta
import requests

# =========================
# PATHS
# =========================
MASTER_PATH = Path("configs/master.json")
CALENDAR_DIR = Path("configs/calendars")

# =========================
# FETCH SETTINGS
# =========================
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122 Safari/537.36"
FETCH_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# =========================
# REGEX
# =========================
RANGE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\s*[-–]\s*(\d{1,2})\.(\d{1,2})")
BR_RE = re.compile(r"<br\s*/?>", flags=re.I)

# "1. og 2. lørdag i november"
NTH_SAT_RE = re.compile(
    r"(\d+)\.\s*og\s*(\d+)\.\s*lørdag\s+i\s+([a-zæøå]+)",
    flags=re.I,
)

# "alle lørdag i december"
ALL_SAT_RE = re.compile(
    r"alle\s+lørdag(?:e)?\s+i\s+([a-zæøå]+)",
    flags=re.I,
)

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

# =========================
# HELPERS
# =========================
def now_utc_stamp() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

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
    attachments: list[str] | None = None
) -> str:
    end_exclusive = end_inclusive + timedelta(days=1)
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{now_utc_stamp()}",
        f"SUMMARY:{ics_escape(summary)}",
        f"DTSTART;VALUE=DATE:{ics_date(start)}",
        f"DTEND;VALUE=DATE:{ics_date(end_exclusive)}",
        f"DESCRIPTION:{ics_escape(description)}",
    ]
    for url in (attachments or []):
        if url:
            # Apple Calendar understøtter typisk ATTACH med URL
            lines.append(f"ATTACH:{ics_escape(url)}")
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

def contains_any(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in keywords if k and k.strip())

def season_start_year_for_today() -> int:
    today = date.today()
    return today.year if today.month >= 7 else (today.year - 1)

def season_date(season_year_start: int, day: int, month: int) -> date | None:
    """
    Jul-Dec => season_year_start
    Jan-Jun => season_year_start + 1
    """
    y = season_year_start if month >= 7 else (season_year_start + 1)
    try:
        return date(y, month, day)
    except ValueError:
        return None

def extract_ranges(season_year_start: int, text: str) -> list[tuple[date, date]]:
    cleaned = (text or "").replace("–", "-")
    out: list[tuple[date, date]] = []
    for m in RANGE_RE.finditer(cleaned):
        d1, mo1, d2, mo2 = map(int, m.groups())
        start = season_date(season_year_start, d1, mo1)
        end = season_date(season_year_start, d2, mo2)
        if start and end:
            out.append((start, end))
    return out

def first_saturday(year: int, month: int) -> date:
    d = date(year, month, 1)
    # weekday(): Mon=0..Sun=6, Saturday=5
    offset = (5 - d.weekday()) % 7
    return d + timedelta(days=offset)

def nth_saturday(year: int, month: int, n: int) -> date:
    return first_saturday(year, month) + timedelta(days=7 * (n - 1))

def all_saturdays(year: int, month: int) -> list[date]:
    d = first_saturday(year, month)
    out = []
    while d.month == month:
        out.append(d)
        d += timedelta(days=7)
    return out

# =========================
# FETCH + HTML -> TEXT
# =========================
def fetch_html(url: str) -> str:
    r = requests.get(url, headers=FETCH_HEADERS, timeout=30)
    r.raise_for_status()
    html = r.text or ""

    # Guard: DJ kan nogle gange vise "log ind" side til bots
    low = html.lower()
    if "du skal logge ind i jagttiderne" in low:
        raise RuntimeError(
            "DJ-siden returnerede en 'log ind'-side (bot/blocked HTML). "
            "Prøv igen senere, eller ændr headers/brug en anden kilde."
        )
    return html

def html_to_text(html: str) -> str:
    html = BR_RE.sub("\n", html)
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "\n", html)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text

# =========================
# PARSING (GENERAL)
# =========================
def parse_general_species_lines(text: str) -> list[tuple[str, str]]:
    """
    Returns list of (species_name_raw, date_text_raw)
    Example line: "Ræv** 01.09 - 31.01"
    """
    out = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        if not RANGE_RE.search(line.replace("–", "-")):
            continue

        m = RANGE_RE.search(line.replace("–", "-"))
        if not m:
            continue
        species = line[:m.start()].strip(" -•\u00a0\t")
        date_part = line[m.start():].strip()

        # fjern trailing footnote stars "**", "****"
        species = re.sub(r"[*]+$", "", species).strip()
        out.append((species, date_part))
    return out

def normalize_species_name(raw: str, master_species: dict[str, dict]) -> str:
    """
    Map raw species text to canonical name using master.json species list (name + aliases).
    If not found, return raw (trimmed).
    """
    t = (raw or "").strip()
    if not t:
        return t

    low = t.lower()
    for canon, meta in master_species.items():
        if low == canon.lower():
            return canon
        for a in meta.get("aliases", []):
            if a and low == a.lower():
                return canon

    return t

def build_general_periods(
    season_year_start: int,
    general_lines: list[tuple[str, str]],
    master_species: dict[str, dict]
) -> dict[str, list[tuple[date, date]]]:
    """
    species -> list of (start,end) in this season
    """
    periods: dict[str, list[tuple[date, date]]] = {}
    for raw_name, date_text in general_lines:
        canon = normalize_species_name(raw_name, master_species)
        ranges = extract_ranges(season_year_start, date_text)
        if not ranges:
            continue
        periods.setdefault(canon, []).extend(ranges)
    return periods

# =========================
# PARSING (LOCAL)
# =========================
def parse_local_candidates(text: str) -> list[tuple[str, str]]:
    """
    Returns list of (context_area, line)
    context is headings like "Region Syddanmark", "Øen Ærø", "På Bornholm", etc.
    """
    out = []
    context = ""

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue

        low = line.lower()

        # context-ish headings
        if low.startswith("region ") or low.startswith("øen ") or low.startswith("på "):
            context = line.strip(":")
            continue

        # keep lines that look like species rules:
        # either date range, or "ingen jagttid", or lørdag rules
        if ("ingen jagttid" in low) or RANGE_RE.search(line.replace("–", "-")) or ("lørdag" in low):
            out.append((context, line))

    return out

def parse_local_rule_to_events(
    season_year_start: int,
    context: str,
    line: str,
    master_species: dict[str, dict],
    general_periods_for_season: dict[str, list[tuple[date, date]]],
    emit_no_hunting_events: bool
) -> list[tuple[str, date, date, str]]:
    """
    Returns list of (species_canon, start, end, kind)
    kind in {"local_range","no_hunting","local_saturdays"}
    """
    out = []
    low = (line or "").lower()

    # split species part vs rule part (best-effort)
    # If line contains a range, species is before range.
    m = RANGE_RE.search(line.replace("–", "-"))
    if m:
        raw_species = line[:m.start()].strip(" -•\u00a0\t")
        raw_species = re.sub(r"[*]+$", "", raw_species).strip()
        canon = normalize_species_name(raw_species, master_species)
        ranges = extract_ranges(season_year_start, line[m.start():])
        for s, e in ranges:
            out.append((canon, s, e, "local_range"))
        return out

    # "ingen jagttid"
    if "ingen jagttid" in low:
        if not emit_no_hunting_events:
            return []
        # species is before "ingen jagttid"
        raw_species = line.lower().split("ingen jagttid")[0].strip()
        raw_species = raw_species.strip(" -•\u00a0\t")
        raw_species = re.sub(r"[*]+$", "", raw_species).strip()
        canon = normalize_species_name(raw_species, master_species)

        # map to general periods -> create "fredet" events same durations
        periods = general_periods_for_season.get(canon, [])
        for s, e in periods:
            out.append((canon, s, e, "no_hunting"))
        return out

    # lørdag-regler (best-effort):
    # Examples:
    # "Fasan hØne 1. og 2. lørdag i november"
    # "Fasan hane 1. og 2. lørdag i oktober, 1. og 2. lørdag i november, samt alle lørdag i december"
    if "lørdag" in low:
        # species is text before first digit or before "alle"
        m2 = re.search(r"(\d+)\.", line)
        idx = m2.start() if m2 else (line.lower().find("alle") if "alle" in low else -1)
        raw_species = line[:idx].strip(" -•\u00a0\t") if idx > 0 else line.strip()
        raw_species = re.sub(r"[*]+$", "", raw_species).strip()
        canon = normalize_species_name(raw_species, master_species)

        # Compute for months, respecting season year:
        # For month >= 7 use season_year_start else season_year_start+1
        def month_year(month: int) -> int:
            return season_year_start if month >= 7 else (season_year_start + 1)

        # nth saturday patterns (can be multiple in same line)
        for mm in NTH_SAT_RE.finditer(line):
            n1 = int(mm.group(1))
            n2 = int(mm.group(2))
            mon_name = mm.group(3).lower().strip()
            mon = MONTHS_DA.get(mon_name)
            if not mon:
                continue
            y = month_year(mon)
            d1 = nth_saturday(y, mon, n1)
            d2 = nth_saturday(y, mon, n2)
            # create single-day all-day events
            out.append((canon, d1, d1, "local_saturdays"))
            out.append((canon, d2, d2, "local_saturdays"))

        # all saturdays patterns
        for mm in ALL_SAT_RE.finditer(line):
            mon_name = mm.group(1).lower().strip()
            mon = MONTHS_DA.get(mon_name)
            if not mon:
                continue
            y = month_year(mon)
            for d in all_saturdays(y, mon):
                out.append((canon, d, d, "local_saturdays"))

        return out

    return []

# =========================
# LOAD CONFIGS
# =========================
def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))

def load_master() -> dict:
    if not MASTER_PATH.exists():
        raise FileNotFoundError(f"Mangler {MASTER_PATH}")
    master = load_json(MASTER_PATH)

    # Build species map for fast lookup
    species_map: dict[str, dict] = {}
    for s in master.get("species", []):
        name = (s.get("name") or "").strip()
        if name:
            species_map[name] = s

    master["_species_map"] = species_map
    return master

def load_calendar_configs() -> list[dict]:
    if not CALENDAR_DIR.exists():
        raise FileNotFoundError(f"Mangler {CALENDAR_DIR}")
    cfgs = []
    for p in sorted(CALENDAR_DIR.glob("*.json")):
        cfg = load_json(p)
        cfg["_path"] = str(p)
        cfgs.append(cfg)
    if not cfgs:
        raise RuntimeError("Ingen kalender-configs fundet i configs/calendars/")
    return cfgs

# =========================
# MAIN GENERATION
# =========================
def main() -> None:
    master = load_master()
    calendars = load_calendar_configs()

    out_dir = Path(master.get("output_dir", "Jagttids-Kalender"))
    out_dir.mkdir(parents=True, exist_ok=True)

    general_url = master["sources"]["general_url"]
    local_url = master["sources"]["local_url"]

    region_map_url = master.get("local_mode", {}).get("default_region_map_image_url", "")

    master_species = master["_species_map"]

    # Fetch once
    general_text = html_to_text(fetch_html(general_url))
    local_text = html_to_text(fetch_html(local_url))

    # Parse general once (lines)
    general_lines = parse_general_species_lines(general_text)

    # Parse local candidates once
    local_candidates = parse_local_candidates(local_text)

    season0 = season_start_year_for_today()

    for cal_cfg in calendars:
        cal_name = cal_cfg["calendar_name"]
        output_filename = cal_cfg["output_filename"]
        seasons_ahead = int(cal_cfg.get("seasons_ahead", 1))
        use_local = bool(cal_cfg.get("use_local", False))

        include_kw = cal_cfg.get("filters", {}).get("include_area_keywords", []) or []
        exclude_kw = cal_cfg.get("filters", {}).get("exclude_area_keywords", []) or []
        emit_no_hunting = bool(cal_cfg.get("local_rules", {}).get("emit_no_hunting_events", False))

        events: list[str] = []
        uid_counter = 0

        for i in range(seasons_ahead):
            season_year_start = season0 + i

            # Build general periods for THIS season (used by local no-hunting logic)
            general_periods = build_general_periods(
                season_year_start=season_year_start,
                general_lines=general_lines,
                master_species=master_species
            )

            # 1) Add general events if calendar is not local
            if not use_local:
                for raw_name, date_text in general_lines:
                    canon = normalize_species_name(raw_name, master_species)
                    ranges = extract_ranges(season_year_start, date_text)
                    for s, e in ranges:
                        uid_counter += 1

                        meta = master_species.get(canon, {})
                        attachments = []
                        if meta.get("image_url"):
                            attachments.append(meta["image_url"])

                        desc_parts = [
                            f"Kilde: {general_url}",
                            f"Sæsonstart-år: {season_year_start}",
                        ]
                        stt = (meta.get("shooting_time_text") or "").strip()
                        if stt:
                            desc_parts.append("")
                            desc_parts.append(stt)

                        uid = f"jagttid-{season_year_start}-gen-{uid_counter}@luka2945"
                        summary = f"{canon}"
                        events.append(build_event(uid, summary, s, e, "\n".join(desc_parts), attachments))

            # 2) Add local events if calendar is local
            if use_local:
                for context, line in local_candidates:
                    ctx = (context or "").strip()
                    ctx_text = f"{ctx} {line}".strip()

                    # filters on AREA/CONTEXT primarily
                    if include_kw and not contains_any(ctx_text, include_kw):
                        continue
                    if exclude_kw and contains_any(ctx_text, exclude_kw):
                        continue

                    parsed = parse_local_rule_to_events(
                        season_year_start=season_year_start,
                        context=ctx,
                        line=line,
                        master_species=master_species,
                        general_periods_for_season=general_periods,
                        emit_no_hunting_events=emit_no_hunting
                    )

                    for canon, s, e, kind in parsed:
                        uid_counter += 1

                        meta = master_species.get(canon, {})
                        attachments = []
                        # Region-map on ALL local events (som du ønskede)
                        if region_map_url:
                            attachments.append(region_map_url)
                        # Dyrebillede hvis sat
                        if meta.get("image_url"):
                            attachments.append(meta["image_url"])

                        # Title + description
                        if kind == "no_hunting":
                            summary = f"{canon} – INGEN JAGTTID ({ctx})" if ctx else f"{canon} – INGEN JAGTTID"
                        elif kind == "local_saturdays":
                            summary = f"{canon} – Lokal (lørdag) ({ctx})" if ctx else f"{canon} – Lokal (lørdag)"
                        else:
                            summary = f"{canon} – Lokal ({ctx})" if ctx else f"{canon} – Lokal"

                        desc_parts = [
                            f"Kilde (lokal): {local_url}",
                            f"Kilde (generel): {general_url}",
                            f"Sæsonstart-år: {season_year_start}"
                        ]
                        if ctx:
                            desc_parts.insert(0, f"Område: {ctx}")

                        stt = (meta.get("shooting_time_text") or "").strip()
                        if stt:
                            desc_parts.append("")
                            desc_parts.append(stt)

                        uid = f"jagttid-{season_year_start}-loc-{uid_counter}@luka2945"
                        events.append(build_event(uid, summary, s, e, "\n".join(desc_parts), attachments))

        ics = build_calendar(cal_name, events)
        (out_dir / output_filename).write_text(ics, encoding="utf-8")
        print(f"Wrote {out_dir / output_filename}  (events: {len(events)})")

if __name__ == "__main__":
    main()
