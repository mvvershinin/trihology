#!/bin/bash
# Единый рецепт: docker ubuntu:22.04 или release job с тем же контейнером.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive PYTHONUTF8=1
apt-get update -qq
apt-get install -y --no-install-recommends binutils gnupg ca-certificates curl git xvfb xauth libglib2.0-0 libgl1 file
# Ключ с проверенным fingerprint хранится в репозитории: импорт gpg-agent не нужен.
gpg --show-keys --with-colons tools/deadsnakes.asc | grep -q 'F23C5A6CF475977595C89F51BA6932366A755776'
cp tools/deadsnakes.asc /usr/share/keyrings/phototrichogram-deadsnakes.asc
echo 'deb [signed-by=/usr/share/keyrings/phototrichogram-deadsnakes.asc] https://ppa.launchpadcontent.net/deadsnakes/ppa/ubuntu jammy main' > /etc/apt/sources.list.d/phototrichogram-python.list
apt-get update -qq
apt-get install -y --no-install-recommends python3.12 python3.12-venv python3.12-tk
python3.12 -m venv build/release-venv
PYTHON=build/release-venv/bin/python
"$PYTHON" -m pip install -r requirements-dev.txt -c constraints-release.txt
"$PYTHON" tools/release.py build "${PHOTOTRICHOGRAM_VERSION:?Не задана версия}"
"$PYTHON" tools/check_glibc.py dist/phototrichogram
mkdir -p build/appimage-tools
curl -fLsS https://github.com/AppImage/appimagetool/releases/download/1.9.1/appimagetool-x86_64.AppImage -o build/appimage-tools/appimagetool
curl -fLsS https://github.com/AppImage/type2-runtime/releases/download/20251108/runtime-x86_64 -o build/appimage-tools/runtime-x86_64
echo 'ed4ce84f0d9caff66f50bcca6ff6f35aae54ce8135408b3fa33abfc3cb384eb0  build/appimage-tools/appimagetool' | sha256sum -c -
echo '2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d  build/appimage-tools/runtime-x86_64' | sha256sum -c -
chmod +x build/appimage-tools/appimagetool
export APPIMAGE_EXTRACT_AND_RUN=1
export APPIMAGETOOL="$PWD/build/appimage-tools/appimagetool"
export APPIMAGE_RUNTIME="$PWD/build/appimage-tools/runtime-x86_64"
bash build-appimage.sh
# Исполняется именно содержимое готового AppImage, вне дерева исходников.
APPIMAGE="$PWD/dist/Phototrichogram-linux-x86_64.AppImage"
CHECK_DIR=$(mktemp -d)
trap 'rm -rf "$CHECK_DIR"' EXIT
(cd "$CHECK_DIR" && "$APPIMAGE" --appimage-extract >/dev/null && xvfb-run -a ./squashfs-root/AppRun --smoke-test "$CHECK_DIR/smoke.json")
"$PYTHON" tools/check_smoke.py "$CHECK_DIR/smoke.json" "$PHOTOTRICHOGRAM_VERSION"
cp dist/phototrichogram/build-info.json dist/build-info-linux.json
