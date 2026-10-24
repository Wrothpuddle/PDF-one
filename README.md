# Northstar PDF

Northstar PDF is an offline Windows PDF markup, measurement, and review app modelled on the Bluebeam Revu workflow. It opens PDFs locally, has no sign-in or online features, and keeps edits in memory, with undo, until you save.

## Features

### Live markups
Every markup is a real PDF annotation. It stays selectable and editable after saving, and other PDF viewers (Acrobat, Bluebeam, browsers) show it the same way.

- **Tools:** Text Box, Callout, Note, Highlight / Underline / Strikethrough (these snap to the words under the drag), Pen, Highlighter, Line, Arrow, Polyline, Polygon, Cloud, Rectangle, Ellipse, Stamps (Approved, Draft, For Comment, and more), and Redaction.
- **Select tool:** click to select (Shift/Ctrl adds to the selection), drag a box to select several markups, drag to move, drag corner handles to resize, and drag vertex handles to reshape lines, polylines, polygons, and clouds. Arrow keys nudge (Shift moves 10 pt). Delete removes markups, Ctrl+D duplicates, Ctrl+C / Ctrl+V copy and paste (also between documents), and F2 or a double-click edits text.
- **Tool Lock** (Ctrl+L) keeps a tool active after each use; otherwise the app returns to Select and selects the new markup. Hold **Shift** or turn on **Ortho** (F8) to snap lines to 45° steps.

### Properties panel
Shows the selected markup, or the active tool's defaults when nothing is selected. You can change the subject, line colour, fill, line width, opacity, line style (solid, dashed, or dotted), line ends, font (Helvetica, Courier, or Times), bold, italic, underline, font size, text colour (separate from the border colour), alignment, stamp, status, and comment. Changes apply to every selected markup and can be undone.

### Markups List
A table of every markup in the document showing page, type, subject, comment, author, date, status, colour, and measurement. You can filter by text, type, or status, sort by any column, set the status (Accepted, Rejected, Completed, …), delete markups, and export the list to CSV. Clicking a row jumps to that markup. A totals line adds up measurements by subject (for example, total flooring area or the number of outlets counted).

### Tool Chest
- **Built-in sets:** Markup, Measure, Construction Review (revision and hold clouds, RFI callout, coloured highlighters, area takeoff, count tools), Stamps, and Redaction.
- **Recent Tools** remembers each tool and property combination you use.
- **Saving tools:** "Add to Tool Chest…" saves the selected markup's look as a reusable tool in **My Tools** or a new set of your own.
- **Sharing:** sets can be exported and imported as JSON.

### Measurement
- **Scales are set per page.** Choose a standard scale (1/8" = 1'-0", 1/4" = 1'-0", 1" = 20', 1:100, …), enter a custom ratio, or use **Calibrate**: drag along a known dimension and type its real length (12'-6" style input is accepted). A scale can be applied to one page or every page.
- **Tools:** Length, Polylength, Area, Perimeter, Count, and Angle.
- **Snap to Content** (F9, on by default) moves measurement and markup points onto line endpoints, midpoints, intersections, and existing markup vertices, with a marker showing what it snapped to. Hold Shift to place a point freely or snap to 45°.
- **Updating and display:** values follow the page scale and recalculate when a measurement is moved or reshaped. Values display in feet-inches (to 1/16"), feet, inches, millimetres, centimetres, or metres. Length measurements show their value on the line in any PDF viewer; other measurements are labelled on the page in Northstar and store their value in the markup's comment.

### Drawing review
- **Compare Documents** finds visible differences between an older revision and the open drawing, and adds a cloud markup around each change.
- **Overlay Pages** combines two revisions into a new view: red means only in the older revision, cyan means only in the newer one, and black means unchanged.
- **Snapshot** copies any area of a page to the clipboard as an image.
- **Links:** the Link tool turns any area into a link to another page, and clicking a link with Select follows it. **Auto-Link Sheet Numbers** finds sheet numbers (such as A-101, taken from bookmarks or page labels) in the page text and links every mention to that sheet.
- **Markup Summary Report** writes a PDF with markup counts by type and status, measurement totals, and a row for every markup.

### Pages and documents
- **Viewing:** tabs for several documents, continuous or single-page view, wheel zoom at the cursor, middle-drag or Space+drag panning, zoom to area, fit width or page, and sharp rendering at high zoom on large-format sheets.
- **Page tools:** thumbnails with drag-to-reorder and multi-select; rotate, delete, insert blank pages, insert from another PDF, replace, extract, split, and merge. Crop pages by dragging the area to keep, with Reset Crop to undo it. Rotating, deleting, or reordering pages keeps bookmarks and form fields.
- **Panels:** bookmarks (add, rename, delete), layers (show or hide optional content), and search across all pages with match case and whole word options.
- **Forms and text:** fill form fields in a table (text, check boxes, and choices), replace existing text, and export the text.
- **Printing:** Print (Ctrl+P) and Print Preview, with or without markups, fit to paper or actual size. Landscape sheets rotate automatically to fill portrait paper.
- **Final output:** true redaction (marked areas are permanently removed when applied), Flatten Markups, and password-protected AES-256 copies. Password-protected PDFs open with their password and stay encrypted when saved.
- **Undo history:** undo and redo cover every edit, including page operations, and are still available after saving.
- **Autosave and recovery:** unsaved documents are copied to a recovery folder every minute. After a crash, the next start offers to reopen them.

The crop box hides content outside it but does not remove it. Use Redaction to delete content.

## Build and download

Every push to `main` and every pull request builds `NorthstarPDF.exe` on a clean Windows runner, runs the unit tests and an automated UI self-test, then smoke-tests the packaged executable. In GitHub, open **Actions → Build Windows app → latest successful run → Artifacts → NorthstarPDF-Windows**, extract the ZIP, and double-click `NorthstarPDF.exe`. It is a one-file build and needs no Python or Qt install. The workflow can also be run manually with **Run workflow**. Artifacts are kept for 30 days.

## Local development

On Windows with Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt pyinstaller pytest
python src\main.py
python -m pytest -q
python src\main.py --self-test
```

To make a local executable, run the `pyinstaller` command in `.github/workflows/windows-build.yml`.

### Code layout

| File | Purpose |
| --- | --- |
| `src/main.py` | Main window, menus, toolbars, and the `--self-test` used by CI |
| `src/canvas.py` | Page rendering (PyMuPDF), zoom/pan, and mouse handling for every tool |
| `src/pdf_document.py` | In-memory document with undo/redo (MuPDF journal plus document snapshots) and saving |
| `src/markups.py` | Creating, styling, moving, reshaping, hit-testing, and listing markups; measurement and scales |
| `src/panels.py` | Properties, Markups List, Tool Chest, Thumbnails, Bookmarks, Layers, Search |
| `src/tool_chest.py` | Tool sets and their JSON storage |
| `src/compare.py` | Overlay and difference detection |
| `src/pdf_operations.py` | Page edits, exports, forms, text replacement, flattening, redaction |
| `src/dialogs.py` | Scale, calibration, and form-filling dialogs |
| `src/snap.py` | Snap-to-content targets (endpoints, midpoints, intersections) |
| `src/links.py` | Page links and sheet-number auto-linking |
| `src/report.py` | Markup summary PDF |
| `src/printing.py` | Printing |
| `src/recovery.py` | Autosave copies and crash recovery |
| `src/icons.py` | Toolbar icons drawn in code |
| `src/offline_guard.py` | Blocks network access from Python code |

## Offline behavior

The program installs a Python audit hook before importing Qt. It rejects Python socket creation, URL requests, and HTTP client connections, and a regression test verifies this. The app does not import Qt Network, WebEngine, update, sharing, or telemetry modules. The guard cannot observe native sockets opened directly by a prebuilt library; the packaged dependency set is limited to Qt Core/Gui/Widgets and PyMuPDF.

## License

Northstar PDF is free software, licensed under the **GNU Affero General Public License v3.0 or later** (see [`LICENSE`](LICENSE)). You may use, study, share, and modify it. If you share a copy or a modified version, you must share it under the same license together with its complete source code. It comes with no warranty. Help → License in the app shows the full text, and the build download includes `LICENSE` and `THIRD_PARTY_NOTICES.md`.

## Libraries and licenses

- **PyMuPDF / MuPDF** (rendering and all PDF editing): AGPL-3.0-or-later, or a commercial license from Artifex. Its AGPL terms are why this app is AGPL too.
- **PySide6 / Qt**: LGPLv3 (or GPL/commercial). Keep the Qt license notices with distributed builds.
- **PyInstaller**: GPL with a bootloader exception, which allows bundling the app.

See `THIRD_PARTY_NOTICES.md`. This project has not been legally reviewed.

## Not implemented yet

- **Document content:** OCR of scanned pages; digital signatures and certificate validation; Office and image conversion.
- **Multi-file and collaboration:** batch processing across many files, Sets (multi-file drawing packages), and real-time collaboration (left out on purpose to keep the app offline).
- **Measurement:** volume measurements, and separate scales for viewports within a page.
- **Text:** formatting individual words differently within one text box, and paragraph reflow when editing existing text.
- **Standards:** PDF/A and PDF/X validation, and accessibility tagging.
