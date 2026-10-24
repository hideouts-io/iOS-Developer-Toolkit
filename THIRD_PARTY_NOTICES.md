# Third-party software notices

iOS Developer Toolkit's original source code is distributed under the repository's [MIT License](LICENSE). That license does not replace or override the licenses of third-party software used to build or run the application.

The prebuilt macOS application contains or is built from the following release-critical components:

| Component | Pinned release | Role | Declared license | Source and license information |
|---|---:|---|---|---|
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | 11.19.4 | Bundled Apple-device protocol implementation and command surface | GPL-3.0-or-later | [Source for 11.19.4](https://github.com/doronz88/pymobiledevice3/tree/v11.19.4) and [license](https://github.com/doronz88/pymobiledevice3/blob/v11.19.4/LICENSE) |
| [PySide6 Essentials](https://doc.qt.io/qtforpython-6/) and Shiboken6 | 6.9.3 | Bundled Qt Core, GUI, Widgets, SVG icons, deployment tooling, and Python bindings | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only, as declared by the installed wheels | [Qt for Python source](https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.9.3) and [Qt licensing](https://www.qt.io/licensing/open-source-lgpl-obligations) |
| Qt Cocoa platform plugin | 6.9.3 with toolkit ownership repair | Source-built native macOS accessibility plugin | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only; consult included source headers and license texts | [Qt 6.9.3 source](https://code.qt.io/cgit/qt/qtbase.git/tag/?h=v6.9.3), with the two parent-ownership guards proposed in [Qt review 765434](https://codereview.qt-project.org/c/qt/qtbase/+/765434) |
| [Nuitka](https://github.com/Nuitka/Nuitka) | 4.2.1 | Release compiler; generated applications contain separately licensed Nuitka runtime material | Compiler: GNU AGPL v3; runtime terms are supplied by Nuitka in `LICENSE-RUNTIME.txt` | [Source for 4.2.1](https://github.com/Nuitka/Nuitka/tree/4.2.1) |
| [CPython](https://github.com/python/cpython) | GitHub runner's Python 3.13 patch release | Bundled Python runtime | Python Software Foundation License Version 2 | [Source and license](https://github.com/python/cpython/blob/3.13/LICENSE) |

The release builder applies the hash-verified import-probe stdin repair in `scripts/patch_nuitka_probe.py` to its dedicated Nuitka 4.2.1 environment. The repair preserves verbose import discovery and the exact probe input while replacing the input pipe with a temporary file to prevent a macOS pipe deadlock. Its complete source and accepted vendor hashes are part of the toolkit source.

The release builder replaces the stock Cocoa plugin with the matching source-built plugin from `scripts/build_qt_cocoa_plugin.py`. The repair keeps parent-managed accessibility placeholders from deleting their parent interface. Its source archive, build manifest, patch and licenses accompany the application; the SBOM records the final packaged plugin hash. Accessibility remains enabled. See the corresponding-source archive requirements in [SOURCE_AVAILABILITY.md](SOURCE_AVAILABILITY.md).

Each architecture-specific release also contains:

- `iOS-Developer-Toolkit-vVERSION-macOS-ARCH.cdx.json`, a CycloneDX SBOM generated from the exact pinned runtime environment plus the Nuitka compiler/runtime component;
- `Contents/Resources/Licenses/THIRD_PARTY_PACKAGES.md`, generated from the package metadata installed during that native build;
- any license or notice files supplied inside those installed Python wheels;
- this notice, the repository MIT license, [source-availability statement](SOURCE_AVAILABILITY.md), and a copy of the release SBOM inside the `.app` bundle.

The generated package inventory is intentionally more detailed than this summary and includes transitive Python dependencies. A package whose wheel does not contain a license text is identified as such in the inventory and linked to its declared project source when available.

The separate firmware executables `idevicerestore` and `irecovery` use pinned libimobiledevice, libimobiledevice-glue, libusbmuxd, libtatsu, libplist, libirecovery, libzip and OpenSSL components. The executables are carried directly in `Contents/Helpers/`, with exact revisions, license texts and final signed file hashes in `Contents/Resources/restore-helpers/`; native components are also included in the release SBOM. The matching source archive described in [SOURCE_AVAILABILITY.md](SOURCE_AVAILABILITY.md) preserves each component's complete tracked source and license headers, including differing LGPL notices within some projects, together with the build-local HTTPS transport patch. Consult those included texts for the component's terms. These helpers are subprocesses and are not imported or linked into Python toolkit code.

UFADE and MVT are optional, separately installed external providers. The toolkit does not bundle those projects. MVT remains subject to the [MVT License](https://license.mvt.re/1.1/) and its consent and interpretation boundaries. Other projects named in the README as design references are not copied, imported, or linked unless the README explicitly says otherwise.

The documentation workflow uses pinned [Material for MkDocs](https://github.com/squidfunk/mkdocs-material) 9.7.7 under its MIT license. It is a site-build dependency only and is not bundled in the macOS application or application SBOM.

See [SOURCE_AVAILABILITY.md](SOURCE_AVAILABILITY.md) for the project source location, matching tagged source, and upstream source locations for bundled third-party components.

These notices document the shipped dependency boundary; they are not legal advice. Anyone redistributing a modified or repackaged application remains responsible for satisfying every applicable component license, including source-availability and relinking obligations where they apply.
