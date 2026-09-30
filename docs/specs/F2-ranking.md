# Spec F2 — Ranking, Top-N filter and CSV export

## Goal
For each vacancy, show HR a clear ranked shortlist they can trust and take elsewhere.

## Ranking (`app/services/scoring.py`)
- Candidates with a score for the vacancy and status `done`, ordered by score desc, then
  upload order (stable).
- Standard competition ranks: equal scores share a rank (1, 2, 2, 4).
- Top-N (`?top=5|10|20|all`, default 10, any 1–1000 accepted): the first N plus everyone
  tied with the N-th score, so equal candidates are never cut arbitrarily.
- Stats over all scored candidates: count, best, average, number of `strong` + `good`.

## Page `GET /sessions/<id>/vacancies/<vid>`
- Breadcrumbs, vacancy tabs, stat tiles, Top-N segmented control, "showing X of Y".
- Table: rank badge (top 3 highlighted), name/headline/experience/file, score bar coloured
  by band, verdict badge, summary; expandable details with matched/missing skill chips,
  strengths, concerns and contacts. Card layout on phones.
- Notice while analysis is still running; empty state with a call to action.
- Fairness note: the score was produced without identity or demographic data.

## Export `GET /sessions/<id>/vacancies/<vid>/export.csv?top=N`
- Same rows as the page; columns: rank, name, file, score, verdict, summary, matched,
  missing, strengths, concerns, headline, years of experience, email, phone.
- UTF-8 with BOM, `;` delimiter, CRLF — opens correctly in Excel with RU/KK locales.
- Cells starting with `= + - @ \t \r` are prefixed with `'` (CSV/formula injection).
- Never contains demographics. `Content-Disposition` has an ASCII fallback plus an RFC 5987
  UTF-8 filename; `Cache-Control: no-store`.
- A vacancy is only reachable through its own session (404 otherwise).

## Acceptance
Unit tests for ties, Top-N cut, parsing of `top`, CSV encoding and injection; route tests
for sorting, Top-N, tabs, export headers/content, cross-session 404 and empty state.
