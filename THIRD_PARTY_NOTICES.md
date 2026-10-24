# Third-party software notices

The Windows build bundles PySide6 / Qt 6, PyMuPDF (MuPDF), and the PyInstaller bootloader.

- **PyMuPDF and MuPDF** are licensed under the GNU Affero General Public License v3.0 or later (or a commercial license from Artifex Software). Northstar PDF uses them for rendering and every PDF edit. Under the AGPL, the complete corresponding source code must be available to everyone who receives the program, including the source of this app.
- **Qt 6 / PySide6** modules used here (Core, Gui, Widgets, Test) are available under LGPLv3, GPLv3, or commercial terms. Keep Qt's license texts with distributed builds, and let users replace the Qt libraries. The build's one-file PyInstaller package extracts Qt's DLLs to a temporary folder at run time.
- **PyInstaller** is GPL-2.0 with a bootloader exception that allows bundling applications under other licenses.

The exact license texts for each version ship inside the installed packages (`site-packages/*/LICENSE*` and the `*.dist-info` folders). Include them with any build you share.
