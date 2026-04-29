#!/bin/bash
# Builds rrresponseq.app as a universal2 (Intel + Apple Silicon) macOS app.
# Uses /usr/local/bin/python3.12 (python.org universal2) via arch -x86_64 so
# all C extensions compile for x86_64; the resulting .app runs natively on
# Intel and via Rosetta on Apple Silicon.
#
# Usage:
#   ./build.sh            → local dev build (copies banks.json into app)
#   ./build.sh --release  → release build (clears personal MIDI ports, zips)

set -e

UNI_PYTHON="/usr/local/bin/python3.12"
BUILD_ENV="./build_env_x86"
BUILD_PYTHON="$BUILD_ENV/bin/python3"
export MACOSX_DEPLOYMENT_TARGET="10.13"

echo "Building rrresponseq macOS app (universal x86_64 / arm64 via Rosetta)..."

# ── Create x86_64 venv if needed ─────────────────────────────────────────────
if [ ! -f "$BUILD_PYTHON" ]; then
    echo "Creating x86_64 build env at $BUILD_ENV ..."
    if [ ! -f "$UNI_PYTHON" ]; then
        echo "ERROR: $UNI_PYTHON not found. Install python.org Python 3.12."
        exit 1
    fi
    arch -x86_64 "$UNI_PYTHON" -m venv "$BUILD_ENV"
fi

echo "Installing dependencies..."
arch -x86_64 "$BUILD_PYTHON" -m pip install --quiet --upgrade pip setuptools wheel
arch -x86_64 "$BUILD_PYTHON" -m pip install --quiet py2app pywebview flask python-rtmidi mido

# python-rtmidi builds x86_64 only from pip wheel; merge with arm64 from the native venv
# so the bundle's _rtmidi.so is universal2 and runs on both Intel and Apple Silicon.
ARM64_SO="./venv/lib/python3.12/site-packages/rtmidi/_rtmidi.cpython-312-darwin.so"
X86_SO="$BUILD_ENV/lib/python3.12/site-packages/rtmidi/_rtmidi.cpython-312-darwin.so"
if [ -f "$ARM64_SO" ] && [ -f "$X86_SO" ]; then
    ARM_ARCH=$(file "$ARM64_SO" | grep -o "arm64")
    X86_ARCH=$(file "$X86_SO" | grep -o "x86_64")
    if [ "$ARM_ARCH" = "arm64" ] && [ "$X86_ARCH" = "x86_64" ]; then
        lipo -create "$ARM64_SO" "$X86_SO" -output "$X86_SO"
        echo "python-rtmidi: universal2 OK"
    fi
fi

chmod -R u+w build dist rrresponseq-macOS 2>/dev/null || true
rm -rf build dist rrresponseq-macOS

echo "Building app..."
arch -x86_64 "$BUILD_PYTHON" setup.py py2app

if [ ! -d "dist/rrresponseq.app" ]; then
    echo "Build failed — dist/rrresponseq.app not found"
    exit 1
fi

mkdir -p rrresponseq-macOS
mv dist/rrresponseq.app rrresponseq-macOS/
rm -rf dist build

# Restore user banks for local dev use
if [ -f "banks.json" ] && [ "$1" != "--release" ]; then
    cp banks.json rrresponseq-macOS/rrresponseq.app/Contents/Resources/banks.json
    echo "Restored banks.json"
fi

if [ "$1" == "--release" ]; then
    # Clear personal MIDI ports from bundled settings.json
    SETTINGS_IN_APP="rrresponseq-macOS/rrresponseq.app/Contents/Resources/settings.json"
    if [ -f "$SETTINGS_IN_APP" ]; then
        python3 -c "
import json
with open('$SETTINGS_IN_APP') as f:
    s = json.load(f)
for k in ('MIDI_OUT_PORT','MIDI_OUT_PORT2','NK_IN_PORT','NK_OUT_PORT',
          'CTRL_IN2_PORT','LAUNCHPAD_PORT','MIDI_KB_PORT','MIDI_CLK_PORT'):
    if k in s:
        s[k] = None
with open('$SETTINGS_IN_APP', 'w') as f:
    json.dump(s, f, indent=2)
print('settings.json: MIDI ports cleared for release')
"
    fi
    VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "v0.1.0")
    ZIP_NAME="rrresponseq-macOS-${VERSION}.zip"
    rm -f "$ZIP_NAME"
    zip -r "$ZIP_NAME" rrresponseq-macOS/
    echo "Release zip ready: $ZIP_NAME"
    file "rrresponseq-macOS/rrresponseq.app/Contents/MacOS/rrresponseq"
else
    echo "App ready: rrresponseq-macOS/rrresponseq.app"
    file "rrresponseq-macOS/rrresponseq.app/Contents/MacOS/rrresponseq"
fi
