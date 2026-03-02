import json
import re
from pathlib import Path
from datetime import date, datetime
import requests

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
    season_year = året hvor jagtsæsonen starter (typisk sensommer/efterår).
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

def season_date(day: int, month: int) -> date | None:
    """
    Months Jul-Dec belong to SEASON_YEAR
    Months Jan-Jun belong to SEASON_YEAR+1
    """
    y = SEASON_YEAR if month >= 7 else (SEASON_YEAR + 1)
    try:
        return date(y, month, day)
    except ValueError:
        return None

RANGE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\s*[-–]\s*(\d{1,2})\.(\d{1,2})")

def add_year_safe(d: date, years: int = 1) -> date:
    # håndter 29/2 osv. simpelt: fald tilbage til 28/2
    try:
        return date(d.year + years, d.month, d.day)
    except ValueError:
        # fx 29 feb -> 28 feb
        if d.month == 2 and d.day == 29:
            return date(d.year + years, 2, 28)
        raise

def extract_ranges(text: str) -> list[tuple[date, date]]:
    cleaned = text.replace("–", "-")
    out: list[tuple[date, date]] = []

    for m in RANGE_RE.finditer(cleaned):
        d1, mo1, d2, mo2 = map(int, m.groups())

        start = season_date(d1, mo1)
        end = season_date(d2, mo2)

        if not start or not end:
            print(f"SKIP invalid date range: {d1}.{mo1} - {d2}.{mo2}  (from: '{text}')")
            continue

        # Hvis end kommer før start (typisk når end er i Jul men start i Maj/Jun),
        # så ligger end i næste år.
        if end < start:
            end = add_year_safe(end, 1)

        out.append((start, end))

    return out

def ics_escape(s: str) -> str:
    return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

def ics_date(dt: date) -> str:
    return dt.strftime("%Y%m%d")

def build_event(uid: str, summary: str, start: date, end_inclusive: date, description: str) -> str:
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

def build_calendar(events: list[str], calname: str) -> str:
    return "\n".join([
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//luka2945//Jagttider ICS//DA",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{ics_escape(calname)}",
        *events,
        "END:VCALENDAR",
        ""
    ])

# =========================
# FETCH + HTML -> TEXT
# =========================
def fetch_html(url: str) -> str:
    r = requests.get(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
        },
        timeout=30
    )
    r.raise_for_status()
    return r.text

def html_to_text(html: str) -> str:
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", "\n", html)
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text

# =========================
# PARSE (fix: brug forrige label)
# =========================
def parse_candidates(text: str, kind: str) -> list[tuple[str, str, str, str]]:
    """
    Returns (kind, context, label, line_with_dates)
    label = typisk dyrenavn fra forrige linje
    """
    candidates: list[tuple[str, str, str, str]] = []
    context = ""
    last_label = ""

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue

        low = line.lower()

        # context-ish lines (loose)
        if ("kommune" in low) or ("region" in low) or low.startswith("øen ") or low.startswith("på ") or low.endswith(":"):
            context = line.strip(":")
            last_label = ""  # reset label ved ny sektion
            continue

        # hvis denne linje er en dato-range -> brug last_label som "art"
        if RANGE_RE.search(line.replace("–", "-")):
            candidates.append((kind, context, last_label.strip(), line))
            continue

        # ellers: opdater label, men undgå støj
        if len(line) >= 2 and "jagttid" not in low and "klik" not in low:
            last_label = line

    return candidates

def main() -> None:
    general_text = html_to_text(fetch_html(GENERAL_URL))
    local_text = html_to_text(fetch_html(LOCAL_URL))

    candidates = []
    candidates += parse_candidates(general_text, "generel")
    candidates += parse_candidates(local_text, "lokal")

    events: list[str] = []
    uid_counter = 0

    for kind, context, label, date_line in candidates:
        low = (date_line or "").lower()
        if "ingen jagttid" in low:
            continue

        # filtrér også label
        if contains_any(f"{kind} {context} {label} {date_line}", EXCLUDE_AREA_KEYWORDS):
            continue
        if contains_any(label, EXCLUDE_SPECIES_KEYWORDS):
            continue
        if not allowed_by_include_only(f"{kind} {context} {label} {date_line}"):
            continue

        ranges = extract_ranges(date_line)
        for start, end in ranges:
            uid_counter += 1

            art = label if label else "(ukendt art)"
            summary = f"{art} ({kind})"

            desc = f"Kilde: {GENERAL_URL if kind == 'generel' else LOCAL_URL}\nSæsonstart-år: {SEASON_YEAR}"
            if kind == "lokal" and context:
                desc = f"Område: {context}\n{desc}"

            uid = f"jagttid-{SEASON_YEAR}-{kind}-{uid_counter}@luka2945"
            events.append(build_event(uid, summary, start, end, desc))

    cal = build_calendar(events, "Jagttider")
    Path("jagttider.ics").write_text(cal, encoding="utf-8")

    print(f"Season year: {SEASON_YEAR}")
    print(f"Events generated: {len(events)}")

if __name__ == "__main__":
    main()
