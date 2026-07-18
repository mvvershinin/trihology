#!/bin/bash
# install-udev.sh — установка udev-правила для USB-микроскопа Phototrichogram.
#
# Зачем: устройства /dev/video* по умолчанию принадлежат root:video с
# правами 0660. На части систем сессионный ACL (systemd-logind, тег
# "uaccess") даёт доступ автоматически, но это не гарантировано (зависит от
# окружения рабочего стола, способа входа и конкретного USB-микроскопа) —
# поэтому нужно собственное явное правило, а не полагаться на совпадение.
#
# Почему группа video, а не MODE="0666" (мир на запись):
# - video — стандартная группа Debian/Ubuntu для /dev/video* (uvcvideo и
#   штатные udev-правила системы уже используют root:video 0660 — наше
#   правило просто гарантирует это же для конкретно нашего микроскопа,
#   а не открывает более широкий доступ, чем уже принят в дистрибутиве);
# - 0666 (мир на запись) даёт доступ к камере ЛЮБОМУ локальному пользователю
#   системы — это лишняя приватность/security-поверхность (кто угодно на
#   многопользовательской машине смог бы захватывать кадры с микроскопа).
#   GROUP=video + MODE=0660 достаточно и безопаснее для той же цели —
#   работать без sudo.
#
# Правило класса SUBSYSTEM=="video4linux" (а не привязка к конкретным
# ATTRS{idVendor}/idProduct) — сознательный выбор: приложение поддерживает
# любой UVC-совместимый USB-микроскоп (см. README.md), а не одну конкретную
# модель с известным заранее vendor/product ID. При необходимости сузить
# правило только под свой микроскоп — узнать ID через `lsusb` и добавить
# строки ATTRS{idVendor}=="XXXX", ATTRS{idProduct}=="YYYY" в файл правила
# после установки.
#
# Скрипт требует sudo (пишет в /etc/udev/rules.d/ и перезагружает правила
# udev) — самостоятельно себя не запускает, вызывается пользователем вручную:
#   sudo bash install-udev.sh

set -euo pipefail

RULES_FILE="/etc/udev/rules.d/99-phototrichogram.rules"
RULE_CONTENT='SUBSYSTEM=="video4linux", GROUP="video", MODE="0660"'

if [[ $EUID -ne 0 ]]; then
    echo "Ошибка: скрипт требует прав root (нужно писать в /etc/udev/rules.d/)." >&2
    echo "Запустите: sudo bash install-udev.sh" >&2
    exit 1
fi

echo "Установка udev-правила для USB-микроскопа Phototrichogram..."
echo "$RULE_CONTENT" > "$RULES_FILE"
echo "Правило записано: $RULES_FILE"

echo "Перезагрузка правил udev..."
udevadm control --reload-rules
udevadm trigger --subsystem-match=video4linux

echo
echo "Готово."
echo

# Определить реального (не root) пользователя, вызвавшего sudo — ему нужно
# членство в группе video, чтобы правило реально сработало.
TARGET_USER="${SUDO_USER:-$USER}"

if id -nG "$TARGET_USER" 2>/dev/null | grep -qw video; then
    echo "Пользователь '$TARGET_USER' уже состоит в группе video — всё готово."
    echo "Переподключите микроскоп (или просто повторно откройте /dev/video*)."
else
    echo "ВАЖНО: пользователь '$TARGET_USER' пока НЕ состоит в группе video."
    echo "Без этого правило GROUP=\"video\" доступа без sudo не даст."
    echo "Выполните:"
    echo "    sudo usermod -aG video $TARGET_USER"
    echo "После этого нужно выйти из сессии и войти заново (или перезагрузиться) —"
    echo "членство в группах применяется при новом входе в систему."
fi

echo
echo "Проверить права можно командой:  ls -la /dev/video*"
echo "Временный воркараунд без udev-правила (до перезагрузки, каждый раз заново):"
echo "    sudo chmod a+rw /dev/video*"
