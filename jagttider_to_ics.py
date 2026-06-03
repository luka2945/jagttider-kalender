from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from datetime import date, datetime, timedelta
import calendar as calmod

import requests
from bs4 import BeautifulSoup

MASTER_PATH = Path("configs/master.json")
CALENDAR_CONFIG_DIR = Path("configs/calendars")
OUT_DIR = Path("Jagttids-Kalender")
OUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_RETSINFORMATION_URL = "https://www.retsinformation.dk/eli/lta/2024/470"
DEFAULT_USER_AGENT = "Mozilla/5.0 (JagttiderICSBot; +https://github.com/luka2945/jagttider-kalender)"
DEFAULT_LANG = "da-DK,da;q=0.9,en-US;q=0.7,en;q=0.6"

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

# Accepterer både:
# 16.11-30.11
# 16.11-30-11
# 16-11-30-11
RANGE_RE = re.compile(r"(\d{1,2})[.-](\d{1,2})\s*[-–]\s*(\d{1,2})[.-](\d{1,2})")

NTH_SAT_RE = re.compile(
    r"(\d)\.\s*og\s*(\d)\.\s*lørdag i ([a-zæøå]+)",
    flags=re.I,
)

ALL_SAT_RE = re.compile(
    r"alle\s+lørdage?\s+i\s+([a-zæøå]+)",
    flags=re.I,
)


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


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_master() -> dict:
    if MASTER_PATH.exists():
        return load_json(MASTER_PATH)
    return {}


def list_calendar_configs() -> list[dict]:
    if not CALENDAR_CONFIG_DIR.exists():
        return []
    return [load_json(p) for p in sorted(CALENDAR_CONFIG_DIR.glob("*.json"))]


def compute_season_year_auto(today: date | None = None) -> int:
    t = today or date.today()
    return t.year if t.month >= 7 else (t.year - 1)


def season_range_dates(
    season_year: int,
    d1: int,
    m1: int,
    d2: int,
    m2: int
) -> tuple[date | None, date | None]:
    start_year = season_year if m1 >= 7 else (season_year + 1)
    end_year = start_year

    if (m2, d2) < (m1, d1):
        end_year += 1

    try:
        return date(start_year, m1, d1), date(end_year, m2, d2)
    except ValueError:
        return None, None


def ics_escape(s: str) -> str:
    return (
        (s or "")
        .replace("\\", "\\\\")
        .replace("\n", "\\n")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )


def ics_date(d: date) -> str:
    return d.strftime("%Y%m%d")


def build_event(
    uid: str,
    summary: str,
    start: date,
    end_inclusive: date,
    description: str,
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
        "END:VEVENT",
    ]
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


def fetch_text(url: str, ua: str) -> str:
    r = requests.get(
        url,
        headers={
            "User-Agent": ua,
            "Accept-Language": DEFAULT_LANG,
            "Accept": "application/xml,text/xml,text/html,application/xhtml+xml,*/*;q=0.8",
        },
        timeout=30,
    )
    r.raise_for_status()
    return r.text


def normalize_source_url(base_url: str) -> str:
    return base_url.rstrip("/")


def fetch_retsinformation_document(base_url: str, ua: str) -> str:
    base = normalize_source_url(base_url)

    urls_to_try = [
        f"{base}/xml",
        f"{base}/rawhtml",
        base,
    ]

    last_error: Exception | None = None

    for url in urls_to_try:
        try:
            text = fetch_text(url, ua)
            if "You need to enable JavaScript" in text and url == base:
                raise RuntimeError("Base-URL returnerede kun JavaScript-app.")
            print(f"Fetched source: {url}")
            return text
        except Exception as e:
            print(f"Could not fetch {url}: {e}")
            last_error = e

    raise RuntimeError(f"Kunne ikke hente Retsinformation-kilden. Sidste fejl: {last_error}")


def html_or_xml_to_lines(text: str) -> list[str]:
    soup = BeautifulSoup(text, "lxml")
    raw_text = soup.get_text("\n")

    lines = []
    for raw in raw_text.splitlines():
        s = normalize_line(raw)
        if s:
            lines.append(s)

    return lines


def normalize_line(s: str) -> str:
    s = (s or "").replace("\xa0", " ")

    replacements = {
        "Kom- mune": "Kommune",
        "kom- mune": "kommune",
        "novem- ber": "november",
        "decem- ber": "december",
        "septem- ber": "september",
        "oktob- er": "oktober",
        "janu- ar": "januar",
        "sol- nedgang": "solnedgang",
        "sol- opgang": "solopgang",
        "Aal- Nord": "Aal-Nord",
        "kom- munen": "kommunen",
        "kom- muner": "kommuner",
        "kommune- grænsen": "kommunegrænsen",
        "kommune- grænse": "kommunegrænse",
    }

    for a, b in replacements.items():
        s = s.replace(a, b)

    # Ret fejl som 16.11-30-11 til 16.11-30.11
    s = re.sub(
        r"(\d{1,2}\.\d{1,2})\s*[-–]\s*(\d{1,2})-(\d{1,2})",
        r"\1-\2.\3",
        s,
    )

    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(":")
    return s


def clean_species_name(s: str) -> str:
    s = normalize_line(s)

    # Fjern kategori-overskrifter hvis de er kommet med i artsnavnet.
    # Fx "Andefugle Gråand" -> "Gråand"
    category_prefixes = [
        "Andefugle",
        "Invasive arter",
        "Måger",
        "Rovdyr",
        "Vadefugle",
        "Hønsefugle",
        "Kragefugle",
        "Duer",
        "Vandhøns",
        "Støttetandede",
        "Hovdyr",
    ]

    for prefix in category_prefixes:
        s = re.sub(
            rf"^{re.escape(prefix)}\s+",
            "",
            s,
            flags=re.IGNORECASE,
        )

    if s.lower() in [p.lower() for p in category_prefixes]:
        return ""

    # Fjern bilag-reference foran art.
    # Fx "(se bilag 16). Dåspidshjort" -> "Dåspidshjort"
    # Fx "bilag 26) Då og dåkalv" -> "Då og dåkalv"
    # Fx "27). Dåspidshjort" -> "Dåspidshjort"
    s = re.sub(
        r"^\s*\(?\s*se\s+bilag\s+\d+\s*\)?\.?\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )

    s = re.sub(
        r"^\s*bilag\s+\d+\s*\)?\.?\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )

    s = re.sub(
        r"^\s*\d+\s*\)\.?\s*",
        "",
        s,
        flags=re.IGNORECASE,
    )

    # Fjern "Som for Øen Als, dog Råvildt" -> "Råvildt"
    s = re.sub(r"^Som for .*?,\s*dog\s+", "", s, flags=re.IGNORECASE)

    # Fjern alt efter " - jagttid fra ..."
    # Fx "Kronhind - jagttid fra ½ time før..." -> "Kronhind"
    s = re.sub(
        r"\s*[-–]\s*jagttid\s+fra.*$",
        "",
        s,
        flags=re.IGNORECASE,
    )

    # Fjern stjerner
    s = re.sub(r"\s*\*+\s*$", "", s)

    # Fjern "(se dog regionale jagttider)" osv.
    s = re.sub(
        r"\s*\(\s*se\s+dog.*?\)",
        "",
        s,
        flags=re.IGNORECASE,
    )

    # Fjern "(se bilag 16)" hvis det ligger sidst
    s = re.sub(
        r"\s*\(\s*se\s+bilag\s+\d+\s*\)",
        "",
        s,
        flags=re.IGNORECASE,
    )

    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\.$", "", s)

    return s.strip()


def is_explanation_line(line: str) -> bool:
    low = normalize_line(line).lower()

    starts = (
        "dog ikke",
        "afstanden",
        "afstand",
        "dvs.",
        "det vil sige",
        "hvor der",
        "ved normal",
        "se dog",
        "gælder ikke",
        "bestemmelsen gælder ikke",
        "jagttid fra",
        "- jagttid fra",
    )

    if low.startswith(starts):
        return True

    if "indgår ikke i området" in low:
        return True

    # Linjer der kun er bilag-reference skal ikke blive til art
    if re.fullmatch(r"\(?\s*se\s+bilag\s+\d+\s*\)?\.?", low):
        return True

    if re.fullmatch(r"bilag\s+\d+\)?\.?", low):
        return True

    return False


def format_info_note(note: str) -> str:
    note = (note or "").strip()
    if not note:
        return ""

    if note.lower().startswith("info:"):
        return note

    return f"Info: {note}"


def normalize_species(s: str) -> str:
    return normalize_line(s).lower()


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
        sub = sub.rstrip(".")
        return f"{sub}. - {region}".strip()

    return region


def region_abbr(area: str | None) -> str:
    region, _sub = split_area(area)
    low = region.lower()

    if "hovedstaden" in low:
        return "RH"
    if "sjælland" in low:
        return "RSj"
    if "syddanmark" in low:
        return "RSy"
    if "midtjylland" in low:
        return "RM"
    if "nordjylland" in low:
        return "RN"

    return ""


def get_species_meta(species_name: str, species_meta: dict) -> dict:
    key = normalize_species(species_name)

    if key in species_meta:
        return species_meta[key]

    for meta_key, meta_val in species_meta.items():
        mk = normalize_species(meta_key)
        if mk and mk in key:
            return meta_val

    return {}


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


def is_bilag_line(line: str) -> bool:
    return bool(re.fullmatch(r"Bilag\s+\d+", line, flags=re.I))


def bilag_number(line: str) -> int | None:
    m = re.fullmatch(r"Bilag\s+(\d+)", line, flags=re.I)
    if not m:
        return None
    return int(m.group(1))


def section_lines(lines: list[str], start_bilag: int, end_bilag: int | None = None) -> list[str]:
    start_idx = None
    end_idx = None

    for i, line in enumerate(lines):
        n = bilag_number(line)
        if n == start_bilag and start_idx is None:
            start_idx = i + 1
            continue

        if start_idx is not None and n is not None:
            if end_bilag is None or n == end_bilag or n > start_bilag:
                end_idx = i
                break

    if start_idx is None:
        return []

    if end_idx is None:
        end_idx = len(lines)

    return lines[start_idx:end_idx]


def all_local_bilag_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    for b in (2, 3, 4):
        part = section_lines(lines, b)
        if part:
            out.extend([f"__BILAG_{b}__"])
            out.extend(part)
    return out


def line_has_range(line: str) -> bool:
    return bool(RANGE_RE.search(line.replace("–", "-")))


def line_has_no_hunting(line: str) -> bool:
    return "ingen jagttid" in line.lower()


def line_has_special_rule(line: str) -> bool:
    return "lørdag" in line.lower()


def split_species_and_rule(line: str) -> tuple[str | None, str | None]:
    txt = normalize_line(line)
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


def is_probably_header(line: str) -> bool:
    low = line.lower().strip()
    headers = {
        "dyreart",
        "jagttid",
        "område",
        "art",
        "vildtart",
        "generelle jagttider",
        "lokale jagttider",
        "lokale jagttider - andet",
        "lokale jagttider – andet",
        "lokale jagttider - kronvildt",
        "lokale jagttider – kronvildt",
        "lokale jagttider - dåvildt",
        "lokale jagttider – dåvildt",
    }
    return low in headers


def is_region_heading(line: str) -> bool:
    low = normalize_line(line).lower()
    return low.startswith("region ") and (
        "hovedstaden" in low
        or "sjælland" in low
        or "syddanmark" in low
        or "midtjylland" in low
        or "nordjylland" in low
    )


def is_area_like(line: str) -> bool:
    low = normalize_line(line).lower()

    if is_region_heading(line):
        return True

    area_words = [
        "kommune",
        "kommuner",
        "region",
        "øen",
        "halvøen",
        "undtagen",
        "bortset fra",
        "vest for",
        "øst for",
        "nord for",
        "syd for",
        "dele af",
        "området",
        "mellem",
        "inklusive",
        "bornholm",
        "fanø",
        "læsø",
        "samsø",
        "endelave",
        "anholt",
        "møn",
        "lolland",
        "falster",
        "langeland",
        "ærø",
        "als",
        "kegnæs",
        "mandø",
        "lyø",
        "strynø",
        "sejerø",
        "fejø",
        "femø",
    ]

    return any(w in low for w in area_words)


def is_area_continuation(line: str) -> bool:
    low = normalize_line(line).lower()

    starts = (
        "kommune",
        "kommuner",
        "og ",
        "af ",
        "der ",
        "som ",
        "ligger ",
        "nord for",
        "syd for",
        "vest for",
        "øst for",
        "mellem ",
        "til ",
        "inklusive ",
        "samt ",
        "den del ",
        "de dele ",
        "øst for",
        "vest for",
        "nord og øst for",
        "syd og øst for",
    )

    return low.startswith(starts)


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


def find_general_duration_ranges_for_species(
    species: str,
    general_by_species: dict[str, list[SeasonRange]],
) -> list[SeasonRange]:
    key = normalize_species(species)

    if key in general_by_species:
        return general_by_species[key]

    fallback_names: dict[str, list[str]] = {
        "fasanhøne": ["fasan"],
        "fasanhane": ["fasan"],
        "råvildt": ["råbuk", "rå og -lam", "rå"],
        "rå og -lam": ["råvildt", "råbuk", "rå"],
        "rå": ["råvildt", "råbuk", "rå og -lam"],
        "råbuk": ["råvildt", "rå og -lam", "rå"],
        "kronvildt": ["kronhjort", "kronhind", "kronkalv", "kronspidshjort"],
        "kronhind": ["kronvildt"],
        "kronkalv": ["kronvildt"],
        "kronhjort": ["kronvildt"],
        "kronspidshjort": ["kronvildt"],
        "dåvildt": ["dåhjort", "dåspidshjort", "då og -kalv", "då og dåkalv", "då"],
        "då og -kalv": ["dåvildt", "dåhjort", "då"],
        "då og dåkalv": ["dåvildt", "dåhjort", "då"],
        "dåhjort": ["dåvildt", "då og -kalv", "då og dåkalv", "då"],
        "dåspidshjort": ["dåvildt", "dåhjort", "då"],
        "då": ["dåvildt", "då og -kalv", "dåhjort"],
        "sika": ["sikahjort", "sikahind og - kalv"],
        "sikahind og - kalv": ["sika", "sikahjort"],
        "sikahjort": ["sika", "sikahind og - kalv"],
        "muflon": ["muflonfår og -lam", "muflonvædder"],
        "muflonfår og -lam": ["muflon", "muflonvædder"],
        "muflonvædder": ["muflon", "muflonfår og -lam"],
    }

    out: list[SeasonRange] = []

    for fb in fallback_names.get(key, []):
        if fb in general_by_species:
            out.extend(general_by_species[fb])

    if out:
        return out

    for general_key, ranges in general_by_species.items():
        if key in general_key or general_key in key:
            return ranges

    return []


def parse_general(lines: list[str], season_year: int) -> list[SeasonRange]:
    bilag1 = section_lines(lines, 1)
    ranges: list[SeasonRange] = []

    pending: list[str] = []

    for line in bilag1:
        txt = normalize_line(line)

        if not txt:
            continue
        if is_probably_header(txt):
            continue
        if is_bilag_line(txt):
            continue
        if is_explanation_line(txt):
            continue

        species_inline, rule_inline = split_species_and_rule(txt)

        if species_inline and rule_inline:
            species = species_inline
            rule_text = rule_inline
            pending = []
        else:
            if not line_has_range(txt) and not line_has_no_hunting(txt) and not line_has_special_rule(txt):
                pending.append(txt)
                pending = pending[-4:]
                continue

            if pending:
                species = clean_species_name(" ".join(pending))
                rule_text = txt
                pending = []
            else:
                continue

        if not species or not rule_text:
            continue

        if line_has_no_hunting(rule_text):
            continue

        for (d1, m1, d2, m2) in RANGE_RE.findall(rule_text.replace("–", "-")):
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

    uniq, seen = [], set()
    for r in ranges:
        key = (normalize_species(r.species), r.start, r.end_inclusive, r.kind, r.area)
        if key not in seen:
            seen.add(key)
            uniq.append(r)

    print(f"GENERAL DEBUG season {season_year}: ranges={len(uniq)}")
    return uniq


def parse_local(lines: list[str], season_year: int) -> tuple[list[SeasonRange], list[NoHuntingMarker], list[SpecialDayRule]]:
    local_lines = all_local_bilag_lines(lines)

    local_ranges: list[SeasonRange] = []
    no_hunting: list[NoHuntingMarker] = []
    specials: list[SpecialDayRule] = []

    current_region = ""
    current_area = ""
    pending: list[str] = []

    for line in local_lines:
        txt = normalize_line(line)

        if not txt:
            continue

        if txt.startswith("__BILAG_"):
            pending = []
            current_area = ""
            if txt in ("__BILAG_3__", "__BILAG_4__"):
                current_region = ""
            continue

        if is_probably_header(txt):
            continue

        if is_explanation_line(txt):
            continue

        if current_area and is_area_continuation(txt) and not line_has_range(txt) and not line_has_no_hunting(txt) and not line_has_special_rule(txt):
            if current_area == current_region:
                current_region = f"{current_region} {txt}".strip()
                current_area = current_region
            else:
                current_area = f"{current_area} {txt}".strip()
            pending = []
            continue

        if is_region_heading(txt):
            current_region = txt
            current_area = txt
            pending = []
            continue

        if is_area_like(txt) and not line_has_range(txt) and not line_has_no_hunting(txt) and not line_has_special_rule(txt):
            if is_region_heading(txt):
                current_region = txt
                current_area = txt
            else:
                current_area = txt

            pending = []
            continue

        species_inline, rule_inline = split_species_and_rule(txt)

        if species_inline and rule_inline:
            species = species_inline
            rule_text = rule_inline
            pending = []
        else:
            if line_has_range(txt) or line_has_no_hunting(txt) or line_has_special_rule(txt):
                clean_pending = [
                    p for p in pending
                    if p
                    and not is_probably_header(p)
                    and not is_explanation_line(p)
                ]

                if not clean_pending:
                    continue

                species = clean_species_name(" ".join(clean_pending))
                rule_text = txt
                pending = []
            else:
                if not is_probably_header(txt) and not is_explanation_line(txt):
                    pending.append(txt)
                    pending = pending[-5:]
                continue

        if not species or not rule_text:
            continue

        species = clean_species_name(species)

        if species.lower() in ("dyreart", "art", "vildtart", "område", "jagttid"):
            continue

        if current_region:
            if current_area and current_area != current_region:
                area = f"{current_region} | {current_area}"
            else:
                area = current_region
        else:
            area = current_area or "Lokalt område"

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

    uniq_local, seen_local = [], set()
    for r in local_ranges:
        key = (normalize_species(r.species), r.start, r.end_inclusive, r.kind, r.area)
        if key not in seen_local:
            seen_local.add(key)
            uniq_local.append(r)

    uniq_no, seen_no = [], set()
    for r in no_hunting:
        key = (normalize_species(r.species), r.area.lower())
        if key not in seen_no:
            seen_no.add(key)
            uniq_no.append(r)

    uniq_sp, seen_sp = [], set()
    for r in specials:
        key = (normalize_species(r.species), r.area.lower(), tuple(r.dates))
        if key not in seen_sp:
            seen_sp.add(key)
            uniq_sp.append(r)

    print(
        f"LOCAL DEBUG season {season_year}: "
        f"ranges={len(uniq_local)} no_hunting={len(uniq_no)} specials={len(uniq_sp)}"
    )

    return uniq_local, uniq_no, uniq_sp


def main() -> None:
    master = load_master()

    retsinformation_url = master.get("retsinformation_url", DEFAULT_RETSINFORMATION_URL)
    ua = master.get("user_agent", DEFAULT_USER_AGENT)

    seasons_ahead_default = int(master.get("seasons_ahead", 2))
    local_map_image_url = master.get("local_map_image_url", "")
    species_meta = master.get("species_meta", {})

    source_url_for_notes = retsinformation_url

    calendar_cfgs = list_calendar_configs()
    if not calendar_cfgs:
        raise RuntimeError("Ingen configs fundet i configs/calendars/*.json")

    document_text = fetch_retsinformation_document(retsinformation_url, ua)
    lines = html_or_xml_to_lines(document_text)

    print(f"Source lines loaded: {len(lines)}")

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

            general_ranges = parse_general(lines, season_year)
            local_ranges, no_hunting, specials = parse_local(lines, season_year)

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

                    info_note = format_info_note(notes)
                    if info_note:
                        desc_parts.append(info_note)

                    desc_parts.append(f"Kilde: {source_url_for_notes}")

                    if img:
                        desc_parts.append(f"Billede: {img}")

                    uid = f"jagttid-{season_year}-gen-{uid_counter}@luka2945"
                    events.append(
                        build_event(
                            uid=uid,
                            summary=f"{r.species} - Jagttid",
                            start=r.start,
                            end_inclusive=r.end_inclusive,
                            description="\n".join(desc_parts),
                        )
                    )

            else:
                for r in local_ranges:
                    if not area_allowed(r.area, include_area, exclude_area):
                        continue

                    uid_counter += 1

                    meta = get_species_meta(r.species, species_meta)
                    img = meta.get("image_url", "")
                    notes = meta.get("notes", "")
                    abbr = region_abbr(r.area)

                    desc_parts = []

                    info_note = format_info_note(notes)
                    if info_note:
                        desc_parts.append(info_note)

                    if r.area:
                        desc_parts.append(f"Område: {display_area(r.area)}")

                    desc_parts.append(f"Kilde: {source_url_for_notes}")

                    if img:
                        desc_parts.append(f"Billede: {img}")

                    if local_map_image_url:
                        desc_parts.append(f"Regionskort: {local_map_image_url}")

                    uid = f"jagttid-{season_year}-lok-{uid_counter}@luka2945"
                    events.append(
                        build_event(
                            uid=uid,
                            summary=f"{r.species} - Lokal jagttid {abbr}".strip(),
                            start=r.start,
                            end_inclusive=r.end_inclusive,
                            description="\n".join(desc_parts),
                        )
                    )

                for sp in specials:
                    if not area_allowed(sp.area, include_area, exclude_area):
                        continue

                    for d in sp.dates:
                        uid_counter += 1

                        meta = get_species_meta(sp.species, species_meta)
                        img = meta.get("image_url", "")
                        notes = meta.get("notes", "")
                        abbr = region_abbr(sp.area)

                        desc_parts = []

                        info_note = format_info_note(notes)
                        if info_note:
                            desc_parts.append(info_note)

                        desc_parts.append(f"Område: {display_area(sp.area)}")
                        desc_parts.append(f"Kilde: {source_url_for_notes}")

                        if img:
                            desc_parts.append(f"Billede: {img}")

                        if local_map_image_url:
                            desc_parts.append(f"Regionskort: {local_map_image_url}")

                        uid = f"jagttid-{season_year}-spec-{uid_counter}@luka2945"
                        events.append(
                            build_event(
                                uid=uid,
                                summary=f"{sp.species} - Lokal jagttid {abbr}".strip(),
                                start=d,
                                end_inclusive=d,
                                description="\n".join(desc_parts),
                            )
                        )

                if emit_no_hunting:
                    for nh in no_hunting:
                        if not area_allowed(nh.area, include_area, exclude_area):
                            continue

                        general_list = find_general_duration_ranges_for_species(
                            nh.species,
                            general_by_species
                        )

                        if not general_list:
                            print(f"WARNING: Ingen generel jagttid fundet for lokal ingen jagttid: {nh.species} / {nh.area}")
                            continue

                        for gr in general_list:
                            uid_counter += 1

                            meta = get_species_meta(nh.species, species_meta)
                            img = meta.get("image_url", "")
                            abbr = region_abbr(nh.area)

                            desc_parts = []
                            desc_parts.append(f"Info: Der er ikke jagttid på {nh.species}.")
                            desc_parts.append(f"Område: {display_area(nh.area)}")
                            desc_parts.append("Lokal regel: ingen jagttid")
                            desc_parts.append("Varighed hentet fra generel jagttid for samme dyr")
                            desc_parts.append(f"Kilde: {source_url_for_notes}")

                            if img:
                                desc_parts.append(f"Billede: {img}")

                            if local_map_image_url:
                                desc_parts.append(f"Regionskort: {local_map_image_url}")

                            uid = f"jagttid-{season_year}-nohunt-{uid_counter}@luka2945"
                            events.append(
                                build_event(
                                    uid=uid,
                                    summary=f"{nh.species} - Lokal - Ingen jagttid {abbr}".strip(),
                                    start=gr.start,
                                    end_inclusive=gr.end_inclusive,
                                    description="\n".join(desc_parts),
                                )
                            )

        ics = build_calendar(cal_name, events)
        out_path = OUT_DIR / out_name
        out_path.write_text(ics, encoding="utf-8")

        print(f"Wrote: {out_path} events={len(events)}")


if __name__ == "__main__":
    main()
