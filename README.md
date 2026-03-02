# Jagttider → ICS (auto-opdateret kalender)

Dette repo genererer `.ics`-kalendere med jagttider fra Danmarks Jægerforbund og committer dem automatisk, så du kan abonnere på dem i Apple Kalender, Google Kalender, Outlook m.m.

## Hvad genereres?
Der bliver genereret flere kalendere (filer) ud fra configs:

- **Jagttider – Generel** (alle generelle jagttider)
- **Jagttider – Lokalt (Personlig)** (lokale jagttider, med dine fravalg)
- **Jagttider – Lokalt (Region …)** (en kalender pr. region)
- **Jagttider – Lokalt (Alle regioner, minus øerne)**

Alle `.ics`-filer bliver lagt i mappen: `Jagttids-Kalender/`

---

## Auto-opdatering (GitHub Actions)
Workflowet kører automatisk (schedule) og kan også køres manuelt:

1. Gå til fanen **Actions**
2. Vælg **Update Jagttider ICS**
3. Klik **Run workflow**

Hvis der er ændringer på Danmarks Jægerforbunds sider, bliver `.ics`-filerne opdateret og committet.

---

## Abonnér på kalenderen (auto-opdater i din kalender-app)

Du skal bruge **RAW-linket** til `.ics` filen:

**Format:**




**Eksempler:**
- `.../Jagttids-Kalender/jagttider-generel.ics`
- `.../Jagttids-Kalender/jagttider-lokalt-personlig.ics`

### Apple Kalender (iPhone/iPad)
1. Åbn **Kalender**
2. Tryk **Kalendere**
3. Tryk **Tilføj kalender** → **Tilføj abonnementskalender**
4. Indsæt RAW-linket
5. Sæt opdatering til **Auto**

### Google Kalender / Outlook
De kan også abonnere via URL (ofte under “Add calendar by URL” / “Subscribe from URL”).

> Bemærk: Opdateringsinterval styres af kalender-appen (nogle opdaterer sjældent). Apple “Auto” er typisk fint.

---

## Konfiguration

### 1) `configs/master.json`
Her ligger “database”-delen:

- `attachments.local_map_image_url`  
  Et billede/URL med Danmark/region-kort som vedhæftes alle lokale events (valgfrit).

- `species`  
  Her kan du (valgfrit) definere:
  - `image_url` (billede af dyret)
  - `shooting_time_note` (tekst som “01:30 før solopgang …”)
  - `aliases` (alternative navne)

Hvis en art ikke findes i `master.json`, virker kalenderen stadig – den får bare ikke dyrebillede/skydetid-tekst.

### 2) `configs/calendars/*.json`
Hver kalender har samme schema:

- `use_local`  
  - `false` = generelle jagttider
  - `true` = lokale jagttider

- `filters.include_area_keywords` / `filters.exclude_area_keywords`  
  Tekstfiltre på område (region/kommune/ø).  
  Tom liste = ingen filter.

- `local_rules.emit_no_hunting_events` (kun relevant når `use_local: true`)
  - `true` = “ingen jagttid” bliver til events (klippet til samme periode som den generelle jagttid for arten)
  - `false` = “ingen jagttid” ignoreres

- `seasons_ahead`  
  Hvor mange jagtsæsoner frem der genereres (1, 2, 3…)

---

## Hvordan tolkes tiderne?
- Generelle og lokale jagttider bliver lavet som **heldags-events** fra startdato til slutdato.
- “Særlige lørdagsregler” (fx “1. og 2. lørdag i november”) bliver lavet som enkelt-dags-events på de konkrete lørdage.
- “Ingen jagttid” (lokale tider) kan laves som events hvis `emit_no_hunting_events: true` og bliver **ikke** sat til “hele året” – de bliver klippet til den generelle jagttidsperiode for arten.

---

## Output
Alle `.ics` genereres til:
- `Jagttids-Kalender/*.ics`

Hvis du ikke kan se filer blive committet, tjek at din `.gitignore` ikke blokerer `.ics`.

---

## Kilder
- Generelle jagttider: https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/
- Lokale jagttider: https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/lokale-jagttider/
