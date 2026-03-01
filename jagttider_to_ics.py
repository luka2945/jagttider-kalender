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
        # fallback hvis config er underlig
        today = date.today()
        return today.year if today.month >= 7 else (today.year - 1)

SEASON_YEAR = compute_season_year(CONFIG.get("season_year", "auto"))
EXCLUDE_AREA_KEYWORDS = CONFIG.get("exclude_area_keywords", [])
INCLUDE_ONLY_KEYWORDS = CONFIG.get("include_only_keywords", [])
EXCLUDE_SPECIES_KEYWORDS = CONFIG.get("exclude_species_keywords", [])

# =========================
# SOURCES (nemmere at parse end retsinformation)
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

def season_date(day: int, month: int) -> date:
    """
    Months Jul-Dec belong to SEASON_YEAR
    Months Jan-Jun belong to SEASON_YEAR+1
    """
    y = SEASON_YEAR if month >= 7 else (SEASON_YEAR + 1)
    return date(y, month, day)

# Finder dato-interval: "01.09 - 31.01" (også med en-dags 1.9)
RANGE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})\s*[-–]\s*(\d{1,2})\.(\d{1,2})")

def extract_ranges(text: str) -> list[tuple[date, date]]:
    """
    Kan håndtere flere intervaller i samme linje, fx "... 16.05-15.07 og 01.10-31.01"
    """
    cleaned = text.replace("–", "-")
    out: list[tuple[date, date]] = []
    for m in RANGE_RE.finditer(cleaned):
        d1, mo1, d2, mo2 = map(int, m.groups())
        start = season_date(d1, mo1)
        end = season_date(d2, mo2)
        out.append((start, end))
    return out

def ics_escape(s: str) -> str:
    # iCal escaping (basic)
    return (s or "").replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")

def ics_date(dt: date) -> str:
    return dt.strftime("%Y%m%d")

def build_event(uid: str, summary: str, start: date, end_inclusive: date, description: str) -> str:
    # DTEND for all-day events is exclusive
    end_exclusive = date.fromordinal(end_inclusive.toordinal() + 1)
    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')}",
        f"SUMMARY:{ics_escape(summary)}",
        f"DTSTART;VALUE=DATE:{ics_date(start)}",
        f"DTEND;VALUE=DATE:{ics_date(end_exclusive)}",
        f"DESCRIPTION:{ics_escape(description)}",
        "END:VEVENT",
    ]
    return "\n".join(lines)

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
# FETCH + HTML -> TEXT
# =========================
def fetch_html(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.text

def html_to_text(html: str) -> str:
    # gør <br> til newline
    html = re.sub(r"<br\s*/?>", "\n", html, flags=re.I)
    # fjern scripts/styles
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.I | re.S)
    # tags til newline
    text = re.sub(r"<[^>]+>", "\n", html)
    # whitespace cleanup
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text

# =========================
# PARSE
# =========================
def parse_candidates(text: str, kind: str) -> list[tuple[str, str, str]]:
    """
    Returnerer (kind, context, line)
    context = seneste "område"-agtige linje (bruges især på lokale jagttider)
    """
    candidates: list[tuple[str, str, str]] = []
    context = ""

    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue

        low = line.lower()

        # Context-linjer for lokale regler (meget loose, men nok til filtering)
        if ("kommune" in low) or ("region" in low) or low.startswith("øen ") or low.startswith("på ") or low.endswith(":"):
            context = line.strip(":")
            continue

        # Linjer med dato-interval
        if RANGE_RE.search(line.replace("–", "-")):
            candidates.append((kind, context, line))

    return candidates

def main() -> None:
    general_text = html_to_text(fetch_html(GENERAL_URL))
    local_text = html_to_text(fetch_html(LOCAL_URL))

    candidates = []
    candidates += parse_candidates(general_text, "generel")
    candidates += parse_candidates(local_text, "lokal")

    events: list[str] = []
    uid_counter = 0

    for kind, context, line in candidates:
        low = line.lower()
        if "ingen jagttid" in low:
            continue

        # find første dato-match
        m = RANGE_RE.search(line.replace("–", "-"))
        if not m:
            continue

        art = line[:m.start()].strip(" -•\u00a0\t")
        date_part = line[m.start():].strip()

        # Filtrering (dit config.json bestemmer)
        filter_text = f"{kind} {context} {art} {date_part}".strip()
        if contains_any(filter_text, EXCLUDE_AREA_KEYWORDS):
            continue
        if contains_any(art, EXCLUDE_SPECIES_KEYWORDS):
            continue
        if not allowed_by_include_only(filter_text):
            continue

        ranges = extract_ranges(date_part)
        for start, end in ranges:
            uid_counter += 1
            summary = f"{art} ({kind})"
            desc = f"Kilde: {GENERAL_URL if kind=='generel' else LOCAL_URL}"
            if kind == "lokal" and context:
                desc = f"Område: {context}\n{desc}"

            uid = f"jagttid-{SEASON_YEAR}-{uid_counter}@luka2945"
            events.append(build_event(uid, summary, start, end, desc))

    cal = build_calendar(events)
    Path("jagttider.ics").write_text(cal, encoding="utf-8")
    print(f"Season year: {SEASON_YEAR}")
    print(f"Generated events: {len(events)}")

if __name__ == "__main__":
    main()
