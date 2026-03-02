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

OUT_DIR = Path("Jagttids-Kalender")
MASTER_PATH = Path("configs/master.json")
CAL_DIR = Path("configs/calendars")

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

MONTHS_DA = {
    "januar": 1, "februar": 2, "marts": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "august": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}


@dataclass
class LocalRow:
    area: str         # "Region X | AreaY"
    species: str
    rule_text: str


def clean(s: str) -> str:
    s = (s or "").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", s).strip()


def fetch_html(url: str) -> str:
    r = requests.get(url, headers=BROWSER_HEADERS, timeout=45)
    r.raise_for_status()
    return r.text


def ics_escape(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("\n", "\\n")
    text = text.replace(",", "\\,")
    text = text.replace(";", "\\;")
    return text


def fold_ics_line(line: str, limit: int = 75) -> str:
    if len(line) <= limit:
        return line
    parts = []
    while len(line) > limit:
        parts.append(line[:limit])
        line = " " + line[limit:]
    parts.append(line)
    return "\r\n".join(parts)


def dtstamp_utc() -> str:
    return datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")


def base_season_year(today: Optional[date] = None) -> int:
    today = today or date.today()
    # If Jan/Feb, still in the season that started last year
    if today.month in (1, 2):
        return today.year - 1
    return today.year


def season_years_from_cfg(cfg: dict) -> List[int]:
    n = int(cfg.get("seasons_ahead", 1))
    n = max(1, min(n, 10))
    start = base_season_year()
    return [start + i for i in range(n)]


def parse_ranges_ddmm(text: str) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
    t = clean(text).lower().replace("–", "-").replace("—", "-")
    parts = [p.strip() for p in re.split(r"\bog\b", t) if p.strip()]
    out = []
    for p in parts:
        m = re.search(r"(\d{1,2})\.(\d{1,2})\s*-\s*(\d{1,2})\.(\d{1,2})", p)
        if not m:
            continue
        sd, sm, ed, em = map(int, m.groups())
        out.append(((sd, sm), (ed, em)))
    return out


def safe_date(y: int, m: int, d: int) -> date:
    while True:
        try:
            return date(y, m, d)
        except ValueError:
            d -= 1
            if d < 1:
                return date(y, m, 1)


def concrete_range(season_y: int, start_dm: Tuple[int, int], end_dm: Tuple[int, int]) -> Tuple[date, date]:
    sd, sm = start_dm
    ed, em = end_dm
    start = safe_date(season_y, sm, sd)
    end_year = season_y if em >= sm else season_y + 1
    end = safe_date(end_year, em, ed)
    return start, end


def all_saturdays(year: int, month: int) -> List[date]:
    d = date(year, month, 1)
    while d.weekday() != 5:
        d += timedelta(days=1)
    res = []
    while d.month == month:
        res.append(d)
        d += timedelta(days=7)
    return res


def nth_saturdays(year: int, month: int, nths: List[int]) -> List[date]:
    sats = all_saturdays(year, month)
    out = []
    for n in nths:
        if 1 <= n <= len(sats):
            out.append(sats[n-1])
    return out


def resolve_saturday_text_to_dates(raw: str, season_y: int) -> List[date]:
    t = clean(raw).lower()
    if "lørdag" not in t:
        return []

    chunks = re.split(r",|\bsamt\b", t)
    dates: List[date] = []

    for c in chunks:
        c = c.strip()
        if not c:
            continue

        m_all = re.search(r"alle\s+lørdage\s+i\s+([a-zæøå]+)", c)
        if m_all:
            mon = MONTHS_DA.get(m_all.group(1))
            if mon:
                dates.extend(all_saturdays(season_y, mon))
            continue

        m_pair = re.search(r"(\d+)\.\s*og\s*(\d+)\.\s*lørdag\s+i\s+([a-zæøå]+)", c)
        if m_pair:
            n1, n2 = int(m_pair.group(1)), int(m_pair.group(2))
            mon = MONTHS_DA.get(m_pair.group(3))
            if mon:
                dates.extend(nth_saturdays(season_y, mon, [n1, n2]))
            continue

        m_one = re.search(r"(\d+)\.\s*lørdag\s+i\s+([a-zæøå]+)", c)
        if m_one:
            n1 = int(m_one.group(1))
            mon = MONTHS_DA.get(m_one.group(2))
            if mon:
                dates.extend(nth_saturdays(season_y, mon, [n1]))
            continue

    return sorted(set(dates))


def looks_blocked(html: str) -> bool:
    h = html.lower()
    if any(x in h for x in ["captcha", "access denied", "cookie", "consent"]) and ("jagttid" not in h):
        return True
    return False


def parse_general(html: str) -> Dict[str, List[Tuple[Tuple[int, int], Tuple[int, int]]]]:
    """
    species_lower -> list of dd.mm-dd.mm ranges
    """
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n")
    lines = [clean(x) for x in text.split("\n") if clean(x)]

    out: Dict[str, List[Tuple[Tuple[int, int], Tuple[int, int]]]] = {}

    for l in lines:
        ll = l.lower()
        if "se lokal" in ll:
            continue
        if not re.search(r"\d{1,2}\.\d{1,2}\s*-\s*\d{1,2}\.\d{1,2}", ll):
            continue

        m = re.search(r"(\d{1,2}\.\d{1,2}\s*-\s*\d{1,2}\.\d{1,2}.*)$", ll)
        if not m:
            continue

        rule_part = l[m.start():].strip()
        sp_part = l[:m.start()].strip().rstrip("*").strip()
        if not sp_part:
            continue

        dm = parse_ranges_ddmm(rule_part)
        if not dm:
            continue

        out.setdefault(sp_part.lower(), []).extend(dm)

    # de-dup
    for k in list(out.keys()):
        seen = set()
        uniq = []
        for a, b in out[k]:
            key = (a, b)
            if key not in seen:
                seen.add(key)
                uniq.append((a, b))
        out[k] = uniq

    return out


def parse_local(html: str) -> List[LocalRow]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text("\n")
    lines = [clean(x) for x in text.split("\n") if clean(x)]

    rows: List[LocalRow] = []
    current_region: Optional[str] = None
    current_area: Optional[str] = None

    for l in lines:
        ll = l.lower()

        m_reg = re.match(r"^region\s+(.+?)\s*-\s*lokale\s+jagttider", ll)
        if m_reg:
            current_region = "Region " + clean(m_reg.group(1)).title()
            current_area = None
            continue

        if current_region:
            if l == "Hele regionen":
                current_area = current_region
                continue
            if ll.startswith("øen "):
                current_area = l
                continue
            if ll.endswith(" kommune"):
                current_area = l
                continue

        if not current_region or not current_area:
            continue

        if not (re.search(r"\d{1,2}\.\d{1,2}\s*-\s*\d{1,2}\.\d{1,2}", ll) or "ingen jagttid" in ll or "lørdag" in ll):
            continue

        m = re.search(r"(\d{1,2}\.\d{1,2}\s*-\s*\d{1,2}\.\d{1,2}.*|ingen jagttid.*|.*lørdag.*)$", ll)
        if not m:
            continue

        rule_part = l[m.start():].strip()
        species_part = l[:m.start()].strip().rstrip("*").strip()
        if not species_part:
            continue

        area_full = f"{current_region} | {current_area}"
        rows.append(LocalRow(area=area_full, species=species_part, rule_text=rule_part))

    # de-dup
    seen = set()
    out = []
    for r in rows:
        k = (r.area.lower(), r.species.lower(), r.rule_text.lower())
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out


def contains_any(haystack: str, keywords: List[str]) -> bool:
    h = (haystack or "").lower()
    for k in keywords or []:
        k = (k or "").lower().strip()
        if k and k in h:
            return True
    return False


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def species_meta(master: dict, species: str) -> dict:
    db = master.get("species", {}) or {}
    if species in db:
        return db[species]
    # alias match
    for _, meta in db.items():
        for a in meta.get("aliases", []) or []:
            if a.lower() == species.lower():
                return meta
    # case-insensitive direct match
    for name, meta in db.items():
        if name.lower() == species.lower():
            return meta
    return {}


def make_description(skydetid: Optional[str], periode: str, extra: List[str], source: str) -> str:
    lines = []
    if skydetid:
        lines.append(f"Skydetid: {skydetid}")
    lines.append(f"Periode: {periode}")
    if extra:
        lines.append("")
        lines.extend(extra)
    lines.append("")
    lines.append(f"Kilde: {source}")
    return "\n".join(lines).strip()


def vevent(summary: str, start: date, end_inclusive: date, description: str, attachments: List[str]) -> str:
    uid = str(uuid.uuid4())
    dtstart = start.strftime("%Y%m%d")
    dtend = (end_inclusive + timedelta(days=1)).strftime("%Y%m%d")

    lines = [
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp_utc()}",
        f"SUMMARY:{ics_escape(summary)}",
        f"DTSTART;VALUE=DATE:{dtstart}",
        f"DTEND;VALUE=DATE:{dtend}",
        f"DESCRIPTION:{ics_escape(description)}",
    ]
    for url in attachments:
        if url:
            lines.append(f"ATTACH:{ics_escape(url)}")
    lines.append("END:VEVENT")

    return "\r\n".join(fold_ics_line(x) for x in lines)


def write_ics(path: Path, calname: str, events: List[str]) -> None:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//luka2945//Jagttider ICS v2.1//DA",
        "CALSCALE:GREGORIAN",
        f"X-WR-CALNAME:{ics_escape(calname)}",
        *events,
        "END:VCALENDAR",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\r\n".join(lines), encoding="utf-8")


def main():
    if not MASTER_PATH.exists():
        raise RuntimeError("Mangler configs/master.json")
    if not CAL_DIR.exists():
        raise RuntimeError("Mangler configs/calendars/")

    master = load_json(MASTER_PATH)
    map_url = (master.get("attachments", {}) or {}).get("local_map_image_url", "")

    general_html = fetch_html(GENERAL_URL)
    local_html = fetch_html(LOCAL_URL)

    if looks_blocked(general_html) or looks_blocked(local_html):
        raise RuntimeError("Det ligner cookie/blocked HTML fra siden. (Prøv igen – eller vi justerer headers yderligere.)")

    general_map = parse_general(general_html)
    local_rows = parse_local(local_html)

    print(f"Parsed general species: {len(general_map)}")
    print(f"Parsed local rows: {len(local_rows)}")

    if len(general_map) == 0:
        raise RuntimeError("0 general parsed – HTML har ændret sig eller vi får forkert side.")
    if len(local_rows) == 0:
        raise RuntimeError("0 local parsed – HTML har ændret sig eller vi får forkert side.")

    cal_files = sorted(CAL_DIR.glob("*.json"))
    if not cal_files:
        raise RuntimeError("Ingen configs i configs/calendars/")

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for cfg_path in cal_files:
        cfg = load_json(cfg_path)

        cal_name = cfg["calendar_name"]
        out_name = cfg["output_filename"]
        use_local = bool(cfg.get("use_local", False))
        seasons = season_years_from_cfg(cfg)

        filters = cfg.get("filters", {}) or {}
        inc_ar = filters.get("include_area_keywords", []) or []
        exc_ar = filters.get("exclude_area_keywords", []) or []

        local_rules = cfg.get("local_rules", {}) or {}
        emit_no_hunting = bool(local_rules.get("emit_no_hunting_events", False))

        events: List[str] = []

        if not use_local:
            # GENEREL kalender: alle arter fra general_map
            for sp_lower, dm_ranges in general_map.items():
                sp_name = sp_lower[:1].upper() + sp_lower[1:]

                meta = species_meta(master, sp_name)
                skyd = meta.get("shooting_time_note")

                attachments: List[str] = []
                img = meta.get("image_url", "")
                if img:
                    attachments.append(img)

                for sy in seasons:
                    for start_dm, end_dm in dm_ranges:
                        s, e = concrete_range(sy, start_dm, end_dm)
                        periode_txt = f"{start_dm[0]:02d}.{start_dm[1]:02d} til {end_dm[0]:02d}.{end_dm[1]:02d}"
                        desc = make_description(skyd, periode_txt, [], GENERAL_URL)
                        summary = f"{sp_name} – Generel jagttid"
                        events.append(vevent(summary, s, e, desc, attachments))

        else:
            # LOKAL kalender: filtrer kun på område
            for row in local_rows:
                area_full = row.area  # "Region X | AreaY"

                if inc_ar and not contains_any(area_full, inc_ar):
                    continue
                if exc_ar and contains_any(area_full, exc_ar):
                    continue

                sp = row.species
                meta = species_meta(master, sp)
                skyd = meta.get("shooting_time_note")

                attachments: List[str] = []
                if map_url:
                    attachments.append(map_url)
                img = meta.get("image_url", "")
                if img:
                    attachments.append(img)

                # nice label: show area part after "|"
                area_label = area_full
                if " | " in area_full:
                    _, a = area_full.split(" | ", 1)
                    area_label = a.strip()

                rule_text_l = row.rule_text.lower()

                # "ingen jagttid" -> event clipped to general seasons for that species
                if "ingen jagttid" in rule_text_l:
                    if not emit_no_hunting:
                        continue

                    gen_dm = general_map.get(sp.lower())
                    if not gen_dm:
                        # can't clip -> skip to avoid whole-year spam
                        continue

                    for sy in seasons:
                        for start_dm, end_dm in gen_dm:
                            s, e = concrete_range(sy, start_dm, end_dm)
                            periode_txt = f"{start_dm[0]:02d}.{start_dm[1]:02d} til {end_dm[0]:02d}.{end_dm[1]:02d}"
                            extra = [f"Lokalområde: {area_label}", "Bemærkning: Ingen jagttid i dette område i perioden."]
                            desc = make_description(None, periode_txt, extra, LOCAL_URL)
                            summary = f"{sp} – Ingen jagttid ({area_label})"
                            events.append(vevent(summary, s, e, desc, attachments))
                    continue

                # Saturday rules
                if "lørdag" in rule_text_l:
                    for sy in seasons:
                        dates = resolve_saturday_text_to_dates(row.rule_text, sy)
                        if not dates:
                            continue
                        # Description format: keep rule in Periode if it's not dd.mm
                        extra = [f"Lokalområde: {area_label}", f"Regel: {row.rule_text}"]
                        desc = make_description(skyd, row.rule_text, extra, LOCAL_URL)
                        summary = f"{sp} – Lokal jagttid ({area_label})"
                        for d in dates:
                            events.append(vevent(summary, d, d, desc, attachments))
                    continue

                # Normal dd.mm-dd.mm
                dm_ranges = parse_ranges_ddmm(row.rule_text)
                if not dm_ranges:
                    continue

                for sy in seasons:
                    for start_dm, end_dm in dm_ranges:
                        s, e = concrete_range(sy, start_dm, end_dm)
                        periode_txt = f"{start_dm[0]:02d}.{start_dm[1]:02d} til {end_dm[0]:02d}.{end_dm[1]:02d}"
                        extra = [f"Lokalområde: {area_label}"]
                        desc = make_description(skyd, periode_txt, extra, LOCAL_URL)
                        summary = f"{sp} – Lokal jagttid ({area_label})"
                        events.append(vevent(summary, s, e, desc, attachments))

        out_path = OUT_DIR / out_name
        write_ics(out_path, cal_name, events)
        print(f"Wrote {out_path} with {len(events)} events")

    print("Done. Generated .ics files.")


if __name__ == "__main__":
    main()
