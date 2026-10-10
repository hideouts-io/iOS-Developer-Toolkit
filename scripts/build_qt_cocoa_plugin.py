"""Build the Qt 6.9.3 Cocoa plugin with the two upstream parent-ownership guards.

Only the platform plugin is compiled, against Qt's checksum-pinned universal SDK.
The supplied Python must contain the official PySide6/Shiboken 6.9.3 wheels; it is
never modified. Output includes retained LGPL source, licenses, build recipe and
a closed, hash-checked manifest. Imports and validation never build or download.
Run: python3 scripts/build_qt_cocoa_plugin.py --output OUTPUT --python VENV/bin/python
"""

from __future__ import annotations

import argparse
import fcntl
import gzip
import hashlib
from http.client import HTTPMessage
import io
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import sys
import tarfile
import time
from typing import BinaryIO
import urllib.request


QT_VERSION = "6.9.3"
SOURCE_REVISION = "be09b211db70e1b0155d05c18668ad76e7e3df51"
SOURCE_URL = "https://download.qt.io/archive/qt/6.9/6.9.3/submodules/qtbase-everywhere-src-6.9.3.tar.xz"
SOURCE_SHA256 = "c5a1a2f660356ec081febfa782998ae5ddbc5925117e64f50e4be9cd45b8dc6e"
SOURCE_SIZE = 49856272
SDK_URL = (
    "https://download.qt.io/online/qtsdkrepository/mac_x64/desktop/qt6_693/qt6_693/"
    "qt.qt6.693.clang_64/6.9.3-0-202509261207"
    "qtbase-MacOS-MacOS_15-Clang-MacOS-MacOS_15-X86_64-ARM64.7z"
)
SDK_SHA256 = "805144e619b7c7b0e9c985d929e1f290241d88b884218db3ad7d56bd579d6b4a"
SDK_SIZE = 34052196
PATCH_SHA256 = "993efc63f5dd0698e81c59af46422f5608beae762c741c6089abeb6d0310e02d"
ORIGINAL_ACCESSIBILITY_SHA256 = "3d0ed975719b4f0d5fe0495aefb4522fa06de57f52b24816495c3e5024685443"
PATCHED_ACCESSIBILITY_SHA256 = "61e42f2835adfc5b7a8eae8326ad6ad7ae050c12e3e10a147db41ead74d1aa9b"
ORIGINAL_WHEEL_PLUGIN_SHA256 = "6f495674500a27b3333b8359e0499a57574dcf714a03654e27428c48418a1f4c"
WHEEL_QTCORE_SHA256 = "a10b511c558b4e28cee002ab3d38906e8b152547433def60b07d8bc624f7fdc0"
WHEEL_QTGUI_SHA256 = "61eebec4ffb210d951916e6484b9e3d34bc3228dfe77ef74b5036bb4b3efdda8"
UPSTREAM_FIX_URL = "https://codereview.qt-project.org/c/qt/qtbase/+/765434"
UPSTREAM_FIX_REVISION = "c7fd3f34b997bb363be15650647665b3b6b8a5f4"
SCRIPT_DIRECTORY = Path(__file__).resolve().parent
BUILDER_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
VERIFIER_SHA256 = hashlib.sha256((SCRIPT_DIRECTORY / "verify_qt_cocoa_accessibility.py").read_bytes()).hexdigest()

CMAKE_PROJECT = '''cmake_minimum_required(VERSION 3.22)
project(ToolkitCocoaRepair VERSION 6.9.3 LANGUAGES CXX OBJCXX)
find_package(Qt6 6.9.3 EXACT REQUIRED COMPONENTS Core Gui)
set(CMAKE_AUTOMOC ON)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_OBJCXX_STANDARD 17)
foreach(framework Foundation AppKit Carbon CoreServices CoreVideo IOKit IOSurface Metal QuartzCore UniformTypeIdentifiers)
    find_library(FW${framework} ${framework} REQUIRED)
endforeach()
# Use public Qt CMake APIs to consume the unchanged, pinned Cocoa source list.
function(qt_internal_add_plugin target)
    cmake_parse_arguments(arg "" "OUTPUT_NAME;PLUGIN_TYPE" "DEFAULT_IF;SOURCES;DEFINES;LIBRARIES" ${ARGN})
    qt_add_plugin(${target} SHARED PLUGIN_TYPE ${arg_PLUGIN_TYPE} CLASS_NAME ${target} OUTPUT_NAME ${arg_OUTPUT_NAME})
    target_sources(${target} PRIVATE ${arg_SOURCES})
    target_compile_definitions(${target} PRIVATE ${arg_DEFINES})
    target_link_libraries(${target} PRIVATE ${arg_LIBRARIES})
endfunction()
function(qt_disable_apple_app_extension_api_only target)
    set_target_properties(${target} PROPERTIES XCODE_ATTRIBUTE_APPLICATION_EXTENSION_API_ONLY NO)
endfunction()
function(qt_internal_add_resource target name)
    qt_add_resources(${target} ${name} ${ARGN})
endfunction()
function(qt_internal_extend_target target)
    cmake_parse_arguments(arg "" "CONDITION" "SOURCES" ${ARGN})
    if(${arg_CONDITION})
        target_sources(${target} PRIVATE ${arg_SOURCES})
    endif()
endfunction()
add_subdirectory("${QT_SOURCE_DIRECTORY}/src/plugins/platforms/cocoa" cocoa)
set_target_properties(QCocoaIntegrationPlugin PROPERTIES
    LIBRARY_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/output"
    BUILD_WITH_INSTALL_RPATH ON INSTALL_RPATH "@loader_path/../../lib")
'''


def mapping(value: object, keys: set[str], name: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"Invalid closed {name} manifest schema")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError(f"Non-string key in {name}")
        result[key] = item
    return result


def digest_value(value: object, name: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"Invalid SHA-256 in {name}")
    return value


def file_record(value: object, filename: str, name: str) -> dict[str, object]:
    record = mapping(value, {"file", "sha256"}, name)
    if record["file"] != filename:
        raise ValueError(f"Unexpected {name} artifact filename")
    digest_value(record["sha256"], name)
    return record


def json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate key in Cocoa provenance JSON")
        result[name] = value
    return result


def validated_manifest(data: bytes) -> dict[str, object]:
    """Validate closed provenance and fixed pins without output-directory access."""
    if len(data) > 65536:
        raise ValueError("Cocoa provenance manifest exceeds 64 KiB")
    manifest = mapping(json.loads(data, object_pairs_hook=json_object), {
        "schema_version", "qt_version", "upstream_fix", "source", "sdk", "patch", "builder",
        "verifier", "wheel", "plugin", "sources", "licenses", "validation",
    }, "Cocoa provenance")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != 1 or manifest["qt_version"] != QT_VERSION:
        raise ValueError("Unsupported Cocoa provenance version")
    fixed: dict[str, dict[str, object]] = {
        "upstream_fix": {"url": UPSTREAM_FIX_URL, "revision": UPSTREAM_FIX_REVISION},
        "source": {"url": SOURCE_URL, "sha256": SOURCE_SHA256, "size": SOURCE_SIZE,
                   "revision": SOURCE_REVISION, "original_accessibility_sha256": ORIGINAL_ACCESSIBILITY_SHA256,
                   "patched_accessibility_sha256": PATCHED_ACCESSIBILITY_SHA256},
        "sdk": {"url": SDK_URL, "sha256": SDK_SHA256, "size": SDK_SIZE},
        "patch": {"file": "qt-cocoa-ownership-6.9.3.patch", "sha256": PATCH_SHA256},
        "builder": {"file": "build_qt_cocoa_plugin.py", "sha256": BUILDER_SHA256},
        "verifier": {"file": "verify_qt_cocoa_accessibility.py", "sha256": VERIFIER_SHA256},
    }
    for name, expected in fixed.items():
        if mapping(manifest[name], set(expected), name) != expected:
            raise ValueError(f"Cocoa {name} provenance differs from reviewed pins")
    wheel = mapping(manifest["wheel"], {"pyside_version", "original_plugin_sha256", "qtcore_sha256", "qtgui_sha256"}, "wheel")
    if wheel["pyside_version"] != QT_VERSION or wheel["original_plugin_sha256"] != ORIGINAL_WHEEL_PLUGIN_SHA256:
        raise ValueError("Cocoa wheel provenance differs from the official 6.9.3 artifact")
    if wheel["qtcore_sha256"] != WHEEL_QTCORE_SHA256 or wheel["qtgui_sha256"] != WHEEL_QTGUI_SHA256:
        raise ValueError("Cocoa wheel framework hashes differ from the official 6.9.3 artifact")
    plugin = mapping(manifest["plugin"], {"file", "sha256", "architectures", "min_macos", "dependencies"}, "plugin")
    if plugin["file"] != "libqcocoa.dylib" or plugin["architectures"] != ["arm64", "x86_64"]:
        raise ValueError("Cocoa artifact must contain both required architectures")
    digest_value(plugin["sha256"], "plugin")
    if mapping(plugin["min_macos"], {"arm64", "x86_64"}, "deployment targets") != {"arm64": "13.0", "x86_64": "13.0"}:
        raise ValueError("Cocoa deployment targets differ from the macOS 13 contract")
    dependencies = plugin["dependencies"]
    if not isinstance(dependencies, list) or not dependencies or len(dependencies) > 64:
        raise ValueError("Invalid Cocoa dependency inventory")
    for dependency in dependencies:
        if not isinstance(dependency, str) or not dependency.startswith(("/System/Library/Frameworks/", "/usr/lib/", "@rpath/QtCore.framework/", "@rpath/QtGui.framework/")):
            raise ValueError("Cocoa plugin contains an unreviewed runtime dependency")
    file_record(manifest["sources"], "sources.tar.gz", "source archive")
    licenses = manifest["licenses"]
    if not isinstance(licenses, list) or not licenses or len(licenses) > 64:
        raise ValueError("Invalid Cocoa license inventory")
    seen: set[str] = set()
    for entry in licenses:
        record = mapping(entry, {"file", "sha256"}, "license")
        name = record["file"]
        if not isinstance(name, str) or re.fullmatch(r"licenses/[A-Za-z0-9_.-]+", name) is None or name in seen:
            raise ValueError("Invalid or duplicate Cocoa license filename")
        seen.add(name)
        digest_value(record["sha256"], "license")
    if "licenses/LGPL-3.0-only.txt" not in seen:
        raise ValueError("Cocoa LGPL license is missing")
    validation = mapping(manifest["validation"], {"build", "native_selection", "native_placeholder_teardown", "native_architecture", "intel_runtime"}, "validation")
    for name in ("build", "native_selection", "native_placeholder_teardown"):
        if validation[name] != "passed":
            raise ValueError("Cocoa output lacks successful native ownership validation")
    native_architecture = validation["native_architecture"]
    if native_architecture not in ("arm64", "x86_64") or validation["intel_runtime"] != ("passed" if native_architecture == "x86_64" else "not_run"):
        raise ValueError("Cocoa runtime validation architecture is inconsistent")
    return manifest


def file_digest(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular Cocoa artifact: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1048576), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_recipe_files() -> dict[str, bytes]:
    """Return the exact small source-archive members reviewed by this builder."""
    return {
        "scripts/build_qt_cocoa_plugin.py": Path(__file__).read_bytes(),
        "scripts/verify_qt_cocoa_accessibility.py": (SCRIPT_DIRECTORY / "verify_qt_cocoa_accessibility.py").read_bytes(),
        "scripts/qt-cocoa-ownership-6.9.3.patch": (SCRIPT_DIRECTORY / "qt-cocoa-ownership-6.9.3.patch").read_bytes(),
        "CMakeLists.txt": CMAKE_PROJECT.encode(),
        "pins.json": json.dumps({"source_url": SOURCE_URL, "source_sha256": SOURCE_SHA256, "source_revision": SOURCE_REVISION,
                                 "sdk_url": SDK_URL, "sdk_sha256": SDK_SHA256, "patch_sha256": PATCH_SHA256}, sort_keys=True, indent=2).encode(),
    }


def validated_source_archive(path: Path, manifest: dict[str, object]) -> None:
    """Verify the six regular retained-source members without extracting them.

    The immutable Qtbase source is bounded to its published size. Every recipe
    member must match the reviewed local bytes, so recomputing an outer digest
    cannot authorize a changed patch, recipe, source payload or extra member.
    """
    validated = validated_manifest(json.dumps(manifest).encode())
    source_record = file_record(validated["sources"], "sources.tar.gz", "source archive")
    if path.is_symlink() or not path.is_file() or path.stat().st_size > SOURCE_SIZE + 2097152:
        raise ValueError("Cocoa corresponding-source archive is missing, unsafe or oversized")
    if file_digest(path) != source_record["sha256"]:
        raise ValueError("Cocoa corresponding-source archive differs from its manifest")
    recipes = archive_recipe_files()
    expected = {name: (len(data), hashlib.sha256(data).hexdigest()) for name, data in recipes.items()}
    expected["qtbase-everywhere-src-6.9.3.tar.xz"] = (SOURCE_SIZE, SOURCE_SHA256)
    seen: set[str] = set()
    with tarfile.open(path, mode="r|gz") as archive:
        for member in archive:
            if member.name not in expected or member.name in seen or not member.isfile():
                raise ValueError("Cocoa corresponding-source archive has an unexpected, duplicate or nonregular member")
            size, digest = expected[member.name]
            if member.size != size or member.pax_headers:
                raise ValueError("Cocoa corresponding-source member has unexpected size or extended metadata")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("Cocoa corresponding-source archive member cannot be read")
            observed = hashlib.sha256()
            with stream:
                for chunk in iter(lambda: stream.read(1048576), b""):
                    observed.update(chunk)
            if observed.hexdigest() != digest:
                raise ValueError("Cocoa corresponding-source member differs from its reviewed bytes")
            seen.add(member.name)
        padding = archive.fileobj.read(10241)
        if len(padding) > 10240 or any(padding):
            raise ValueError("Cocoa corresponding-source archive has unexpected trailing data")
    if seen != set(expected):
        raise ValueError("Cocoa corresponding-source archive is missing a required member")


def validated_output(output: Path) -> dict[str, object]:
    """Read and verify cached artifacts; never download, build, or launch a GUI."""
    if output.is_symlink() or not output.is_dir():
        raise ValueError("Cocoa output must be a real directory")
    path = output / "manifest.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Cocoa output has no regular completed manifest")
    manifest = validated_manifest(path.read_bytes())
    records = [mapping(manifest[name], {"file", "sha256", "architectures", "min_macos", "dependencies"} if name == "plugin" else {"file", "sha256"}, name) for name in ("plugin", "sources")]
    license_entries = manifest["licenses"]
    if not isinstance(license_entries, list):
        raise ValueError("Invalid license inventory")
    records.extend(mapping(item, {"file", "sha256"}, "license") for item in license_entries)
    for record in records:
        name = record["file"]
        if not isinstance(name, str) or file_digest(output / name) != record["sha256"]:
            raise ValueError("Cocoa output artifact differs from its completed manifest")
    if file_digest(output / "qt-cocoa-ownership-6.9.3.patch") != PATCH_SHA256:
        raise ValueError("Cocoa output patch differs from the reviewed bytes")
    if file_digest(output / "build_qt_cocoa_plugin.py") != BUILDER_SHA256:
        raise ValueError("Cocoa output builder differs from the reviewed bytes")
    if file_digest(output / "verify_qt_cocoa_accessibility.py") != VERIFIER_SHA256:
        raise ValueError("Cocoa output native verifier differs from the reviewed bytes")
    validated_source_archive(output / "sources.tar.gz", manifest)
    return manifest


class TLSRedirects(urllib.request.HTTPRedirectHandler):
    """Allow Qt's mirrors only through certificate-verified HTTPS redirects."""

    def redirect_request(self, req: urllib.request.Request, fp: BinaryIO, code: int,
                         msg: str, headers: HTTPMessage, newurl: str) -> urllib.request.Request | None:
        if not newurl.startswith("https://"):
            raise RuntimeError("Refusing a non-HTTPS Qt artifact redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def download(url: str, expected: str, size: int, path: Path) -> None:
    if path.exists():
        if path.stat().st_size != size or file_digest(path) != expected:
            raise ValueError("Cached Qt artifact differs from its published size or SHA-256")
        return
    opener = urllib.request.build_opener(TLSRedirects())
    started = time.monotonic()
    partial = path.with_suffix(path.suffix + ".part")
    digest = hashlib.sha256()
    received = 0
    with opener.open(url, timeout=45) as response, partial.open("xb") as stream:
        while chunk := response.read(1048576):
            received += len(chunk)
            if received > size or time.monotonic() - started > 180:
                raise RuntimeError("Qt artifact download exceeded its size or total deadline")
            digest.update(chunk)
            stream.write(chunk)
    if received != size or digest.hexdigest() != expected:
        raise ValueError("Qt artifact failed published SHA-256 verification")
    partial.rename(path)


def tool(name: str) -> str:
    result = shutil.which(name)
    if result is None:
        raise RuntimeError(f"Required Cocoa build tool is missing from PATH: {name}")
    return result


def run_logged(command: list[str], directory: Path, log: Path, environment: dict[str, str], timeout: int) -> None:
    with log.open("wb") as stream:
        result = subprocess.run(command, cwd=directory, env=environment, stdout=stream,
                                stderr=subprocess.STDOUT, timeout=timeout, check=False)
    if result.returncode != 0:
        detail = log.read_text(errors="replace")[-4000:]
        raise RuntimeError(f"Cocoa command failed with exit {result.returncode}; {log.name}:\n{detail}")


def extract_sdk(archive: Path, destination: Path, seven_zip: str) -> None:
    listing = subprocess.check_output([seven_zip, "l", "-slt", str(archive)], text=True, timeout=30)
    for line in listing.splitlines():
        if line.startswith("Path = ") and line[7:] != str(archive):
            path = PurePosixPath(line[7:])
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Unsafe path in the checksum-verified Qt SDK archive")
    destination.mkdir()
    subprocess.run([seven_zip, "x", "-y", "-bd", f"-o{destination}", str(archive)],
                   check=True, stdout=subprocess.DEVNULL, timeout=60)
    for entry in destination.rglob("*"):
        if entry.is_symlink() and not entry.resolve().is_relative_to(destination.resolve()):
            raise ValueError("Qt SDK contains a symbolic link outside its extraction directory")


def wheel_directory(python: Path) -> Path:
    command = [str(python), "-c", "import PySide6,shiboken6; from PySide6.QtCore import qVersion; "
               "from pathlib import Path; assert PySide6.__version__ == shiboken6.__version__ == qVersion() == '6.9.3'; "
               "print(Path(PySide6.__file__).parent / 'Qt')"]
    result = subprocess.check_output(command, text=True, timeout=20).strip()
    return Path(result).resolve(strict=True)


def plugin_metadata(plugin: Path) -> dict[str, object]:
    architectures = subprocess.check_output(["/usr/bin/lipo", "-archs", str(plugin)], text=True, timeout=20).split()
    if set(architectures) != {"arm64", "x86_64"}:
        raise ValueError("The repaired Cocoa plugin is not universal")
    dependencies: list[str] = []
    for architecture in ("arm64", "x86_64"):
        output = subprocess.check_output(["/usr/bin/xcrun", "vtool", "-arch", architecture, "-show-build", str(plugin)], text=True, timeout=20)
        if re.search(r"\bminos\s+13\.0\b", output) is None:
            raise ValueError("The repaired Cocoa plugin exceeds or differs from the macOS 13 deployment target")
        load_commands = subprocess.check_output(["/usr/bin/otool", "-arch", architecture, "-l", str(plugin)], text=True, timeout=20)
        paths = re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.+) \(offset", load_commands)
        if paths != ["@loader_path/../../lib"]:
            raise ValueError("Cocoa plugin contains an unexpected runtime search path")
        linkage = subprocess.check_output(["/usr/bin/otool", "-arch", architecture, "-L", str(plugin)], text=True, timeout=20)
        names = [line.strip().split(" (", 1)[0] for line in linkage.splitlines()[1:] if line.startswith("\t")]
        if dependencies and dependencies != names:
            raise ValueError("Cocoa plugin slices have different runtime dependencies")
        dependencies = names
    return {"file": "libqcocoa.dylib", "sha256": file_digest(plugin), "architectures": ["arm64", "x86_64"],
            "min_macos": {"arm64": "13.0", "x86_64": "13.0"}, "dependencies": dependencies}


def source_archive(output: Path, source: Path) -> None:
    """Retain exact original source plus patch and recipe, with no build-host paths."""
    files = archive_recipe_files()
    files["qtbase-everywhere-src-6.9.3.tar.xz"] = source.read_bytes()
    with (output / "sources.tar.gz").open("wb") as stream, gzip.GzipFile(fileobj=stream, mode="wb", mtime=0, filename="") as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, data in sorted(files.items()):
                member = tarfile.TarInfo(name)
                member.size = len(data)
                member.mode = 0o644
                archive.addfile(member, io.BytesIO(data))


def build(output: Path, python: Path) -> dict[str, object]:
    qt = wheel_directory(python)
    original_plugin = qt / "plugins/platforms/libqcocoa.dylib"
    original_hash = file_digest(original_plugin)
    qtcore = file_digest(qt / "lib/QtCore.framework/Versions/A/QtCore")
    qtgui = file_digest(qt / "lib/QtGui.framework/Versions/A/QtGui")
    if qtcore != WHEEL_QTCORE_SHA256 or qtgui != WHEEL_QTGUI_SHA256:
        raise ValueError("Input Qt frameworks are not the reviewed official 6.9.3 wheels")
    if (output / "manifest.json").exists():
        existing = validated_output(output)
        wheel = mapping(existing["wheel"], {"pyside_version", "original_plugin_sha256", "qtcore_sha256", "qtgui_sha256"}, "wheel")
        plugin = mapping(existing["plugin"], {"file", "sha256", "architectures", "min_macos", "dependencies"}, "plugin")
        if wheel["qtcore_sha256"] != qtcore or wheel["qtgui_sha256"] != qtgui or original_hash not in (ORIGINAL_WHEEL_PLUGIN_SHA256, plugin["sha256"]):
            raise ValueError("Cached Cocoa output differs from the supplied wheel runtime")
        return existing
    if original_hash != ORIGINAL_WHEEL_PLUGIN_SHA256:
        raise ValueError("Input Cocoa plugin is not the reviewed official PySide6 6.9.3 plugin")
    patch = SCRIPT_DIRECTORY / "qt-cocoa-ownership-6.9.3.patch"
    if file_digest(patch) != PATCH_SHA256:
        raise ValueError("Cocoa ownership patch differs from the reviewed bytes")
    shutil.copyfile(patch, output / patch.name)
    shutil.copyfile(Path(__file__), output / "build_qt_cocoa_plugin.py")
    shutil.copyfile(SCRIPT_DIRECTORY / "verify_qt_cocoa_accessibility.py", output / "verify_qt_cocoa_accessibility.py")
    downloads = output / "downloads"
    downloads.mkdir(exist_ok=True)
    source_artifact = downloads / "qtbase-everywhere-src-6.9.3.tar.xz"
    sdk_artifact = downloads / "qtbase-sdk-6.9.3.7z"
    download(SOURCE_URL, SOURCE_SHA256, SOURCE_SIZE, source_artifact)
    download(SDK_URL, SDK_SHA256, SDK_SIZE, sdk_artifact)
    work = output / "work"
    work.mkdir()
    source_parent = work / "source"
    source_parent.mkdir()
    with tarfile.open(source_artifact) as archive:
        archive.extractall(source_parent, filter="data")
    source = source_parent / "qtbase-everywhere-src-6.9.3"
    if (source / ".tag").read_text().strip() != SOURCE_REVISION:
        raise ValueError("Qt source revision differs from the reviewed release")
    sdk = work / "sdk"
    extract_sdk(sdk_artifact, sdk, tool("7zz"))
    accessibility = source / "src/plugins/platforms/cocoa/qcocoaaccessibilityelement.mm"
    if file_digest(accessibility) != ORIGINAL_ACCESSIBILITY_SHA256:
        raise ValueError("Qt accessibility source differs from the reviewed release")
    subprocess.run(["/usr/bin/patch", "--batch", "--forward", "-p1", "-i", str(patch)], cwd=source, check=True, timeout=20)
    if file_digest(accessibility) != PATCHED_ACCESSIBILITY_SHA256:
        raise ValueError("Patched Qt accessibility source differs from the reviewed result")
    project = work / "project"
    project.mkdir()
    (project / "CMakeLists.txt").write_text(CMAKE_PROJECT)
    compilation = work / "build"
    environment = os.environ.copy()
    cmake = tool("cmake")
    flags = f"-ffile-prefix-map={source}=qtbase -ffile-prefix-map={sdk}=qt-sdk"
    command = [cmake, "-S", str(project), "-B", str(compilation), "-G", "Unix Makefiles",
               f"-DCMAKE_PREFIX_PATH={sdk}", f"-DQT_SOURCE_DIRECTORY={source}", "-DCMAKE_BUILD_TYPE=Release",
               "-DCMAKE_OSX_ARCHITECTURES=arm64;x86_64", "-DCMAKE_OSX_DEPLOYMENT_TARGET=13.0",
               f"-DCMAKE_INSTALL_PREFIX={work / 'local-install'}", f"-DCMAKE_CXX_FLAGS={flags}", f"-DCMAKE_OBJCXX_FLAGS={flags}"]
    run_logged(command, work, output / "configure.log", environment, 120)
    run_logged([cmake, "--build", str(compilation), "--target", "QCocoaIntegrationPlugin", "--parallel", "6"],
               work, output / "compile.log", environment, 600)
    plugin = output / "libqcocoa.dylib"
    shutil.copyfile(compilation / "output/libqcocoa.dylib", plugin)
    plugin_record = plugin_metadata(plugin)
    probe_plugin = work / "native-plugins/platforms/libqcocoa.dylib"
    probe_plugin.parent.mkdir(parents=True)
    probe_plugin.symlink_to(plugin)
    environment.pop("NSZombieEnabled", None)
    run_logged([str(python), str(SCRIPT_DIRECTORY / "verify_qt_cocoa_accessibility.py"), "--expected-plugin", str(probe_plugin)],
               work, output / "native-ownership.log", environment, 45)
    licenses = output / "licenses"
    licenses.mkdir()
    license_records: list[dict[str, object]] = []
    for license_file in sorted((source / "LICENSES").iterdir()):
        if not license_file.is_file() or license_file.is_symlink():
            raise ValueError("Unexpected file in Qt's source license inventory")
        target = licenses / license_file.name
        shutil.copyfile(license_file, target)
        license_records.append({"file": f"licenses/{target.name}", "sha256": file_digest(target)})
    source_archive(output, source_artifact)
    manifest: dict[str, object] = {
        "schema_version": 1, "qt_version": QT_VERSION,
        "upstream_fix": {"url": UPSTREAM_FIX_URL, "revision": UPSTREAM_FIX_REVISION},
        "source": {"url": SOURCE_URL, "sha256": SOURCE_SHA256, "size": SOURCE_SIZE, "revision": SOURCE_REVISION,
                   "original_accessibility_sha256": ORIGINAL_ACCESSIBILITY_SHA256, "patched_accessibility_sha256": PATCHED_ACCESSIBILITY_SHA256},
        "sdk": {"url": SDK_URL, "sha256": SDK_SHA256, "size": SDK_SIZE},
        "patch": {"file": patch.name, "sha256": PATCH_SHA256},
        "builder": {"file": Path(__file__).name, "sha256": BUILDER_SHA256},
        "verifier": {"file": "verify_qt_cocoa_accessibility.py", "sha256": VERIFIER_SHA256},
        "wheel": {"pyside_version": QT_VERSION, "original_plugin_sha256": ORIGINAL_WHEEL_PLUGIN_SHA256,
                  "qtcore_sha256": qtcore, "qtgui_sha256": qtgui},
        "plugin": plugin_record, "sources": {"file": "sources.tar.gz", "sha256": file_digest(output / "sources.tar.gz")},
        "licenses": license_records,
        "validation": {"build": "passed", "native_selection": "passed", "native_placeholder_teardown": "passed",
                       "native_architecture": platform.machine(), "intel_runtime": "passed" if platform.machine() == "x86_64" else "not_run"},
    }
    data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode()
    validated_manifest(data)
    (output / "manifest.json").write_bytes(data)
    return validated_output(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--python", required=True, type=Path)
    arguments = parser.parse_args()
    if sys.platform != "darwin" or sys.version_info < (3, 13):
        raise RuntimeError("The Cocoa repair builder requires macOS and Python 3.13 for safe archive extraction")
    output = arguments.output.absolute()
    if output.is_symlink():
        raise ValueError("Cocoa output must not be a symbolic link")
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".build.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("Another Cocoa plugin build is active for this output directory") from error
        manifest = build(output, arguments.python.absolute())
    print(json.dumps({"qt_version": manifest["qt_version"], "output": str(output), "validation": manifest["validation"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
