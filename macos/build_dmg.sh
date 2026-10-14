#!/bin/bash
#
# BinMirror — build del DMG (installer .pkg dentro un .dmg), stesso schema di DropCut.
#
# Produce BinMirror.app con PyInstaller, la impacchetta in un .pkg che installa
# anche il launcher in Workspace > Scripts di Resolve, e mette il .dmg sul Desktop.
#
# USO (dalla cartella del repo):   ./macos/build_dmg.sh
# REQUISITI: macOS, Python con Tcl/Tk 8.6 (python.org 3.12), Xcode Command Line Tools.
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

APP_NAME="BinMirror"
BUNDLE_ID="com.andrearicciotti.davinci.binmirror"
PKG_ID="$BUNDLE_ID.pkg"
VERSION="$(sed -nE 's/^__version__ *= *"([^"]+)".*/\1/p' binmirror/__init__.py)"
BUILD="$REPO_ROOT/build/macos"
DIST="$REPO_ROOT/dist/macos"
DESKTOP="$HOME/Desktop"
OUT_DMG="$DESKTOP/BinMirror-$VERSION.dmg"

echo "▶ BinMirror v$VERSION — build DMG"
[ "$(uname)" = "Darwin" ] || { echo "✗ Solo su macOS."; exit 1; }

# ── interprete di build: Tcl/Tk 8.6 (con Homebrew 3.14 + Tk 9 l'app non si apre) ──
pick_build_py() {
  local c
  for c in "${PYTHON:-}" /usr/local/bin/python3.12 \
           /Library/Frameworks/Python.framework/Versions/3.12/bin/python3 \
           /Library/Frameworks/Python.framework/Versions/3.13/bin/python3; do
    [ -n "$c" ] && [ -x "$c" ] || continue
    if "$c" -c 'import _tkinter,sys; sys.exit(0 if _tkinter.TK_VERSION=="8.6" else 1)' 2>/dev/null; then
      echo "$c"; return 0
    fi
  done
  return 1
}
PY="$(pick_build_py)" || {
  echo "✗ Serve un Python con Tcl/Tk 8.6 (consigliato python.org 3.12)."
  echo "  Se ce l'hai in un altro posto: PYTHON=<percorso del python3> ./macos/build_dmg.sh"
  exit 1
}
echo "▶ Build interpreter: $("$PY" -V 2>&1) → $PY"
VENV="$BUILD/venv"
mkdir -p "$BUILD"
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
# shellcheck disable=SC1091
source "$VENV/bin/activate"
python -m pip install --quiet --upgrade pip
python -m pip install --quiet "customtkinter==6.0.0" pillow pyinstaller

# ── Python portatile per l'helper Resolve (Mac senza Python/CLT) ──────────────
PYRT="$BUILD/pyruntime"
case "$(uname -m)" in
  arm64) PBS_ARCH="aarch64-apple-darwin" ;;
  *)     PBS_ARCH="x86_64-apple-darwin" ;;
esac
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14%2B20260901-$PBS_ARCH-install_only_stripped.tar.gz"
if [ ! -x "$PYRT/bin/python3" ]; then
  echo "▶ Scarico Python portatile ($PBS_ARCH)…"
  rm -rf "$PYRT" "$BUILD/pbs.tar.gz" "$BUILD/pbs_extract"
  curl -fsSL -o "$BUILD/pbs.tar.gz" "$PBS_URL"
  mkdir -p "$BUILD/pbs_extract"
  tar xzf "$BUILD/pbs.tar.gz" -C "$BUILD/pbs_extract"
  mv "$BUILD/pbs_extract/python" "$PYRT"
  rm -rf "$BUILD/pbs_extract" "$BUILD/pbs.tar.gz"
  rm -rf "$PYRT/lib/python3.12/test" "$PYRT/lib/python3.12/idlelib" \
         "$PYRT/lib/python3.12/tkinter" "$PYRT/lib/python3.12/turtledemo" \
         "$PYRT/lib/python3.12/ensurepip" "$PYRT/lib/python3.12/lib2to3" 2>/dev/null || true
  rm -rf "$PYRT"/lib/python3.12/site-packages/pip* 2>/dev/null || true
  find "$PYRT" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true
fi
echo "  ✓ python portatile: $("$PYRT/bin/python3" --version 2>&1)"

# ── test prima di impacchettare ───────────────────────────────────────────────
echo "▶ Test…"
python -m unittest discover -s tests >/dev/null 2>&1 || { python -m unittest discover -s tests; exit 1; }
echo "  ✓ test ok"

# ── PyInstaller ───────────────────────────────────────────────────────────────
echo "▶ PyInstaller…"
rm -rf "$DIST/$APP_NAME.app" "$BUILD/pyinstaller"
ICON_ARG=()
[ -f "$REPO_ROOT/macos/BinMirror.icns" ] && ICON_ARG=(--icon "$REPO_ROOT/macos/BinMirror.icns")

pyinstaller --noconfirm --clean --windowed \
  --name "$APP_NAME" \
  --osx-bundle-identifier "$BUNDLE_ID" \
  --workpath "$BUILD/pyinstaller" \
  --specpath "$BUILD" \
  --distpath "$DIST" \
  ${ICON_ARG[@]+"${ICON_ARG[@]}"} \
  --paths "$REPO_ROOT" \
  --add-data "$REPO_ROOT/binmirror/resolve_helper.py:binmirror" \
  --add-data "$PYRT:pyruntime" \
  --collect-all customtkinter \
  "$REPO_ROOT/app/binmirror_app.py"

APP="$DIST/$APP_NAME.app"
[ -d "$APP" ] || { echo "✗ Build fallita: $APP non creato."; exit 1; }

/usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $VERSION" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string $VERSION" "$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c "Set :CFBundleVersion $VERSION" "$APP/Contents/Info.plist" 2>/dev/null \
  || /usr/libexec/PlistBuddy -c "Add :CFBundleVersion string $VERSION" "$APP/Contents/Info.plist"

if [ -n "${BINMIRROR_SIGN_APP_IDENTITY:-}" ]; then
  echo "▶ Firmo l'app…"
  codesign --deep --force --options runtime --timestamp --sign "$BINMIRROR_SIGN_APP_IDENTITY" "$APP"
else
  echo "⚠ App non firmata: il postinstall toglie la quarantena, quindi su questo Mac si apre."
fi

# ── pkg ───────────────────────────────────────────────────────────────────────
echo "▶ Costruisco il .pkg…"
PKGROOT="$BUILD/pkgroot"; SCRIPTS="$BUILD/scripts"
rm -rf "$PKGROOT" "$SCRIPTS"; mkdir -p "$PKGROOT" "$SCRIPTS"
cp -R "$APP" "$PKGROOT/"
cp macos/pkg_preinstall  "$SCRIPTS/preinstall"
cp macos/pkg_postinstall "$SCRIPTS/postinstall"
chmod +x "$SCRIPTS/preinstall" "$SCRIPTS/postinstall"
PKG_OUT="$BUILD/BinMirror-$VERSION.pkg"
pkgbuild --root "$PKGROOT" --install-location /Applications --scripts "$SCRIPTS" \
  --identifier "$PKG_ID" --version "$VERSION" "$PKG_OUT"

if [ -n "${BINMIRROR_SIGN_INSTALLER_IDENTITY:-}" ]; then
  SIGNED="$BUILD/BinMirror-$VERSION-signed.pkg"
  productsign --sign "$BINMIRROR_SIGN_INSTALLER_IDENTITY" "$PKG_OUT" "$SIGNED"
  mv "$SIGNED" "$PKG_OUT"
fi

# ── dmg ───────────────────────────────────────────────────────────────────────
echo "▶ Costruisco il .dmg…"
DMGSTAGE="$BUILD/dmgstage"
rm -rf "$DMGSTAGE"; mkdir -p "$DMGSTAGE" "$DESKTOP"
cp "$PKG_OUT" "$DMGSTAGE/"
rm -f "$OUT_DMG"
hdiutil create -volname "BinMirror" -srcfolder "$DMGSTAGE" -ov -format UDZO "$OUT_DMG" >/dev/null

echo ""
echo "✅ Fatto: $OUT_DMG"
echo "   Apri il DMG → doppio clic sul .pkg → poi in Resolve: Workspace > Scripts > BinMirror"
