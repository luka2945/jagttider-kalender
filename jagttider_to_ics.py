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

        # Region heading
        if is_region_heading(txt):
            current_region = re.sub(r"\s*-\s*lokale jagttider\s*$", "", txt, flags=re.I).strip()
            current_subarea = current_region
            pending_species = ""
            continue

        if not current_region:
            continue

        # Underområde
        if is_subarea_heading(txt):
            if "hele regionen" in low:
                current_subarea = current_region
            else:
                current_subarea = txt
            pending_species = ""
            continue

        # Hvis linjen KUN er en art, gem den og vent på næste linje
        species_only, rule_only = split_species_and_rule(txt)
        if species_only and rule_only:
            # art og regel på samme linje
            species = species_only
            rule_text = rule_only
        else:
            # mulig art-linje alene
            if not RANGE_RE.search(txt.replace("–", "-")) and "ingen jagttid" not in low and "lørdag" not in low:
                pending_species = clean_species_name(txt)
                continue

            # hvis vi kommer hertil og har en pending art, så er denne linje reglen
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

        # Ingen jagttid
        if "ingen jagttid" in rule_low:
            no_hunting.append(NoHuntingMarker(species=species, area=area))
            continue

        # Normale dato-intervaller
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

        # Særlige lørdage
        special_dates = parse_special_text_to_dates(rule_text, season_year)
        if special_dates:
            specials.append(
                SpecialDayRule(
                    species=species,
                    area=area,
                    dates=tuple(special_dates)
                )
            )

    # dedup
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
