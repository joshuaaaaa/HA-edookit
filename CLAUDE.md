# Project notes for Claude

- This repo is a HACS **integration** (`custom_components/edookit`). HACS/Home Assistant cannot
  install an integration and a Lovelace card from one repository.
- The timetable card therefore lives on its own in `edookit-timetable-card/` and the user adds it
  **manually** as a dashboard resource (`/local/edookit-timetable-card.js`). The integration must
  **not** register, serve or bundle the card (no static paths, no `add_extra_js_url`, no
  `frontend`/`http` dependencies for it).
- The user writes in Czech; user-facing docs (README) are in Czech.
- Tests: `pytest` (needs `pytest-homeassistant-custom-component`, Python 3.13); lint: `ruff check`
  and `ruff format --check` on `custom_components tests`; card: `node --check` on the JS file.
