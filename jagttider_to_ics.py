import json
import re
from pathlib import Path
from datetime import date, datetime
import requests
from bs4 import BeautifulSoup

# =========================
# CONFIG
# =========================
CONFIG_PATH = Path("config.json")

def load_config() -> dict:
    if CONFIG_PATH.exists():
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "season_year": "auto",
        "exclude_area_keywords": [],
        "include_only_keywords": [],
        "exclude_species_keywords": [],
    }

CONFIG = load_config()

def compute_season_year(value) -> int:
    """
    season_year = året hvor jagtsæsonen starter.
    - "auto": Jul-Dec -> current year, Jan-Jun -> previous year
    """
    if isinstance(value, str) and value.strip().lower() == "auto":
        today = date.today()
        return today.year if today.month >= 7 else (today.year - 1)
    try:
        return int(value)
    except Exception:
        today = date.today()
        return today.year if today.month >= 7 else (today.year - 1)

SEASON_YEAR = compute_season_year(CONFIG.get("season_year", "auto"))
EXCLUDE_AREA_KEYWORDS = CONFIG.get("exclude_area_keywords", [])
INCLUDE_ONLY_KEYWORDS = CONFIG.get("include_only_keywords", [])
EXCLUDE_SPECIES_KEYWORDS = CONFIG.get("exclude_species_keywords", [])

# =========================
# SOURCES
# =========================
GENERAL_URL = "https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/"
LOCAL_URL   = "https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/lokale-jagttider/"

USER_AGENT = "Mozilla/5.0 (JagttiderICSBot; +https://github.com/luka2945/jagttider-kalender)"

# =========================
# HELPERS
# =========================
def contains_any(text: str, keywords: list[str]) -> bool:
    t = (text or "").lower()
    return any(k.lower() in t for k in keywords if k and k.strip())

def allowed_by_include_only(text: str) -> bool:
    if not INCLUDE_ONLY_KEYWORDS:
        return True
    return contains_any(text, INCLUDE_ONLY_KEYWORDS)

def clean_species_name(s: str) -> str:
    # fjerner stjerner/footnote-tegn og ekstra whitespace
    s = (s or "").strip()
    s = s.replace("\xa0", " ")
    s = re.sub(r"\s+", " ", s)
    # fjern trailing footnote stars like *** or *
    s = re.sub(r"[*]+$", "", s).strip()
    return s

def season_date(day: int, month: int) -> date | None:
    """
    Months Jul-Dec belong to SEASON_YEAR
    Months Jan-Jun belong to SEASON_YEAR+1
    Returns None if invalid (e.g. 31.06).
    """
    y = SEASON_YEAR if month >= 7 else (SEASON_YEAR + 1)
    try:
        return date(y, month, day)
    except ValueError:
        return None

# Matches "01.09 - 31.01" (also accepts 1.9 and en-dash)
RANGE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\s*[-–]\s*(\d{1,2})\.(\d{1,2})")

def extract_ranges(text: str) -> list[tuple[date, date]]:
    """
    Extracts all ranges from a string like:
      "16.05 - 15.07 og 01.10 - 31.01"
    Returns list of (start, end_inclusive).
    Skips invalid dates but logs them.
    """
    cleaned = (text or "").replace("–", "-")
    out: list[tuple[date, date]] = []

    for m in RANGE_RE.finditer(cleaned):
        d1, mo1, d2, mo2 = map(int, m.groups())

        start = season_date(d1, mo1)
        end = season_date(d2, mo2)

        if not start or not end:
            print(f"SKIP invalid date range: {d1}.{mo1} - {d2}.{mo2}  (from: '{text}')")
            continue

        out.append((start, end))

    return out

def ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

def ics_date(dt: date) -> str:
    return dt.strftime("%Y%m%d")

def build_event(uid: str, summary: str, start: date, end_inclusive: date, description: str) -> str:
    # DTEND for all-day events is exclusive
    end_exclusive = date.fromordinal(end_inclusive.toordinal() + 1)
    return "\n".join([
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{ics_escape(summary)}",
        f"DTSTART;VALUE=DATE:{ics_date(start)}",
        f"DTEND;VALUE=DATE:{ics_date(end_exclusive)}",
        f"DESCRIPTION:{ics_escape(description)}",
        "END:VEVENT",
    ])

def build_calendar(events: list[str]) -> str:
    return "\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//luka2945//Jagttider ICS//DA",
        "CALSCALE:GREGORIAN",
        *events,
        "END:VCALENDAR",
        ""
    ])

# =========================
# FETCH + PARSE TABLES
# =========================
def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.text

def extract_table_rows(html: str) -> list[tuple[str, str]]:
    """
    Extract rows from any HTML table where row has 2+ columns.
    We assume:
      col0 = species / label
      col1 = date string (contains "dd.mm - dd.mm")
    Returns list of (species, date_text)
    """
    soup = BeautifulSoup(html, "html.parser")
    rows: list[tuple[str, str]] = []

    for tr in soup.find_all("tr"):
        tds = tr.find_all(["td", "th"])
        if len(tds) < 2:
            continue

        left = tds[0].get_text(" ", strip=True)
        right = tds[1].get_text(" ", strip=True)

        left = clean_species_name(left)
        right = right.replace("\xa0", " ").strip()

        if not left or not right:
            continue

        # only keep if it looks like it contains date ranges
        if not RANGE_RE.search(right.replace("–", "-")):
            continue

        rows.append((left, right))

    return rows

def passes_filters(kind: str, area: str, species: str, date_text: str) -> bool:
    filter_text = f"{kind} {area} {species} {date_text}".strip()

    if contains_any(filter_text, EXCLUDE_AREA_KEYWORDS):
        return False
    if contains_any(species, EXCLUDE_SPECIES_KEYWORDS):
        return False
    if not allowed_by_include_only(filter_text):
        return False
    return True

# =========================
# MAIN
# =========================
def main() -> None:
    general_html = fetch_html(GENERAL_URL)
    local_html = fetch_html(LOCAL_URL)

    general_rows = extract_table_rows(general_html)
    local_rows = extract_table_rows(local_html)

    events: list[str] = []
    uid_counter = 0

    # GENERELLE
    for species, date_text in general_rows:
        if not passes_filters("generel", "", species, date_text):
            continue

        for start, end in extract_ranges(date_text):
            uid_counter += 1
            summary = f"{species} (generel)"
            desc = f"Kilde: {GENERAL_URL}"
            uid = f"jagttid-{SEASON_YEAR}-g-{uid_counter}@luka2945"
            events.append(build_event(uid, summary, start, end, desc))

    # LOKALE
    # (vi har ikke sikre område-headings fra tabellen her – men dine exclude_keywords kan stadig matche ord i species/date)
    for species, date_text in local_rows:
        if not passes_filters("lokal", "", species, date_text):
            continue

        for start, end in extract_ranges(date_text):
            uid_counter += 1
            summary = f"{species} (lokal)"
            desc = f"Kilde: {LOCAL_URL}"
            uid = f"jagttid-{SEASON_YEAR}-l-{uid_counter}@luka2945"
            events.append(build_event(uid, summary, start, end, desc))

    cal = build_calendar(events)
    Path("jagttider.ics").write_text(cal, encoding="utf-8")

    print(f"Season year: {SEASON_YEAR}")
    print(f"General rows: {len(general_rows)} | Local rows: {len(local_rows)}")
    print(f"Events generated: {len(events)}")

if __name__ == "__main__":
    main()
