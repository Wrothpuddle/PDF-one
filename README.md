# Northstar PDF

Northstar PDF is an original-brand, offline-first Windows PDF editor. It opens PDFs locally, has no sign-in or online-facing features, and stages supported edits in a temporary working copy until you save.

## Available in the current build

- Multiple document tabs, continuous or single-page view, fit-to-width/page and 100% zoom, page navigation, scroll-wheel zoom, middle-button drag panning, full screen, dark theme, recent files, and built-in text search.
- Bookmark navigation, selectable/copyable text where the PDF contains a text layer, and page labels in the viewer.
- Current-page rotation and deletion, arbitrary page reordering, crop-box adjustment, insert/replace/extract page operations, splitting into one-page PDFs, and merging PDFs.
- Comment workspace tools for text boxes, sticky notes, highlight, underline, strikethrough, freehand pen, lines, arrows, rectangles, and ellipses. Select a tool, then click or drag on the page; press Esc to exit the active tool. These are PDF annotations and remain undoable until saved.
- Form field inspection/filling and plain-text export.
- Staged edits with disk-backed undo/redo. Ctrl+S writes edits back to the same PDF; Save/Discard/Cancel is offered when closing a dirty document. Separate exports such as merge, split, extract, and text export ask for output paths.
- Password-protected source PDFs can be opened with a password; supported edit operations preserve encryption using AES-256.

The crop operation changes the visible crop box; underlying content outside the box remains in the PDF and can be revealed by changing the crop. It is not secure redaction. Rebuilding page sequences may not preserve every advanced PDF feature (for example, all document-level metadata, named destinations, or complex forms).

## Task workspaces and tool palette

The workspace strip uses a document-centric layout with Home, Document, Markup, and Tool Chest tabs. The **Tool Chest** and **Pages/Bookmarks** docks share the left panel, with a compact markup toolbar above the document. Drag its title bar to dock it in another area, or use **View → Tools** to show it again. The Read, Edit, Organize, Comment, Fill & Sign, and Create & Convert presets are connected to existing local operations; Protect includes a local AES-256 password-protected copy action, along with save/export. Text replacement is available from Tools → Edit existing text. It finds an exact phrase on the current page, redacts the old glyphs, and inserts a replacement at the same location; this is staged in the document edit session with undo/redo and save-on-demand. Font metrics and color are approximated, surrounding text does not reflow, and scanned pages need OCR first. PyMuPDF is AGPL-3.0-or-later or commercially licensed by Artifex; this private local build uses its AGPL package. Do not distribute without meeting its license obligations or obtaining an Artifex commercial license. Place text box opens a live editor directly over the page: drag to define the box, type while seeing the preview, resize it with the blue corner handle, then choose Place (or Ctrl+Enter). The formatted text is added to the page itself, so it remains visible and selectable after the preview closes. The top formatting toolbar controls point size, bold, italic, underline, letter spacing, line spacing, and left/center/right/justified alignment. Place writes the formatted text and visible blue outline into the PDF page content, so the text appears in the viewer and remains selectable and searchable. Placed text boxes can be selected and dragged to a new position before saving; this replays the edit journal so later edits are retained and undo remains available. Press Esc to switch to Select. Select also preserves ordinary PDF text selection. The active tool remains highlighted in the toolbar and in the persistent status indicator. Comment tools add visible markups for sticky notes, highlight, underline, strikethrough, freehand pen, lines, arrows, rectangles, and ellipses. To support Qt's PDF viewer, these marks include page-content appearances plus PDF annotation objects. OCR, signatures, Office conversion, and PDF/A/X checks remain unavailable.

The selected workspace and dock layout are saved locally. Existing document tabs remain separate from the task workspace tabs. The current in-memory edit journal is needed to move text boxes; after closing and reopening a saved PDF, previously placed text is page content and cannot yet be reselected as a movable object.

## Build and download

Every push to `main` builds `NorthstarPDF.exe` on a clean Windows runner. In GitHub, open **Actions → Build Windows app → latest successful run → Artifacts → NorthstarPDF-Windows**. Extract the artifact ZIP and double-click `NorthstarPDF.exe`. It is a one-file build and does not require Python or Qt installed on your PC. The app itself does not check for updates or contact a service. GitHub Actions is used only to build and deliver the binary.

The workflow can also be started manually from the Actions tab using **Run workflow**. Build artifacts are retained for 30 days. This repository does not commit executable binaries.

## Local development

On Windows with Python 3.12:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt pyinstaller pytest
python src\main.py
```

To make a local executable, run the `pyinstaller` command in `.github/workflows/windows-build.yml`.

## Offline behavior

The program installs a Python audit hook before importing Qt. It rejects Python socket creation, URL requests, and HTTP client connections. A regression test verifies that socket creation fails. The app does not import Qt Network, WebEngine, update, sharing, or telemetry modules. This source-level/runtime Python guard cannot observe native sockets opened directly by a prebuilt binary; the packaged dependency set is intentionally limited to Qt Core/Gui/Widgets/Pdf/PdfWidgets.

## Libraries and licenses

- **PySide6 / Qt PDF**: Qt modules are available under LGPLv3, GPLv3, and/or commercial terms depending on module and distribution. Qt PDF uses PDFium internally. Review the exact license notices shipped with the wheel and meet applicable dynamic-linking/replacement and attribution obligations before redistributing the executable. The build artifact bundles Qt libraries in a one-file PyInstaller package; confirm the LGPL compliance model for this distribution before commercial release.
- **PDFium**: BSD-style license with additional bundled third-party notices. Qt's PDF module includes attribution and license data; release packaging must retain it.
- **pypdf**: BSD-3-Clause; include the license notice when redistributing.
- **cryptography**: Apache-2.0 or BSD-3-Clause; include applicable notices when redistributing.
- **PyMuPDF**: AGPL-3.0-or-later or Artifex commercial license; distribution requires license compliance.
- **PyInstaller**: GPL with a bootloader exception; include applicable notices.

This application has not been legally reviewed. The build workflow includes library payloads in the executable, so do not sell or redistribute it until the Qt/PDFium/PyInstaller notice package and LGPL requirements have been verified for the exact versions.

## Not implemented yet

This is a growing local PDF editor, not a complete Acrobat replacement. Acrobat-quality text/image editing with font matching and reflow, advanced callouts, stamps, measuring tools, comment filtering/export, attachments/layers panels, regex and multi-file search, and full two-page spread are not implemented. Basic existing-text replacement is available in the Edit workspace, and the Comment workspace supports common text and shape annotations. The editing tools do not yet provide Acrobat's font reconstruction, paragraph reflow, or complete markup toolset. The current forms work is basic field filling; a form designer, calculations, robust XFDF/FDF/CSV interchange, and advanced form widgets are not implemented. Office/HTML conversion, scanning, batch action wizard, OCR, signatures/certificate validation, true redaction and sanitization, PDF/A or PDF/X validation, accessibility tagging, preflight, and print-production workflows are also not implemented. These require dedicated implementations and validation; this build does not present them as completed features.
