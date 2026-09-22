# Contributing

Contributions that make authorized iOS development, diagnostics, testing, backup, and evidence workflows safer and easier to understand are welcome. The project is open to everyone through public issues, Discussions, forks, and pull requests, including first-time contributors.

## Start with the intended scope

iOS Developer Toolkit is a guided macOS front end around supported Apple-device service paths and pinned `pymobiledevice3` commands. It does not aim to jailbreak devices, bypass a passcode or activation, evade code signing, remove supervision, decrypt protected traffic, or expose unrestricted filesystem access.

Use only devices and data you own or are explicitly authorized to test. Never submit real UDIDs, serial numbers, phone numbers, Apple Account data, coordinates, pairing records, backup contents, packet payloads, profiles, certificates, crash contents, or evidence cases.

Use a bug report for reproducible defects, a feature request for a bounded workflow, and Discussions for setup or compatibility questions. Report vulnerabilities through the repository's private security reporting form.

## Development setup

The supported development host is macOS 13 or later with Python 3.10 or later. Create an isolated environment and install the project in editable mode:

```bash
python3 -m venv venv
venv/bin/python -m pip install --disable-pip-version-check --upgrade pip
venv/bin/python -m pip install --disable-pip-version-check --editable .
```

Launch from source with:

```bash
venv/bin/python -m ios_developer_toolkit
```

Do not run launch or device tests during an active backup, acquisition, location simulation, packet capture, or log collection.

## Design expectations

- Keep changes small and match the existing typed, function-oriented Python style.
- Reuse existing command, validation, runtime, and capability models before adding another path.
- Pass subprocess arguments as an argument vector; do not add shell evaluation.
- Validate external data and raise specific, actionable errors.
- Keep device selection explicit. Never silently switch to another connected target.
- Classify device mutations accurately and require confirmation for destructive or state-changing actions.
- Preserve complete raw evidence separately from filtered or formatted derivatives.
- Treat unavailable services and partial collection as coverage results, not success.
- Keep UFADE and other differently licensed tools isolated rather than importing their source or dependencies.
- Add dependencies to `pyproject.toml`, pin release-critical dependencies, and explain the need in the pull request.

## Verification

Run the complete local checks before opening a pull request:

```bash
venv/bin/python -m unittest discover -s tests -v
venv/bin/python -m compileall -q ios_developer_toolkit tests
venv/bin/python -m ios_developer_toolkit.collector --help
venv/bin/python -m ios_developer_toolkit.local_ddi --help
venv/bin/python -m ios_developer_toolkit.ipa_inspector --help
QT_QPA_PLATFORM=offscreen venv/bin/python -m ios_developer_toolkit --toolkit-internal-smoke-test
```

Documentation changes must also pass the strict site build:

```bash
venv/bin/python -m pip install --requirement requirements/docs.txt
venv/bin/python -m mkdocs build --strict --clean
```

Prefer a real, authorized integration check when the change touches device discovery, pairing, developer services, DDI handling, tunnels, backup, installation, location simulation, logging, or packet capture. State exactly which host, device family, OS version, connection path, and cleanup action were tested, without publishing a unique identifier.

Changes to packaging must additionally build the native app, run its embedded CLI and GUI smoke checks, verify the expected Mach-O architecture, and pass `codesign --verify --deep --strict`. The app must contain a matching `Contents/Resources/BOM.cdx.json`, `SOURCE_AVAILABILITY.md`, and the generated `Contents/Resources/Licenses/` inventory; `scripts/verify_release_metadata.py` enforces those links. Release assets must remain separate for Apple Silicon and Intel until a verified universal build exists, and the release workflow must retain checksums plus build-provenance and SBOM attestations.

## Pull requests

Open a focused pull request against `main`. Explain the problem, the behavior change, validation performed, device coverage, privacy impact, and any remaining limitation. CI must pass before merge. Screenshots and logs must use synthetic or thoroughly sanitized data.

By contributing, you agree that your contribution is licensed under the repository's MIT License and that community participation follows the Code of Conduct.
