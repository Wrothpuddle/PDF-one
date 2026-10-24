# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Northstar PDF contributors
"""Markup summary report: a PDF listing every markup, with measurement totals."""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

import pymupdf as fitz

import markups

CSS = """
* { font-family: sans-serif; }
h1 { font-size: 16pt; margin: 0 0 2pt 0; }
p.meta { font-size: 8.5pt; color: #555; margin: 0 0 8pt 0; }
h2 { font-size: 11pt; margin: 10pt 0 4pt 0; }
table { border-collapse: collapse; width: 100%; }
th { background: #dde7f0; font-size: 8pt; text-align: left; padding: 3pt;
     border: 0.5pt solid #9aa9b6; }
td { font-size: 8pt; padding: 3pt; border: 0.5pt solid #c3cdd6; vertical-align: top; }
td.num { text-align: right; }
"""


def _cell(value) -> str:
    return html.escape(str(value or "")).replace("\n", "<br/>")


def build_html(title: str, rows: list[dict],
               threads: dict[tuple[int, int], list[dict]] | None = None) -> str:
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    for row in rows:
        by_type[row["type"]] = by_type.get(row["type"], 0) + 1
        by_status[row["status"] or "(none)"] = by_status.get(row["status"] or "(none)", 0) + 1
    parts = [f"<h1>Markup Summary — {_cell(title)}</h1>",
             f"<p class='meta'>{len(rows)} markups · generated "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M')} by Northstar PDF</p>"]
    parts.append("<h2>Overview</h2><table><tr><th>Type</th><th>Count</th>"
                 "<th>Status</th><th>Count</th></tr>")
    types, statuses = sorted(by_type.items()), sorted(by_status.items())
    for i in range(max(len(types), len(statuses))):
        t = types[i] if i < len(types) else ("", "")
        s = statuses[i] if i < len(statuses) else ("", "")
        parts.append(f"<tr><td>{_cell(t[0])}</td><td class='num'>{t[1]}</td>"
                     f"<td>{_cell(s[0])}</td><td class='num'>{s[1]}</td></tr>")
    parts.append("</table>")
    totals = markups.measurement_summary(rows)
    if totals:
        parts.append("<h2>Measurement totals</h2><table><tr><th>Subject</th><th>Total</th>"
                     "<th>Count</th></tr>")
        for subject, total, count in totals:
            parts.append(f"<tr><td>{_cell(subject)}</td><td>{_cell(total)}</td>"
                         f"<td class='num'>{count}</td></tr>")
        parts.append("</table>")
    with_files = any("file" in row for row in rows)  # a report for a whole set
    parts.append("<h2>Markups</h2><table><tr>" + ("<th>File</th>" if with_files else "")
                 + "<th>Page</th><th>Type</th><th>Subject</th>"
                 "<th>Comment</th><th>Author</th><th>Date</th><th>Status</th>"
                 "<th>Measurement</th></tr>")
    for row in sorted(rows, key=lambda r: (r.get("file", ""), r["page"], r["type"])):
        parts.append(
            "<tr>" + (f"<td>{_cell(row.get('file', ''))}</td>" if with_files else "")
            + f"<td class='num'>{row['page'] + 1}</td><td>{_cell(row['type'])}</td>"
            f"<td>{_cell(row['subject'])}</td><td>{_cell(row['comment'])}"
            + "".join(f"<br/><i>&#8627; {_cell(reply['author'] or 'Reply')}: "
                      f"{_cell(reply['text'])}</i>"
                      for reply in (threads or {}).get((row["page"], row["xref"]), []))
            + "</td>"
            f"<td>{_cell(row['author'])}</td><td>{_cell(row['date'])}</td>"
            f"<td>{_cell(row['status'])}</td><td>{_cell(row['measurement'])}</td></tr>")
    parts.append("</table>")
    return "".join(parts)


def write_summary(doc: fitz.Document | None, output: str | Path, title: str,
                  rows: list[dict] | None = None) -> Path:
    """Write the report as a landscape Letter PDF."""
    rows = markups.list_markups(doc) if rows is None else rows
    # Reply threads are looked up in doc, so skip them for multi-file (set) reports.
    threads = {(row["page"], row["xref"]): markups.replies(doc[row["page"]], row["xref"])
               for row in rows if row.get("replies") and "file" not in row} if doc else {}
    output = Path(output)
    story = fitz.Story(html=build_html(title, rows, threads), user_css=CSS)
    page_rect = fitz.paper_rect("letter-l")
    content = page_rect + (36, 36, -36, -40)
    writer = fitz.DocumentWriter(str(output))
    more = True
    while more:
        device = writer.begin_page(page_rect)
        more, _filled = story.place(content)
        story.draw(device)
        writer.end_page()
    writer.close()
    # Number the pages.
    with fitz.open(output) as result:
        for page in result:
            page.insert_text((page.rect.width - 110, page.rect.height - 20),
                             f"Page {page.number + 1} of {result.page_count}",
                             fontsize=8, color=(0.4, 0.4, 0.4))
        result.saveIncr()
    return output
