# US7 — Export of the shortlist to Excel and PDF (Sprint 2)

As a hiring manager I want to export the top-N shortlist to Excel or PDF so that I can share
it with colleagues who do not use the tool.

## Scope
- Ranking page: buttons **Excel**, **PDF**, **CSV** export the current Top-N (ties kept).
- Session page: **Excel: all vacancies** — one workbook, summary sheet + one sheet per
  vacancy with its Top-10.
- Excel: header row in the UI language, frozen columns, auto-filter, score cell coloured by
  verdict band, numeric score/rank/experience. Text cells starting with `= + - @ \t \r` are
  prefixed with `'` (same rule as CSV).
- PDF: A4, overview table + one block per candidate (summary, matched/missing skills,
  strengths, concerns, contacts) and the fairness note. DejaVu Sans is bundled in
  `app/fonts/` (licence in `LICENSE-DejaVu.txt`) because PDF core fonts have no Cyrillic.
- No demographics in any export. No AI call for any export.

## Routes
| Route | Result |
|-------|--------|
| `GET /sessions/<sid>/vacancies/<vid>/export.xlsx?top=N` or `top=all` | Excel for one vacancy |
| `GET /sessions/<sid>/vacancies/<vid>/export.pdf?top=N` or `top=all` | PDF report for one vacancy |
| `GET /sessions/<sid>/export.xlsx?top=N` or `top=all` | Excel for every vacancy (default Top-10) |

## Tests
`tests/test_export.py`: workbook structure, formula neutralization, sheet-name rules, PDF
Cyrillic/Kazakh text, long and empty lists, routes, 404 on foreign ids, buttons on the page,
zero AI calls, resume-language diversity.
