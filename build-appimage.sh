#!/bin/bash
# build-appimage.sh — сборка AppImage из готового PyInstaller-дистрибутива.
#
# Предполагает, что dist/phototrichogram/ уже собран:
#   .venv/bin/pyinstaller build.spec --noconfirm
#
# Ничего не устанавливает в систему и не требует sudo. Единственная внешняя
# зависимость — appimagetool в PATH (см. подсказку в конце скрипта, если его
# нет — скрипт САМ ничего не скачивает, решение оставлено пользователю).
#
# Результат: phototrichogram-x86_64.AppImage в корне репозитория.

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIST_DIR="$ROOT_DIR/dist/phototrichogram"
APPDIR="$ROOT_DIR/build/Phototrichogram.AppDir"
ICON_SRC="$ROOT_DIR/assets/icon.png"

if [[ ! -x "$DIST_DIR/phototrichogram" ]]; then
    echo "Ошибка: не найден $DIST_DIR/phototrichogram" >&2
    echo "Сначала соберите дистрибутив:" >&2
    echo "    .venv/bin/pyinstaller build.spec --noconfirm" >&2
    exit 1
fi

echo "Сборка AppDir в $APPDIR ..."
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"

# Onedir-дистрибутив PyInstaller (бинарник + _internal/) копируется в
# usr/bin/ целиком — AppRun запускает бинарник изнутри этого каталога.
cp -a "$DIST_DIR/." "$APPDIR/usr/bin/"

# AppRun — точка входа AppImage. cd в usr/bin/ обязателен: PyInstaller
# onedir ищет _internal/ рядом с самим исполняемым файлом.
cat > "$APPDIR/AppRun" << 'EOF'
#!/bin/bash
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/usr/bin"
exec "$HERE/usr/bin/phototrichogram" "$@"
EOF
chmod +x "$APPDIR/AppRun"

# Desktop entry (структура и поля — см. .claude/agents/04-packaging-expert.md)
cat > "$APPDIR/phototrichogram.desktop" << 'EOF'
[Desktop Entry]
Type=Application
Name=Фототрихограмма
Comment=Анализ трихоскопических снимков
Exec=phototrichogram
Icon=phototrichogram
Categories=Science;
Terminal=false
EOF

# Иконка — плейсхолдер (см. assets/icon.png); AppImage требует её в корне
# AppDir с именем, совпадающим с Icon= из .desktop.
if [[ -f "$ICON_SRC" ]]; then
    cp "$ICON_SRC" "$APPDIR/phototrichogram.png"
else
    echo "Предупреждение: $ICON_SRC не найден — AppImage будет без иконки." >&2
fi

echo "AppDir готов: $APPDIR"

APPIMAGETOOL="${APPIMAGETOOL:-appimagetool}"
if ! command -v "$APPIMAGETOOL" >/dev/null 2>&1; then
    echo "Ошибка: appimagetool отсутствует; релиз не собран" >&2
    exit 1
fi
ARGS=()
if [[ -n "${APPIMAGE_RUNTIME:-}" ]]; then
    ARGS+=(--runtime-file "$APPIMAGE_RUNTIME")
fi
mkdir -p "$ROOT_DIR/dist"
ARCH=x86_64 "$APPIMAGETOOL" "${ARGS[@]}" "$APPDIR" "$ROOT_DIR/dist/Phototrichogram-linux-x86_64.AppImage"
