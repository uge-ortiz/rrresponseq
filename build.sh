#!/bin/bash

VENV_PYTHON="./venv/bin/python3"

echo "Building rrresponseq macOS app..."

if [ ! -f "$VENV_PYTHON" ]; then
    echo "venv/bin/python3 not found"
    exit 1
fi

echo "Installing dependencies..."
$VENV_PYTHON -m pip install --quiet setuptools py2app pywebview pygame mido flask 2>/dev/null || true

chmod -R u+w build dist rrresponseq-macOS 2>/dev/null || true
rm -rf build dist rrresponseq-macOS

echo "Building app..."
$VENV_PYTHON setup.py py2app

if [ ! -d "dist/rrresponseq.app" ]; then
    echo "Build failed"
    exit 1
fi

mkdir -p rrresponseq-macOS
mv dist/rrresponseq.app rrresponseq-macOS/
rm -rf dist build

# Restore user banks for local use
if [ -f "banks.json" ] && [ "$1" != "--release" ]; then
    cp banks.json rrresponseq-macOS/rrresponseq.app/Contents/Resources/banks.json
    echo "Restored your banks.json"
fi

if [ "$1" == "--release" ]; then
    rm -f rrresponseq-macOS-v0.1.0.zip
    zip -r rrresponseq-macOS-v0.1.0.zip rrresponseq-macOS/
    echo "Release zip ready: rrresponseq-macOS-v0.1.0.zip"
else
    echo "App ready: rrresponseq-macOS/rrresponseq.app"
fi
