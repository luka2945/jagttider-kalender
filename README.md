# 🦆 Jagttider → Automatisk kalender

![Sidst opdateret](https://img.shields.io/github/actions/workflow/status/luka2945/jagttider-kalender/update-jagttider.yml?label=Sidst%20opdateret&style=for-the-badge)
![Repository size](https://img.shields.io/github/repo-size/luka2945/jagttider-kalender?style=for-the-badge)
![Last commit](https://img.shields.io/github/last-commit/luka2945/jagttider-kalender?style=for-the-badge)
![Status](https://img.shields.io/badge/Project-Active-brightgreen?style=for-the-badge)

Automatisk opdaterede `.ics` kalendere med jagttider fra **Danmarks Jægerforbund** — klar til Apple Kalender, Google Kalender, Outlook og andre kalenderapps.

Kalenderne opdateres automatisk via **GitHub Actions**, så ændringer i jagttiderne bliver slået igennem i de abonnementerede kalendere.

---

## 📱 Brug kalenderen med det samme

Vælg en kalender herunder og abonnér på den i din kalenderapp.

### 📅 Generele jagttider
[Generel kalender](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-generel.ics) ↔️ 
https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-generel.ics

### 📅 Lokale jagttider. Region kalendere
- [Region Hovedstaden](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-hovedstaden.ics) ↔️ 
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-hovedstaden.ics
  
- [Region Sjælland](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-sjaelland.ics) ↔️ 
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-sjaelland.ics
  
- [Region Syddanmark](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-syddanmark.ics) ↔️ 
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-syddanmark.ics
  
- [Region Midtjylland](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-midtjylland.ics) ↔️ 
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-midtjylland.ics
  
- [Region Nordjylland](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-nordjylland.ics) ↔️ 
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-nordjylland.ics

### 📅 Lokale jagttider. Kun fastland, kalender
- [Alle regioner minus øerne](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-alle-regioner-minus-oerne.ics) ↔️ 
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-alle-regioner-minus-oerne.ics

---

## ➡️ Sådan tilføjer du kalenderen

### Apple Kalender (iPhone / iPad)
1. Åbn **Kalender**
2. Tryk **Kalendere**
3. Tryk **Tilføj kalender**
4. Vælg **Tilføj abonnementskalender**
5. Indsæt linket til den ønskede `.ics`
6. Sæt opdatering til **Auto**

### Google Kalender
1. Åbn Google Kalender
2. Vælg **Add calendar**
3. Vælg **From URL**
4. Indsæt `.ics` linket

### Outlook
1. Vælg **Add calendar**
2. Vælg **Subscribe from web**
3. Indsæt `.ics` linket

---

## 📃 Hvad indeholder kalenderne?

### Generelle jagttider
Eksempel:
```text
Gråand - Jagttid
```

### Lokale jagttider
Eksempel:
```text
Hare - Lokal jagttid RM
```

### Lokal ingen jagttid
Eksempel:
```text
Hare - Lokal ingen jagttid RM
```

### Region forkortelser

| Region | Forkortelse |
|------|------|
Region Hovedstaden | RH  
Region Sjælland | RSj  
Region Syddanmark | RSy  
Region Midtjylland | RM  
Region Nordjylland | RN

---

## 📝 Hvad står der i event-noterne?

Et event kan indeholde:
- område
- kilde
- billede-link
- regionskort
- ekstra note om arten

Eksempel:
```text
Område: Øen Endelave Region Midtjylland
Kilde: ...
Billede: ...
Regionskort: ...
```

---

## 📂 Hvor ligger kalenderfilerne?

Alle genererede kalendere ligger i:

```text
Jagttids-Kalender/
```

Eksempel filer:
```text
jagttider-generel.ics
jagttider-lokalt-region-hovedstaden.ics
jagttider-lokalt-region-sjaelland.ics
jagttider-lokalt-region-syddanmark.ics
jagttider-lokalt-region-midtjylland.ics
jagttider-lokalt-region-nordjylland.ics
jagttider-lokalt-alle-regioner-minus-oerne.ics
```

---

## ⚙️ Automatisk opdatering

Kalenderne opdateres via GitHub Actions.

Workflow:
```text
Update Jagttider ICS
```

Workflowet:
1. henter jagttider fra Jægerforbundet
2. genererer nye `.ics` filer
3. opdaterer repository hvis noget har ændret sig

Hvis du vil køre det manuelt:
1. Gå til fanen **Actions**
2. Vælg **Update Jagttider ICS**
3. Klik **Run workflow**

---

## ⚙️ Konfiguration

Projektets konfiguration ligger i:

```text
configs/
```

### `configs/master.json`
Indeholder hovedopsætningen, fx:
- `general_url`
- `local_url`
- `user_agent`
- `seasons_ahead`
- `local_map_image_url`
- `species_meta`

### `species_meta`
Her kan du tilføje billeder og noter til arter.

Eksempel:
```json
"gråkrage": {
  "image_url": "",
  "notes": ""
}
```

Felter:
- `image_url` = billede af dyret
- `notes` = ekstra tekst om arten

Hvis en art ikke findes i listen, fungerer kalenderen stadig — den får bare ikke ekstra note eller billede-link.

### `configs/calendars/*.json`
Hver fil genererer én kalender.

Vigtige felter:
- `calendar_name`
- `output_filename`
- `use_local`
- `filters.include_area_keywords`
- `filters.exclude_area_keywords`
- `local_rules.emit_no_hunting_events`
- `seasons_ahead`

---

## 🔍 Se koden bagved

Hvis du vil se hvordan det hele virker, kan du gå direkte til repositoryet her:

[Åbn koden på GitHub](https://github.com/luka2945/jagttider-kalender)

Du kan også gå direkte til min GitHub profil her:

[GitHub profil – luka2945](https://github.com/luka2945)

---

## 📜 Datakilder

Generelle jagttider:  
https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/

Lokale jagttider:  
https://www.jaegerforbundet.dk/jagt/regler-og-sikkerhed/jagttider/lokale-jagttider/

---

## ⚠️ Disclaimer

Kalenderen er et hjælpemiddel.

Den officielle kilde er altid **Danmarks Jægerforbund**.  
Tjek altid gældende regler før jagt.

---

## 👤 Lavet af

**Lavet af Lukas Jermiin**

- [GitHub profil](https://github.com/luka2945)
- [Se projektet](https://github.com/luka2945/jagttider-kalender)
