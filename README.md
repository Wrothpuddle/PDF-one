# Northstar PDF

Northstar PDF is an original-brand, offline-first Windows PDF editor. It opens PDFs locally, has no sign-in or online-facing features, and stages supported edits in a temporary working copy until you save.

## Available in the current build

- Multiple document tabs, continuous or single-page view, fit-to-width/page and 100% zoom, page navigation, full screen, dark theme, recent files, and built-in text search.
- Bookmark navigation, selectable/copyable text where the PDF contains a text layer, and page labels in the viewer.
- Current-page rotation and deletion, arbitrary page reordering, crop-box adjustment, insert/replace/extract page operations, splitting into one-page PDFs, and merging PDFs.
- Sticky-note annotations, form field inspection/filling, and plain-text export.
- Staged edits with disk-backed undo/redo. Ctrl+S writes edits back to the same PDF; Save/Discard/Cancel is offered when closing a dirty document. Separate exports such as merge, split, extract, and text export ask for output paths.
- Password-protected source PDFs can be opened with a password; supported edit operations preserve encryption using AES-256.

The crop operation changes the visible crop box; underlying content outside the box remains in the PDF and can be revealed by changing the crop. It is not secure redaction. Rebuilding page sequences may not preserve every advanced PDF feature (for example, all document-level metadata, named destinations, or complex forms).

## Task workspaces and tool palette

The workspace strip offers Home, Read, Edit, Organize, Comment, Fill & Sign, Protect, and Create & Convert presets inspired by familiar PDF editor workflows. The movable **Tools** dock filters to the actions currently implemented for each task. Drag its title bar to dock it in another area, or use **View → Tools** to show it again. The Read, Edit, Organize, Comment, Fill & Sign, and Create & Convert presets are connected to existing local operations; Protect currently exposes save/export actions only. Advanced features such as text/image object editing, OCR, signatures, true redaction, Office conversion, and PDF/A/X checks remain unavailable and are not shown as working controls.

The selected workspace and dock layout are saved locally. Existing document tabs remain separate from the task workspace tabs.

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
- **PyInstaller**: GPL with a bootloader exception; include applicable notices.

This application has not been legally reviewed. The build workflow includes library payloads in the executable, so do not sell or redistribute it until the Qt/PDFium/PyInstaller notice package and LGPL requirements have been verified for the exact versions.

## Not implemented yet

This is a growing local PDF editor, not a complete Acrobat replacement. Text/image object editing with font matching and reflow, broad markup tools (highlight, freehand, shapes, callouts, stamps, measurement), comments filtering/export, attachments/layers panels, regex and multi-file search, and full two-page spread are not implemented. The current forms work is basic field filling; a form designer, calculations, robust XFDF/FDF/CSV interchange, and advanced form widgets are not implemented. Office/HTML conversion, scanning, batch action wizard, OCR, signatures/certificate validation, true redaction and sanitization, PDF/A or PDF/X validation, accessibility tagging, preflight, and print-production workflows are also not implemented. These require dedicated implementations and validation; this build does not present them as completed features.
