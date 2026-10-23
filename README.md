# Northstar PDF

Northstar PDF is an original-brand, offline-first Windows PDF viewer. The current executable focuses on viewing and navigation: multiple tabs, continuous or single-page mode, fit-to-width/page zoom, page labels, bookmarks, text search with result highlighting, recent files, dark theme, and full screen. The Pages menu also supports rotating or deleting the current page, extracting the current page, and merging PDFs into a new file. PDF text selection and copy are supplied by Qt PDF's viewer widget where supported.

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
- **PyInstaller**: GPL with a bootloader exception; include applicable notices.

This application has not been legally reviewed. The build workflow includes library payloads in the executable, so do not sell or redistribute it until the Qt/PDFium/PyInstaller notice package and LGPL requirements have been verified for the exact versions.

## Current scope

This is a functional viewer MVP, not an Acrobat replacement. Page manipulation currently covers current-page rotate/delete/extract and PDF merge. Reordering, crop, split, insert, annotations, OCR, forms, signatures, and conversion are not included yet. Search is literal (Qt PDF's built-in search model), not regex or cross-file search. Two-page spread view and page rotation are not included yet.
