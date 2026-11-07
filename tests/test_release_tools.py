"""Отказы релизной упаковки на неверной версии и неполных артефактах."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "release_tools", Path(__file__).resolve().parents[1] / "tools/release.py",
)
assert SPEC is not None and SPEC.loader is not None
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


@pytest.mark.parametrize("value", ["v1", "main", "v1.0.0-rc1", "1.0.0;bad"])
def test_invalid_version(value: str) -> None:
    """Неподдерживаемый тег не должен начинать релиз."""
    with pytest.raises(ValueError):
        release.version(value)


def test_missing_artifact(tmp_path: Path) -> None:
    """Одна успешная платформа не образует релиз."""
    (tmp_path / "Phototrichogram-win64.zip").write_bytes(b"zip")
    with pytest.raises(ValueError, match="Нет артефакта"):
        release.manifest(tmp_path)


def test_manifest_checks_provenance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Иной коммит и иной тег не проходят, верный набор получает проверяемый хеш."""
    monkeypatch.setattr(release.subprocess, "check_output", lambda *a, **kw: "abc\n")
    monkeypatch.setenv("RELEASE_VERSION", "1.0.0")
    for name in ("Phototrichogram-win64.zip", "Phototrichogram-linux-x86_64.AppImage"):
        (tmp_path / name).write_bytes(b"artifact")
    for platform in ("linux", "windows"):
        (tmp_path / f"build-info-{platform}.json").write_text(
            json.dumps({"version": "1.0.0", "revision": "wrong"}),
        )
    with pytest.raises(ValueError, match="не из текущего коммита"):
        release.manifest(tmp_path)
    for platform in ("linux", "windows"):
        (tmp_path / f"build-info-{platform}.json").write_text(
            json.dumps({"version": "2.0.0", "revision": "abc"}),
        )
    with pytest.raises(ValueError, match="не соответствует тегу"):
        release.manifest(tmp_path)
    monkeypatch.setenv("RELEASE_VERSION", "2.0.0")
    release.manifest(tmp_path)
    assert hashlib.sha256(b"artifact").hexdigest() in (tmp_path / "SHA256SUMS").read_text()


def test_missing_changelog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Версия без описания изменений не публикуется."""
    (tmp_path / "CHANGELOG.md").write_text("# Изменения\n\n## 1.0.0\nСделано\n")
    monkeypatch.setattr(release, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="Нет раздела"):
        release.notes("2.0.0")
