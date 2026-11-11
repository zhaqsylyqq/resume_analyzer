# S5 — User feedback: "Подходят" tile, mobile layout, visible score colours

Requests from pilot users (11.11.2026):

1. **The "Подходят (≥ 65)" number on the ranking page opens those candidates.** The tile is
   a link to `?top=all&fit=1#ranking-list`; the list keeps only candidates with a strong or
   good verdict (score ≥ 65). A second click (or the "Только подходящие ✕" chip) shows
   everyone again. `fit=1` combines with the status filter and search, survives a status
   change and applies to CSV/Excel/PDF exports. The "Оценено" tile resets all filters.
2. **Mobile layout.** At 390 px nothing is wider than the screen any more:
   - top bar: logo and buttons on the first row, the menu scrolls on its own row
     (before, the language switch covered "Новый скрининг" and admin links pushed the page
     to 507 px);
   - session page: grid and file list may shrink (`minmax(0, 1fr)`, `min-width: 0`);
     before, long file names made the page 639 px wide;
   - users, statistics and audit tables turn into one card per row with column names
     (`.table--cards`, `data-label`);
   - ranking: status, note and Save stay inside the candidate card; stat tiles 2 × 2.
3. **Score colours.** The bar fill was an inline `<span>`, so its width was ignored and the
   bar looked empty on every screen. Now the fill is a block, the bar is thicker (8 px,
   10 px on phones) and the number is a coloured pill: green ≥ 65, orange 40–64, red < 40.

Tests: `tests/test_fit_filter.py` (7). 361 in total.
