# Third-party software notices

iOS Developer Toolkit's own source code is distributed under the repository's
[MIT License](LICENSE). That license does not replace the licenses of the third-party software
used to build or run the application.

## Swift packages linked into the app and `idt`

Versions are pinned in [`Package.resolved`](Package.resolved).

| Component | Version | Role | License |
|---|---:|---|---|
| [swift-nio](https://github.com/apple/swift-nio) | 2.103.0 | Networking for the lockdown connection | Apache-2.0 |
| [swift-nio-ssl](https://github.com/apple/swift-nio-ssl) | 2.37.5 | TLS for the lockdown connection | Apache-2.0; contains [BoringSSL](https://boringssl.googlesource.com/boringssl/) under the ISC and OpenSSL licenses |
| [swift-argument-parser](https://github.com/apple/swift-argument-parser) | 1.8.2 | Command-line parsing for `idt` | Apache-2.0 |
| [swift-atomics](https://github.com/apple/swift-atomics) | 1.3.1 | Dependency of swift-nio | Apache-2.0 |
| [swift-collections](https://github.com/apple/swift-collections) | 1.7.1 | Dependency of swift-nio | Apache-2.0 |
| [swift-system](https://github.com/apple/swift-system) | 1.8.1 | Dependency of swift-nio | Apache-2.0 |

Each release app contains the exact `LICENSE` and `NOTICE` files of these packages in
`Contents/Resources/Licenses/`, and each release has an SPDX SBOM generated from `Package.resolved`
(see [docs/release-verification.md](docs/release-verification.md)).

## Data

The Location Lab world map is derived from [Natural Earth](https://www.naturalearthdata.com)
1:110m land data, which is in the public domain.

## Optional external tools

[MVT](https://github.com/mvt-project/mvt), [UFADE](https://github.com/prosch88/UFADE), and
[idb Companion](https://github.com/facebook/idb) can be launched from the External Tools page if
you install them yourself. They are not bundled, linked, or included in the release SBOM, and they
remain under their own licenses (MVT under the [MVT License 1.1](https://license.mvt.re/1.1/),
idb under MIT). The app records the path and SHA-256 of the executable it launches.

## Apple tools

The app uses `devicectl`, `simctl`, `xctrace`, `xed`, and `rvictl` from the user's own Xcode
installation and macOS. They are not redistributed.

These notices describe the shipped dependency boundary and are not legal advice. Anyone who
redistributes a modified app is responsible for meeting every applicable license.
