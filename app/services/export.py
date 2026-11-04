"""US7 — Excel and PDF export of the ranked shortlist (CSV lives in scoring.py).

Both formats are built locally, without any AI call. Like the CSV export they never
include demographics, and text cells are protected against spreadsheet formula injection.

- Excel: one sheet per vacancy (a single vacancy or the whole session) plus a summary
  sheet. Cyrillic works out of the box (xlsx is UTF-8).
- PDF: a printable shortlist report for one vacancy. Uses the bundled DejaVu Sans font,
  because the PDF core fonts have no Cyrillic or Kazakh letters.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from fpdf import FPDF
from fpdf.enums import XPos, YPos
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .scoring import RankedCandidate, _safe_cell

FONTS_DIR = Path(__file__).resolve().parent.parent / "fonts"
BRAND = "14213D"
SCORE_FILLS = ((85, "DDF0DA"), (65, "DCEBFA"), (40, "FFEBC7"), (0, "F6D5D0"))
SHEET_NAME_MAX = 31

Translate = Callable[..., str]


@dataclass
class VacancySheet:
    title: str
    candidates: list[RankedCandidate]
    total: int


def _sheet_title(title: str, used: set[str]) -> str:
    """Excel sheet names: max 31 chars, no []:*?/\\ and unique within the workbook."""
    base = re.sub(r"[\[\]:*?/\\]", " ", title).strip()[:SHEET_NAME_MAX] or "Sheet"
    name, n = base, 2
    while name.lower() in used:
        suffix = f" ({n})"
        name = base[: SHEET_NAME_MAX - len(suffix)] + suffix
        n += 1
    used.add(name.lower())
    return name


def _score_fill(score: int) -> PatternFill:
    color = next(c for limit, c in SCORE_FILLS if score >= limit)
    return PatternFill("solid", fgColor=color)


COLUMNS = (
    ("rank", 7), ("name", 26), ("score", 8), ("verdict", 16), ("summary", 60), ("matched", 34),
    ("missing", 34), ("strengths", 40), ("concerns", 40), ("headline", 26), ("experience", 10),
    ("email", 26), ("phone", 18), ("file", 24),
)


def _row(c: RankedCandidate, t: Translate) -> list[object]:
    values = {
        "rank": c.rank, "name": c.name or "", "score": c.score, "verdict": t(f"verdict.{c.verdict}"),
        "summary": c.summary, "matched": ", ".join(c.matched_skills), "missing": ", ".join(c.missing_skills),
        "strengths": "; ".join(c.strengths), "concerns": "; ".join(c.concerns), "headline": c.headline or "",
        "experience": c.experience_years, "email": c.email or "", "phone": c.phone or "", "file": c.filename,
    }
    # Numbers stay numbers (sortable in Excel); every text cell is neutralized.
    return [v if isinstance(v, (int, float)) or v is None else _safe_cell(v) for v in (values[k] for k, _ in COLUMNS)]


def to_xlsx(session_title: str, sheets: list[VacancySheet], t: Translate) -> bytes:
    wb = Workbook()
    summary = wb.active
    used: set[str] = set()
    summary.title = _sheet_title(t("xlsx.summary"), used)
    bold = Font(bold=True)
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor=BRAND)

    summary["A1"] = _safe_cell(session_title)
    summary["A1"].font = Font(bold=True, size=14)
    summary["A2"] = t("xlsx.generated", date=datetime.now().strftime("%d.%m.%Y %H:%M"))
    summary.append([])
    summary.append([t("xlsx.col.vacancy"), t("xlsx.col.in_file"), t("xlsx.col.scored"), t("xlsx.col.best"),
                    t("xlsx.col.average"), t("xlsx.col.fit")])
    for cell in summary[4]:
        cell.font, cell.fill = head_font, head_fill

    for sheet in sheets:
        cands = sheet.candidates
        scores = [c.score for c in cands]
        summary.append([
            _safe_cell(sheet.title), len(cands), sheet.total, max(scores, default=0),
            round(sum(scores) / len(scores)) if scores else 0, sum(c.verdict in ("strong", "good") for c in cands),
        ])

        ws = wb.create_sheet(_sheet_title(sheet.title, used))
        ws.append([t(f"csv.{key}") for key, _ in COLUMNS])
        for cell in ws[1]:
            cell.font, cell.fill = head_font, head_fill
            cell.alignment = Alignment(vertical="center", wrap_text=True)
        for c in cands:
            ws.append(_row(c, t))
            score_cell = ws.cell(ws.max_row, 3)
            score_cell.fill, score_cell.font = _score_fill(c.score), bold
        for i, (_, width) in enumerate(COLUMNS, start=1):
            ws.column_dimensions[get_column_letter(i)].width = width
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        ws.freeze_panes = "C2"
        ws.auto_filter.ref = ws.dimensions

    summary.append([])
    summary.append([t("xlsx.note")])
    for col, width in zip("ABCDEF", (40, 14, 12, 12, 12, 16)):
        summary.column_dimensions[col].width = width

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


class _Report(FPDF):
    """A4 report; every multi_cell returns to the left margin on the next line."""

    def multi_cell(self, w, h=None, text="", **kwargs):  # type: ignore[override]
        kwargs.setdefault("new_x", XPos.LMARGIN)
        kwargs.setdefault("new_y", YPos.NEXT)
        return super().multi_cell(w, h, text, **kwargs)

    def __init__(self, footer_text: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.footer_text = footer_text
        self.add_font("DejaVu", "", str(FONTS_DIR / "DejaVuSans.ttf"))
        self.add_font("DejaVu", "B", str(FONTS_DIR / "DejaVuSans-Bold.ttf"))
        self.set_auto_page_break(auto=True, margin=16)
        self.set_margins(15, 15, 15)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("DejaVu", "", 7.5)
        self.set_text_color(110, 110, 110)
        self.cell(0, 5, f"{self.footer_text}   ·   {self.page_no()}/{{nb}}", align="C")


def _pdf_text(value: object) -> str:
    # Drop control characters that would break the layout; DejaVu covers RU/KK letters.
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", " ", "" if value is None else str(value))


def to_pdf(session_title: str, vacancy_title: str, candidates: list[RankedCandidate], total: int,
           top: int | None, t: Translate) -> bytes:
    pdf = _Report(t("pdf.footer"))
    pdf.alias_nb_pages()
    pdf.add_page()
    width = pdf.epw

    pdf.set_font("DejaVu", "B", 16)
    pdf.set_text_color(20, 33, 61)
    pdf.multi_cell(width, 8, _pdf_text(t("pdf.title", vacancy=vacancy_title)))
    pdf.set_font("DejaVu", "", 9)
    pdf.set_text_color(80, 80, 80)
    scope = t("pdf.top", n=top) if top else t("pdf.all")
    pdf.multi_cell(width, 5, _pdf_text(t("pdf.meta", session=session_title, scope=scope, shown=len(candidates),
                                          total=total, date=datetime.now().strftime("%d.%m.%Y"))))
    pdf.ln(3)

    if not candidates:
        pdf.set_font("DejaVu", "", 11)
        pdf.multi_cell(width, 6, _pdf_text(t("rank.empty")))
        return bytes(pdf.output())

    # Overview table
    cols = ((t("csv.rank"), 12), (t("csv.name"), 62), (t("csv.score"), 14), (t("csv.verdict"), 46), (t("csv.headline"), width - 134))
    pdf.set_font("DejaVu", "B", 8.5)
    pdf.set_fill_color(20, 33, 61)
    pdf.set_text_color(255, 255, 255)
    for label, w in cols:
        pdf.cell(w, 7, _pdf_text(label), border=0, fill=True)
    pdf.ln()
    pdf.set_font("DejaVu", "", 8.5)
    pdf.set_text_color(20, 20, 20)
    for i, c in enumerate(candidates):
        pdf.set_fill_color(*((247, 245, 239) if i % 2 == 0 else (255, 255, 255)))
        values = (c.rank, c.name or c.filename, c.score, t(f"verdict.{c.verdict}"), c.headline or "")
        for (_, w), value in zip(cols, values):
            text = _pdf_text(value)
            while text and pdf.get_string_width(text) > w - 2:
                text = text[:-2] + "…"
            pdf.cell(w, 6.5, text, fill=True)
        pdf.ln()

    # One block per candidate
    pdf.ln(4)
    for c in candidates:
        if pdf.will_page_break(30):
            pdf.add_page()
        pdf.set_font("DejaVu", "B", 10.5)
        pdf.set_text_color(20, 33, 61)
        pdf.multi_cell(width, 6, _pdf_text(f"{c.rank}. {c.name or c.filename} — {c.score}/100 ({t(f'verdict.{c.verdict}')})"))
        pdf.set_font("DejaVu", "", 8.5)
        pdf.set_text_color(40, 40, 40)
        lines = [c.summary]
        for key, items in (("rank.matched", c.matched_skills), ("rank.missing", c.missing_skills),
                           ("rank.strengths", c.strengths), ("rank.concerns", c.concerns)):
            if items:
                lines.append(f"{t(key)}: {', '.join(items)}")
        contacts = " · ".join(v for v in (c.email, c.phone) if v)
        if contacts:
            lines.append(f"{t('rank.contacts')}: {contacts}")
        for line in lines:
            pdf.multi_cell(width, 4.6, _pdf_text(line))
        pdf.ln(2.5)

    pdf.set_font("DejaVu", "", 7.5)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(width, 4, _pdf_text(t("rank.fairness_note")))
    return bytes(pdf.output())
