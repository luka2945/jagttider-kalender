# 🦆 Jagttider → Automatisk kalender

![Sidst opdateret](https://img.shields.io/github/actions/workflow/status/luka2945/jagttider-kalender/update-jagttider.yml?label=Sidst%20opdateret\&style=for-the-badge)
![Repository size](https://img.shields.io/github/repo-size/luka2945/jagttider-kalender?style=for-the-badge)
![Last commit](https://img.shields.io/github/last-commit/luka2945/jagttider-kalender?style=for-the-badge)
![Status](https://img.shields.io/badge/Project-Active-brightgreen?style=for-the-badge)

Automatisk opdaterede `.ics` kalendere med jagttider fra **Retsinformation** — klar til Apple Kalender, Google Kalender, Outlook og andre kalenderapps.

Kalenderne opdateres automatisk via **GitHub Actions**, så ændringer i jagttiderne bliver slået igennem i de abonnementerede kalendere.

---

## 📱 Brug kalenderen med det samme

Vælg en kalender herunder og abonnér på den i din kalenderapp.

### 📅 Generelle jagttider

[Generel kalender](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-generel.ics) ↔️
https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-generel.ics

### 📅 Lokale jagttider. Alle lokale regler

* [Alt lokalt](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-alt-lokalt.ics) ↔️
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-alt-lokalt.ics

### 📅 Lokale jagttider. Region kalendere

* [Region Hovedstaden](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-hovedstaden.ics) ↔️
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-hovedstaden.ics

* [Region Sjælland](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-sjaelland.ics) ↔️
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-sjaelland.ics

* [Region Syddanmark](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-syddanmark.ics) ↔️
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-syddanmark.ics

* [Region Midtjylland](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-midtjylland.ics) ↔️
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-midtjylland.ics

* [Region Nordjylland](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-nordjylland.ics) ↔️
  https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-region-nordjylland.ics

### 📅 Lokale jagttider. Kun fastland, kalender

* [Alle regioner minus øerne](https://raw.githubusercontent.com/luka2945/jagttider-kalender/main/Jagttids-Kalender/jagttider-lokalt-alle-regioner-minus-oerne.ics) ↔️
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

Hvis der lokalt ikke er jagttid på en art, kan kalenderen vise det som et event.

Eksempel:

```text
Agerhøne - Lokal - Ingen jagttid RM
```

### Region forkortelser

| Region             | Forkortelse |
| ------------------ | ----------- |
| Region Hovedstaden | RH          |
| Region Sjælland    | RSj         |
| Region Syddanmark  | RSy         |
| Region Midtjylland | RM          |
| Region Nordjylland | RN          |

---

## 📝 Hvad står der i event-noterne?

Et event kan indeholde:

* info om arten
* område
* kilde
* billede-link
* regionskort
* ekstra note om arten

Eksempel på normal jagttid:

```text
Info: Gråand må jages 01:30 time før solopgang til 01:30 time efter solnedgang.
Kilde: https://www.retsinformation.dk/eli/lta/2024/470
Billede: ...
```

Eksempel på lokal jagttid:

```text
Område: Øen Endelave Region Midtjylland
Kilde: https://www.retsinformation.dk/eli/lta/2024/470
Billede: ...
Regionskort: ...
```

Eksempel på lokal ingen jagttid:

```text
Info: Der er ikke jagttid på Agerhøne.
Område: Øen Endelave Region Midtjylland
Lokal regel: ingen jagttid
Varighed hentet fra generel jagttid for samme dyr
Kilde: https://www.retsinformation.dk/eli/lta/2024/470
Billede: ...
Regionskort: ...
```

Ved events med **lokal ingen jagttid** bliver artens normale note ikke tilføjet, så der ikke står modstridende tekst som fx at dyret både må jages og ikke må jages.

---

## 📂 Hvor ligger kalenderfilerne?

Alle genererede kalendere ligger i:

```text
Jagttids-Kalender/
```

Eksempel filer:

```text
jagttider-generel.ics
jagttider-alt-lokalt.ics
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

1. henter jagttider fra Retsinformation
2. læser Bilag 1, 2, 3 og 4
3. genererer nye `.ics` filer
4. opdaterer repository hvis noget har ændret sig

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

* `retsinformation_url`
* `user_agent`
* `seasons_ahead`
* `local_map_image_url`
* `species_meta`

Eksempel:

```json
{
  "retsinformation_url": "https://www.retsinformation.dk/eli/lta/2024/470",
  "user_agent": "Mozilla/5.0 (JagttiderICSBot; +https://github.com/luka2945/jagttider-kalender)",
  "seasons_ahead": 4,
  "local_map_image_url": "",
  "species_meta": {}
}
```

### `species_meta`

Her kan du tilføje billeder og noter til arter.

Eksempel:

```json
"gråand": {
  "image_url": "",
  "notes": "Gråand må jages 01:30 time før solopgang til 01:30 time efter solnedgang."
}
```

Felter:

* `image_url` = billede af dyret
* `notes` = ekstra tekst om arten

Hvis `notes` er udfyldt, tilføjes teksten automatisk med `Info:` foran.

Hvis en art ikke findes i listen, fungerer kalenderen stadig — den får bare ikke ekstra note eller billede-link.

### `configs/calendars/*.json`

Hver fil genererer én kalender.

Vigtige felter:

* `calendar_name`
* `output_filename`
* `use_local`
* `filters.include_area_keywords`
* `filters.exclude_area_keywords`
* `local_rules.emit_no_hunting_events`
* `seasons_ahead`

---

## 🗂️ Eksempel på kalender-config

```json
{
  "calendar_name": "Jagttider - Lokalt (Region Midtjylland)",
  "output_filename": "jagttider-lokalt-region-midtjylland.ics",
  "use_local": true,

  "filters": {
    "include_area_keywords": [
      "Region Midtjylland"
    ],
    "exclude_area_keywords": []
  },

  "local_rules": {
    "emit_no_hunting_events": true
  },

  "seasons_ahead": 4
}
```

---

## 🔍 Se koden bagved

Hvis du vil se hvordan det hele virker, kan du gå direkte til repositoryet her:

[Åbn koden på GitHub](https://github.com/luka2945/jagttider-kalender)

Du kan også gå direkte til min GitHub profil her:

[GitHub profil – luka2945](https://github.com/luka2945)

---

## 📜 Datakilde

Kalenderen bruger Retsinformation som datakilde til jagttider:

https://www.retsinformation.dk/eli/lta/2024/470

Parseren bruger især:

Bilag 1 - generelle jagttider
Bilag 2 - lokale jagttider
Bilag 3 - lokale jagttider for kronvildt
Bilag 4 - lokale jagttider for dåvildt

Billederne, der bruges som billede-links i kalendernoterne, hentes fra mit andet GitHub-projekt, hvor mine egne AI-genererede artsillustrationer ligger:

https://luka2945.github.io/Billede/

Billederne er lavet som vejledende artsillustrationer til projektet og er ikke officielle artsbilleder.

---

## ⚠️ Disclaimer

Kalenderen er et hjælpemiddel.

Den officielle kilde er altid **Retsinformation** og den gældende bekendtgørelse om jagttider.
Tjek altid gældende regler før jagt.

---

## 👤 Lavet af

**Lavet af Lukas Jermiin**

* [GitHub profil](https://github.com/luka2945)
* [Se projektet](https://github.com/luka2945/jagttider-kalender)
