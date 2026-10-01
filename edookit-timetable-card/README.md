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
| `title` | jméno žáka | nadpis (`""` = bez nadpisu) |
| `view` | `auto` | `week` = týdenní mřížka (dny × hodiny), `day` = seznam na jeden den, `auto` = podle šířky karty |
| `show_room` | `true` | učebna |
| `show_teacher` | `false` | vyučující |
| `show_times` | `true` | časy hodin |
| `show_footer` | `true` | čas poslední aktualizace |
| `highlight_now` | `true` | zvýraznit probíhající hodinu |
| `short_names` | `auto` | zkratky předmětů v úzké mřížce (`true` / `false` / `auto`) |
| `next_week_from` | `friday_after_school` | kdy přepnout na další týden: `friday_after_school`, `saturday`, `never` |
| `subject_colors` | – | vlastní barvy, např. `{Matematika: "#7e57c2"}`; jinak se barva odvodí z názvu |

Šipkami se přepínají týdny (resp. dny), kliknutím na datum se vrátíte na dnešek, kliknutím na
nadpis otevřete detail entity. Zrušené hodiny jsou přeškrtnuté, změněné (suplování) mají čárkovaný rámeček.

Karta jen zobrazuje data senzoru – rozvrh stahuje integrace jednou denně v nastavený čas.
