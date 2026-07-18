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
Categories=Science;Medical;
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

if command -v appimagetool >/dev/null 2>&1; then
    echo "Найден appimagetool — собираю AppImage..."
    ARCH=x86_64 appimagetool "$APPDIR" "$ROOT_DIR/phototrichogram-x86_64.AppImage"
    echo "Готово: $ROOT_DIR/phototrichogram-x86_64.AppImage"
else
    cat << 'EOF'

appimagetool не найден в PATH — AppImage не собран (сама сборка PyInstaller
и AppDir выше уже готовы).

Получить appimagetool (без установки в систему, инструмент — просто
исполняемый файл):
    wget -O ~/bin/appimagetool \
        https://github.com/AppImage/appimagetool/releases/latest/download/appimagetool-x86_64.AppImage
    chmod +x ~/bin/appimagetool
    # ~/bin должен быть в PATH

Затем повторите:
    bash build-appimage.sh

Или соберите вручную:
    ARCH=x86_64 appimagetool build/Phototrichogram.AppDir phototrichogram-x86_64.AppImage

Если appimagetool ругается на отсутствие FUSE (частый случай в контейнерах/
песочницах без /dev/fuse) — используйте:
    ARCH=x86_64 appimagetool --appimage-extract-and-run build/Phototrichogram.AppDir ...
EOF
fi
