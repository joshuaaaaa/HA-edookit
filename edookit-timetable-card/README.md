# Edookit rozvrh – karta pro Lovelace

Samostatná karta, která zobrazuje rozvrh z integrace **Edookit**. Je to jeden soubor bez
závislostí: [`edookit-timetable-card.js`](edookit-timetable-card.js).

Integrace kartu **neregistruje** – Home Assistant (HACS) neumí z jednoho repozitáře
nainstalovat integraci i kartu, proto se karta přidává ručně.

![Karta rozvrhu](../docs/card-preview.png)

## Instalace (ručně)

1. Zkopírujte `edookit-timetable-card.js` do `/config/www/` (např. přes File editor / Samba).
2. **Nastavení → Ovládací panely → ⋮ → Zdroje → Přidat zdroj**
   * URL: `/local/edookit-timetable-card.js`
   * Typ: **JavaScript modul**
3. Obnovte stránku prohlížeče (Ctrl+F5).

Po aktualizaci souboru změňte URL zdroje, např. `/local/edookit-timetable-card.js?v=2`,
aby prohlížeč nenačítal starou verzi z cache.

## Použití

V dashboardu: **Přidat kartu → Edookit rozvrh**, nebo YAML:

```yaml
type: custom:edookit-timetable-card
entity: sensor.edookit_jan_novak_rozvrh
```

| Volba | Výchozí | Popis |
|---|---|---|
| `entity` | – | senzor **Rozvrh** z integrace Edookit (povinné) |
| `title` | jméno žáka + třída | nadpis (`""` = bez nadpisu) |
| `view` | `week` | `week` = týdenní mřížka jako na portálu (v úzkém sloupci se posouvá do strany), `day` = seznam na jeden den (s učivem), `auto` = pod 400 px seznam, jinak týden |
| `show_teacher` | `true` | zkratka vyučujícího vlevo dole |
| `show_room` | `true` | učebna vpravo dole |
| `show_exams` | `true` | zelené štítky písemek („Pís. - …“) |
| `show_times` | `true` | časy hodin v záhlaví |
| `show_header` | `true` | jméno, třída a přepínání týdnů |
| `show_footer` | `true` | čas poslední aktualizace |
| `short_names` | `true` | velké zkratky předmětů jako na portálu (`false` = celé názvy) |
| `highlight_now` | `true` | zvýraznit probíhající hodinu |
| `colorize` | `false` | obarvit hodiny podle předmětu místo šedých políček portálu |
| `subject_colors` | – | vlastní barvy pro `colorize`, např. `{Matematika: "#7e57c2"}` |
| `next_week_from` | `friday_after_school` | kdy přepnout na další týden: `friday_after_school`, `saturday`, `never` |

Vzhled odpovídá „Rozvrhu žáků“ na portálu: zkratka předmětu uprostřed, vyučující a učebna dole,
**Zrušeno** / **Událost** oranžově s přeškrtnutým předmětem, změna (např. jiná učebna) oranžově,
písemky jako zelený štítek a akce školy (svátek, výlet) jako fialový pruh pod dnem.
Šipkami ← → se přepínají týdny (resp. dny), odkaz „Aktuální týden“ vrací na dnešek, kliknutím na
jméno otevřete detail entity. Po najetí myší se ukáže detail hodiny včetně učiva.

Karta jen zobrazuje data senzoru – rozvrh stahuje integrace jednou denně v nastavený čas.
