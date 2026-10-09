# Repository workflow

- Follow CONTRIBUTING.md, SECURITY.md, and docs/release-verification.md. Preserve the required Tests and Analyze Python checks and conversation resolution.
- Use .github/workflows/ci.yml for complete source validation. Packaging changes also require frozen-macos-smoke.yml's Apple Silicon and Intel verification; documentation changes require the strict documentation build.
- A v* tag builds release artifacts. Publishing requires separate release authorization and human approval in the protected github-release environment; attestations and checksums do not establish notarization.
- Documentation pushes and PRs build only. Publishing Pages requires an authorized manual dispatch from main and human approval in github-pages.
- Report physical-device validation separately from offscreen, packaged, and static checks. Keep device identifiers, private evidence, and credentials out of public artifacts.
