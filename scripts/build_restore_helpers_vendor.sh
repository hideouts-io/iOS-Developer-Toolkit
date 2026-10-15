#!/bin/bash
# Builds the firmware helpers bundled with iOS Developer Toolkit (Python): `idevicerestore` and
# `irecovery` from the libimobiledevice project, as universal (arm64 + x86_64) executables that
# link every third-party library statically and depend only on macOS system libraries.
#
#   scripts/build_restore_helpers_vendor.sh [OUTPUT_DIR]
#
# OUTPUT_DIR (default build-output/restore-helpers/out) receives:
#   bin/idevicerestore, bin/irecovery      universal executables
#   licenses/<project>/…                   each project's license and notices
#   SOURCES.txt                            the exact source of every component
#   STAMP                                  SHA-256 of this script, so a build is reused only
#                                          while the pinned sources and steps are unchanged
#
# Every source is pinned: git projects to a commit, OpenSSL to a release tarball and its published
# SHA-256. Needs Xcode, and autoconf, automake, libtool, pkg-config, and cmake (Homebrew). Builds
# happen under build-output/restore-helpers; nothing is installed on the system.
#
# The helpers are separate programs: the app runs them through CommandRunner and never links them.
# idevicerestore is LGPL-3.0; the libimobiledevice libraries are LGPL-2.1; libzip is BSD-3-Clause;
# OpenSSL is Apache-2.0.
set -euo pipefail

fail() { echo "build-restore-helpers: $*" >&2; exit 1; }
step() { echo "==> $*"; }

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WORK="$ROOT/build-output/restore-helpers"
OUT="${1:-$WORK/out}"
SRC="$WORK/src"
MIN_MACOS=14.0
JOBS="$(sysctl -n hw.ncpu)"
TLS_PATCH="$ROOT/scripts/restore_helpers_verified_tls.patch"
TLS_PATCH_SHA256=18609e43bbe67b71af34e5a1c52959f3e9f988a4d5dc26cab8e2a335b10c11c7
[[ -f "$TLS_PATCH" && ! -L "$TLS_PATCH" ]] || fail "verified HTTPS source patch is missing or is a symlink"
[[ "$(shasum -a 256 "$TLS_PATCH" | cut -d' ' -f1)" == "$TLS_PATCH_SHA256" ]] \
  || fail "verified HTTPS source patch does not match the reviewed digest"
STAMP="$(shasum -a 256 "$ROOT/scripts/build_restore_helpers_vendor.sh" | cut -d' ' -f1)"
STATE="$WORK/cache-$STAMP"

for homebrew_bin in /opt/homebrew/bin /usr/local/bin; do
  if [[ -d "$homebrew_bin" ]]; then PATH="$homebrew_bin:$PATH"; fi
done
export PATH

mkdir -p "$WORK"
LOCK="$WORK/.build-lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  [[ -d "$LOCK" ]] || fail "cannot create build lock $LOCK"
  owner="unknown"
  if [[ -f "$LOCK/pid" ]]; then read -r owner < "$LOCK/pid"; fi
  fail "another helper build owns $LOCK (PID $owner). Check that process before removing a stale lock."
fi
printf '%s\n' "$$" > "$LOCK/pid"
trap 'rm -f "$LOCK/pid"; rmdir "$LOCK"' EXIT

if [[ -f "$OUT/STAMP" && "$(cat "$OUT/STAMP")" == "$STAMP" \
  && -x "$OUT/bin/idevicerestore" && -x "$OUT/bin/irecovery" \
  && -f "$OUT/SOURCES.txt" && -d "$OUT/licenses" ]]; then
  step "Reusing firmware helpers: $OUT"
  exit 0
fi
if [[ -e "$OUT" || -L "$OUT" ]]; then
  fail "refusing to replace an existing helper output; choose a fresh output directory"
fi

# name|git URL|commit|version label
GIT_SOURCES=(
  "libplist|https://github.com/libimobiledevice/libplist.git|32428abacb909988e8e960a8845a6430b17b6a60|2.7.0-git"
  "libimobiledevice-glue|https://github.com/libimobiledevice/libimobiledevice-glue.git|da770a7687f35fbb981db4d7b47b1b032cd5c2c7|1.3.2-git"
  "libusbmuxd|https://github.com/libimobiledevice/libusbmuxd.git|93eb168bf6b07472d17781328c21df0c60300524|2.1.1-git"
  "libtatsu|https://github.com/libimobiledevice/libtatsu.git|e7d6ad13ef928aa609d0ccdfc586f7d6e8e049bf|1.0.5-git"
  "libimobiledevice|https://github.com/libimobiledevice/libimobiledevice.git|fa0f79190142bc309307967c058f89c1b36eb6b8|1.4.0-git"
  "libirecovery|https://github.com/libimobiledevice/libirecovery.git|93c117c29b1f6669bc4ceca8b84e1df06449fe33|1.3.1-git"
  "idevicerestore|https://github.com/libimobiledevice/idevicerestore.git|60192e97f87d1bbab5c493684e0a245b0966363f|1.0.0-git"
  "libzip|https://github.com/nih-at/libzip.git|6f8a0cdd24a0dc6cce9dac4a7679da784ab124ea|1.11.4"
)
OPENSSL_VERSION=3.5.8
OPENSSL_URL="https://github.com/openssl/openssl/releases/download/openssl-$OPENSSL_VERSION/openssl-$OPENSSL_VERSION.tar.gz"
OPENSSL_SHA256=a8f84a39918ec6415ce765d9b429d313ba97b8143169c172e734b9514464f5b2

for tool in autoconf autom4te automake glibtoolize pkg-config cmake git xcrun lipo; do
  command -v "$tool" >/dev/null || fail "$tool is missing (brew install autoconf automake libtool pkg-config cmake)"
done
SDK="$(xcrun --sdk macosx --show-sdk-path)"

# Autoconf 2.73's Perl exec handoff stalls on this macOS host. A local copy uses
# a child process and preserves its exit status; installed tools and source pins are unchanged.
if [[ "$(autoconf --version | head -1)" == "autoconf (GNU Autoconf) 2.73" ]]; then
  step "Preparing the build-local Autoconf 2.73 process-launch compatibility patch"
  mkdir -p "$STATE/tools"
  /usr/bin/perl -0777 -e '
    my $source = <>;
    my $original = q{exec {$autom4te_command[0]} @autom4te_command;};
    my $replacement = q{my $status = system {$autom4te_command[0]} @autom4te_command;
exit ($status == -1 ? 127 : $status & 127 ? 128 + ($status & 127) : $status >> 8);};
    my $count = $source =~ s/\Q$original\E/$replacement/g;
    die "Unsupported Autoconf 2.73 launch contract\n" unless $count == 1;
    print $source;
  ' "$(command -v autoconf)" > "$STATE/tools/autoconf.pl"
  cat > "$STATE/tools/autoconf" <<'EOF'
#!/bin/bash
set -euo pipefail
/usr/bin/perl "$(dirname "$0")/autoconf.pl" "$@"
EOF
  chmod +x "$STATE/tools/autoconf"
  printf -v AUTOCONF '%q' "$STATE/tools/autoconf"
  export AUTOCONF
  PATH="$STATE/tools:$PATH"
  export PATH
fi

verify_source_cache() {
  local dir="$1" commit="$2" version="$3"
  [[ "$(git -C "$dir" rev-parse HEAD)" == "$commit" ]] || fail "$dir is not at its pinned commit"
  git -C "$dir" diff --quiet HEAD -- || fail "tracked source cache differs from its pinned commit: $dir"
  [[ "$(git -C "$dir" ls-files --others)" == ".tarball-version" ]] \
    || fail "source cache contains unexpected untracked files: $dir (only .tarball-version is allowed)"
  [[ -f "$dir/.tarball-version" && ! -L "$dir/.tarball-version" \
    && "$(cat "$dir/.tarball-version")" == "$version" ]] || fail "source cache version label differs from the pin: $dir"
}

fetch_sources() {
  mkdir -p "$SRC"
  for entry in "${GIT_SOURCES[@]}"; do
    IFS='|' read -r name url commit version <<<"$entry"
    local dir="$SRC/$name"
    if [[ -d "$dir/.git" && "$(git -C "$dir" rev-parse HEAD)" == "$commit" ]]; then
      verify_source_cache "$dir" "$commit" "$version"
      continue
    fi
    [[ ! -e "$dir" && ! -L "$dir" ]] || fail "existing source cache is not at its pinned commit: $dir; preserve it and choose a fresh cache"
    step "Fetching $name @ ${commit:0:12}"
    mkdir -p "$dir"
    git -C "$dir" init -q
    git -C "$dir" fetch -q --depth 1 "$url" "$commit"
    git -C "$dir" -c advice.detachedHead=false checkout -q FETCH_HEAD
    [[ "$(git -C "$dir" rev-parse HEAD)" == "$commit" ]] || fail "$name is not at the pinned commit"
    echo "$version" > "$dir/.tarball-version"
    verify_source_cache "$dir" "$commit" "$version"
  done
  local tarball="$SRC/openssl-$OPENSSL_VERSION.tar.gz"
  if [[ ! -f "$tarball" ]]; then
    step "Downloading OpenSSL $OPENSSL_VERSION"
    curl --proto '=https' --proto-redir '=https' -fsSL -o "$tarball.part" "$OPENSSL_URL"
    mv "$tarball.part" "$tarball"
  fi
  echo "$OPENSSL_SHA256  $tarball" | shasum -a 256 -c - >/dev/null || fail "OpenSSL tarball checksum mismatch"
}

# Apply only to private architecture build copies, with exact source bytes and patch counts.
apply_verified_tls_patch() {
  local name="$1" build="$2" file digest expected_counts source_digests
  local patch_args=()
  case "$name" in
    libtatsu)
      expected_counts=$'23\t10\tsrc/tss.c'
      source_digests='src/tss.c|6445249d87dcf6c42a054b7d60116fefa8bb984275fa613aebc4754d7e8fda10'
      patch_args=(--include=src/tss.c)
      ;;
    idevicerestore)
      expected_counts=$'49\t9\tsrc/download.c\n4\t0\tsrc/download.h\n30\t6\tsrc/restore.c\n4\t6\tsrc/idevicerestore.c'
      source_digests='src/download.c|b648d22304c7c52a76eaf8e0972a68d525208fef76900402181d64ba94c6a9fe
src/download.h|8d68ea2adb6cb6257a38c7cb2696267df44f09508902daff044d38dc49bfed12
src/restore.c|b138a28b51251ee5b117ae1bd3304b1beaa78821b377c698b344af2ffcefb08d
src/idevicerestore.c|dc66cebeff9096dad46aa87e5bfc5aea4f4a2b7c46dae4972d15b134e46e7a1d'
      patch_args=(--include=src/download.c --include=src/download.h --include=src/restore.c --include=src/idevicerestore.c)
      ;;
    *) return 0 ;;
  esac
  while IFS='|' read -r file digest; do
    [[ "$(shasum -a 256 "$build/$file" | cut -d' ' -f1)" == "$digest" ]] \
      || fail "$name TLS patch source digest differs: $file"
  done <<<"$source_digests"
  [[ "$(git -C "$build" apply -p2 --numstat "${patch_args[@]}" "$TLS_PATCH")" == "$expected_counts" ]] \
    || fail "$name TLS patch file or change counts differ from the reviewed contract"
  git -C "$build" apply -p2 --check "${patch_args[@]}" "$TLS_PATCH" || fail "$name TLS patch does not apply exactly"
  git -C "$build" apply -p2 "${patch_args[@]}" "$TLS_PATCH" || fail "$name TLS patch could not be applied"
}

# autotools project: name, extra configure flags…
build_autotools() {
  local arch="$1" name="$2"; shift 2
  local prefix="$STATE/prefix-$arch" build="$STATE/build-$arch/$name"
  [[ -f "$build/.done" ]] && return 0
  step "[$arch] $name"
  rm -rf "$build"; mkdir -p "$build"
  cp -R "$SRC/$name/." "$build/"
  apply_verified_tls_patch "$name" "$build"
  (
    cd "$build"
    export PKG_CONFIG_PATH="$prefix/lib/pkgconfig" PKG_CONFIG_LIBDIR="$prefix/lib/pkgconfig"
    export CC="$(xcrun -f clang) -arch $arch -isysroot $SDK -mmacosx-version-min=$MIN_MACOS"
    export CFLAGS="-O2" CPPFLAGS="-I$prefix/include" LDFLAGS="-L$prefix/lib"
    export LIBTOOLIZE=glibtoolize
    NOCONFIGURE=1 ./autogen.sh >"$build/autogen.log" 2>&1 || { tail -30 "$build/autogen.log"; exit 1; }
    ./configure --host="$([[ $arch == arm64 ]] && echo aarch64 || echo x86_64)-apple-darwin" --prefix="$prefix" \
      --enable-static --disable-shared "$@" >"$build/configure.log" 2>&1 || { tail -40 "$build/configure.log"; exit 1; }
    make -j"$JOBS" >"$build/make.log" 2>&1 || { grep -E "error" "$build/make.log" | head -20; tail -20 "$build/make.log"; exit 1; }
    make install >"$build/install.log" 2>&1
  )
  touch "$build/.done"
}

build_openssl() {
  local arch="$1" prefix="$STATE/prefix-$arch" build="$STATE/build-$arch/openssl"
  [[ -f "$build/.done" ]] && return 0
  step "[$arch] OpenSSL $OPENSSL_VERSION"
  rm -rf "$build"; mkdir -p "$build"
  tar -xzf "$SRC/openssl-$OPENSSL_VERSION.tar.gz" -C "$build" --strip-components 1
  (
    cd "$build"
    ./Configure "darwin64-$arch-cc" no-shared no-tests no-docs no-module --prefix="$prefix" --libdir=lib \
      -isysroot "$SDK" -mmacosx-version-min="$MIN_MACOS" >"$build/configure.log" 2>&1 || { tail -30 "$build/configure.log"; exit 1; }
    make -j"$JOBS" build_libs >"$build/make.log" 2>&1 || { tail -30 "$build/make.log"; exit 1; }
    make install_dev >"$build/install.log" 2>&1
  )
  touch "$build/.done"
}

build_libzip() {
  local arch="$1" prefix="$STATE/prefix-$arch" build="$STATE/build-$arch/libzip"
  [[ -f "$build/.done" ]] && return 0
  step "[$arch] libzip"
  rm -rf "$build"; mkdir -p "$build"
  cmake -S "$SRC/libzip" -B "$build" -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
    -DCMAKE_OSX_ARCHITECTURES="$arch" -DCMAKE_OSX_DEPLOYMENT_TARGET="$MIN_MACOS" -DCMAKE_OSX_SYSROOT="$SDK" \
    -DCMAKE_INSTALL_PREFIX="$prefix" -DCMAKE_INSTALL_LIBDIR=lib \
    -DENABLE_BZIP2=OFF -DENABLE_LZMA=OFF -DENABLE_ZSTD=OFF -DENABLE_GNUTLS=OFF -DENABLE_MBEDTLS=OFF \
    -DENABLE_OPENSSL=OFF -DENABLE_COMMONCRYPTO=ON -DBUILD_TOOLS=OFF -DBUILD_REGRESS=OFF -DBUILD_OSSFUZZ=OFF \
    -DBUILD_EXAMPLES=OFF -DBUILD_DOC=OFF >"$build/cmake.log" 2>&1 || { tail -30 "$build/cmake.log"; exit 1; }
  cmake --build "$build" -j "$JOBS" >"$build/make.log" 2>&1 || { tail -30 "$build/make.log"; exit 1; }
  cmake --install "$build" >"$build/install.log" 2>&1
  touch "$build/.done"
}

# pkg-config files for the libraries macOS provides (curl and zlib from the SDK).
system_pkgconfig() {
  local prefix="$STATE/prefix-$1"
  mkdir -p "$prefix/lib/pkgconfig"
  cat > "$prefix/lib/pkgconfig/libcurl.pc" <<EOF
Name: libcurl
Description: libcurl (macOS SDK)
Version: 8.0.0
Libs: -lcurl
Cflags:
EOF
  cat > "$prefix/lib/pkgconfig/zlib.pc" <<EOF
Name: zlib
Description: zlib (macOS SDK)
Version: 1.2.12
Libs: -lz
Cflags:
EOF
}

build_arch() {
  local arch="$1"
  system_pkgconfig "$arch"
  build_openssl "$arch"
  build_libzip "$arch"
  build_autotools "$arch" libplist --without-cython --without-tests
  build_autotools "$arch" libimobiledevice-glue
  build_autotools "$arch" libusbmuxd --without-preflight
  build_autotools "$arch" libtatsu
  build_autotools "$arch" libimobiledevice --without-cython --enable-debug=no
  build_autotools "$arch" libirecovery --with-tools
  build_autotools "$arch" idevicerestore
}

fetch_sources
for arch in arm64 x86_64; do build_arch "$arch"; done

step "Combining architectures"
[[ ! -e "$OUT" && ! -L "$OUT" ]] || fail "the helper output appeared during the build; refusing to overwrite it"
mkdir -p "$OUT/bin" "$OUT/licenses"
for tool in idevicerestore irecovery; do
  lipo -create "$STATE/prefix-arm64/bin/$tool" "$STATE/prefix-x86_64/bin/$tool" -output "$OUT/bin/$tool"
  archs="$(lipo -archs "$OUT/bin/$tool")"
  [[ " $archs " == *" arm64 "* && " $archs " == *" x86_64 "* ]] || fail "$tool is not universal ($archs)"
  # Only macOS system libraries may remain dynamic.
  # (otool prints a header line ending in ":" for each architecture.)
  if otool -L "$OUT/bin/$tool" | grep -v ':$' | awk '{print $1}' | grep -v -E '^(/usr/lib/|/System/Library/)'; then
    fail "$tool links a non-system library (listed above)"
  fi
done

step "Licenses and sources"
for entry in "${GIT_SOURCES[@]}"; do
  IFS='|' read -r name url commit version <<<"$entry"
  mkdir -p "$OUT/licenses/$name"
  for file in COPYING COPYING.LESSER LICENSE AUTHORS NOTICE; do
    [[ -f "$SRC/$name/$file" ]] && cp "$SRC/$name/$file" "$OUT/licenses/$name/"
  done
  echo "$name $version $url @ $commit" >> "$OUT/SOURCES.txt"
done
mkdir -p "$OUT/licenses/openssl"
tar -xzf "$SRC/openssl-$OPENSSL_VERSION.tar.gz" -C "$OUT/licenses/openssl" --strip-components 1 "openssl-$OPENSSL_VERSION/LICENSE.txt"
echo "openssl $OPENSSL_VERSION $OPENSSL_URL sha256 $OPENSSL_SHA256" >> "$OUT/SOURCES.txt"
printf '%s\n' "$STAMP" > "$OUT/STAMP"

step "Done: $OUT"
ls -l "$OUT/bin"
