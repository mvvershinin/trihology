# Phototrichogram

A Russian-language desktop application for measuring hair characteristics in
scalp images captured with a USB microscope. It combines image analysis,
patient records, examination history, and printable reports in one local tool.

Built for trichoscopy workflows and for evaluating an affordable alternative
to commercial analysis software. Measurements depend on image quality and
microscope calibration. The application is an aid for a specialist; it does
not establish a diagnosis or replace clinical judgement.

**[Download a release](https://github.com/mvvershinin/trihology/releases/latest)** ·
**[Installation and first launch](docs/INSTALL.md)** ·
**[Русская документация](README.ru.md)** ·
**[Руководство пользователя](docs/USER_GUIDE.md)**

## What it does

- Captures images from UVC microscopes: V4L2 on Linux; DirectShow/MSMF on Windows.
- Loads image files and measures hair density, shaft thickness, and the
  Terminal / Intermediate / Vellus distribution.
- Analyzes frontal, parietal, temporal, and occipital zones, including comparison
  with an occipital reference zone.
- Supports microscope calibration, rooted counting, and averaging image series.
- Stores patient profiles and examinations in SQLite, with images and history charts.
- Displays reference ranges selected using patient demographics, with their limitations.
- Exports PDF reports with Cyrillic fonts and provides database backup/restore.

All interface text is in Russian. Data is stored locally, outside the application
folder, in `~/.phototrichogram` (`%USERPROFILE%\.phototrichogram` on Windows).

## Download and run

Open [Releases](https://github.com/mvvershinin/trihology/releases/latest), expand
**Assets**, and download the file for your system. The automatically generated
“Source code” archives are not runnable application packages.

| System | Download | Launch |
|---|---|---|
| Windows 10/11 x64 | `Phototrichogram-win64.zip` | Extract the entire ZIP and run `phototrichogram.exe` |
| Linux x86_64 | `Phototrichogram-linux-x86_64.AppImage` | Make it executable and run it |

Python, SQLite, and Python libraries do not need to be installed for these packages.
Releases also include `SHA256SUMS` and `build-info.json`.
See the [installation guide](docs/INSTALL.md) for first-launch prompts, Linux
FUSE/camera permissions, verification, updates, and troubleshooting.

Automated package checks exercise the interface, synthetic analysis, SQLite,
and PDF generation without a physical camera. **Windows operation with a real
USB microscope has not yet been accepted by the owner.** A successful build
is not evidence of measurement accuracy or hardware compatibility.

## Data storage and updates

Linux AppImage, Windows ZIP, and source launches share the current OS user's
local data folder, independently of the application version or location:

| System | Default data folder |
|---|---|
| Linux | `~/.phototrichogram/` |
| Windows | `%USERPROFILE%\.phototrichogram\` (usually `C:\Users\YourName\.phototrichogram\`) |

Contents: `phototrichogram.db` (SQLite patients, examinations, and results),
`config.json` (settings), `images/` (originals and annotations), and `reports/`
(default PDF folder). If a different database path is configured, its managed
`images/` folder is always beside that actual database. Imported pictures are
copied there; relative references work after moving the database and images
together. The report folder can be selected separately. Data stays on your
computer and is not included in release packages.

A new version therefore opens records created by an earlier version or source
launch. Replacing or deleting the application package does not delete your data.
Before updating, use the application's full `.ptg` backup (database and linked
original/annotated pictures). The legacy `.db` format contains only the database;
keep its images separately. Settings and exported PDFs are not in the `.ptg`
archive. In History, select an examination row and click **Снимки** (Images).
Avoid running
multiple copies against the same data folder simultaneously.

For a separate, initially empty profile, set `PHOTOTRICHOGRAM_DATA_DIR` before
launch. It works for packaged and source launches.

Linux:

```bash
PHOTOTRICHOGRAM_DATA_DIR="$HOME/.phototrichogram-test" ./Phototrichogram-linux-x86_64.AppImage
```

Windows PowerShell, from the extracted application folder:

```powershell
$env:PHOTOTRICHOGRAM_DATA_DIR = "$env:USERPROFILE\.phototrichogram-test"
.\phototrichogram.exe
```

The original data remains intact. Reusing the test folder reopens its records;
choose a new folder for another empty profile. To return to the default profile,
launch normally from a new terminal without that environment variable.

## Development

Python 3.11+; tkinter/ttk, OpenCV/NumPy, SQLite, matplotlib, fpdf2.
Linux development setup:

```bash
sudo apt install python3-venv python3-tk
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m phototrichogram
```

A connected microscope is optional when opening saved images. Calibrate your
own microscope before relying on measurements; the default scale belongs to
one particular microscope.

```bash
.venv/bin/pytest -q
.venv/bin/ruff check phototrichogram tests tools run_phototrichogram.py
.venv/bin/mypy phototrichogram
.venv/bin/lint-imports
```

[Architecture](docs/ARCHITECTURE.md), [methodology](docs/METHODOLOGY.md),
[backlog](docs/todo/BACKLOG.md), and [release plan](docs/todo/2026-11-07_планы-и-github-релизы.md)
are maintained in Russian. Roadmap stage numbers are not release version numbers.

## Building releases

A `vX.Y.Z` tag runs checks and builds both platforms before publishing a complete
GitHub Release. A manual Release workflow run builds trial artifacts without
publishing. Windows uses native PyInstaller onedir; Linux uses PyInstaller
and AppImage on an Ubuntu 22.04 base. UPX is disabled.

Linux uses the same recipe locally and in CI:

```bash
docker run --rm -v "$PWD:/project" -w /project \
  -e PHOTOTRICHOGRAM_VERSION=1.0.2 ubuntu:22.04 \
  bash tools/build_linux_release.sh
```

Native Windows packaging:

```powershell
python -m pip install -r requirements-dev.txt -c constraints-release.txt
python tools/release.py build 1.0.2
python tools/release.py windows
```

The older Linux-to-Windows runtime packager remains an alternative;
see [Windows build details](docs/BUILD_WINDOWS.md) in Russian.

## AI-assisted development

Original Claude instructions are in `CLAUDE.md` and `.claude/`; they are preserved
separately from local Codex adapters. The owner's local workspace also has
`AGENTS.md`, `.codex/agents/`, `.codex/config.toml`, and `.agents/skills/`.
These adapters are not yet tracked in this repository or included in binaries;
they do not synchronize automatically with Claude instructions.

In that configured Codex workspace, start Codex from the project root and invoke
`$orchestrate`, `$develop-cv`, `$develop-gui`, `$develop-db`, `$package`, `$validate`,
`$review`, or `$test-code` followed by the task. These correspond to the same
Claude slash commands. `/skills` lists available skills. Models and reasoning
settings are inherited; Claude model names do not select Codex models. Claude
hooks and `allowed-tools` are not Codex execution or security settings.
Full local setup notes are preserved in the [Russian README](README.ru.md).

## License

MIT. Bundled dependencies retain their own licenses.
