# Installation and first launch

[Русское руководство](USER_GUIDE.md) · [Download releases](https://github.com/mvvershinin/trihology/releases/latest)

Phototrichogram has a Russian-language interface. Ready-made packages include
Python and their required Python libraries; SQLite does not need a separate
installation. Download the application asset, not the “Source code” archive.

## Windows 10/11 x64

1. Download `Phototrichogram-win64.zip` from the release's **Assets** list.
2. Extract the entire archive to a folder you can write to. Keep its files together.
3. Open that folder and double-click `phototrichogram.exe`.
4. The program is unsigned. If SmartScreen appears, first verify that the file
   came from the expected repository/release. Windows may offer **More info** →
   **Run anyway** for the file you intended to run.
5. Connect a UVC USB microscope, open **Захват** (Capture), refresh the camera
   list, and connect. In **Файл → Настройки** (File → Settings), DirectShow and
   Media Foundation are alternative camera backends.

Do not run the executable directly from the ZIP viewer. Administrator privileges
are not required to launch the program. Actual Windows/microscope compatibility
still requires a manual check; cloud runner tests do not provide that evidence.

## Linux x86_64

Download `Phototrichogram-linux-x86_64.AppImage`, then:

```bash
chmod +x Phototrichogram-linux-x86_64.AppImage
./Phototrichogram-linux-x86_64.AppImage
```

The release is built on Ubuntu 22.04; older Linux distributions are not supported
by this recipe. A desktop session with an X11/Wayland display is required.
If FUSE support is unavailable, try the AppImage runtime's extraction mode:

```bash
./Phototrichogram-linux-x86_64.AppImage --appimage-extract-and-run
```

The microscope must be accessible through video4linux. If your account lacks
camera access, see the [Russian guide](USER_GUIDE.md) and the repository's
`install-udev.sh`; configuring device permissions may require administrator
rights. Opening saved images does not require a camera.

## Verify the download

Download `SHA256SUMS` from the same release. On Linux, compare the AppImage hash:

```bash
sha256sum Phototrichogram-linux-x86_64.AppImage
```

On Windows PowerShell:

```powershell
Get-FileHash .\Phototrichogram-win64.zip -Algorithm SHA256
```

Compare the output with the corresponding filename in `SHA256SUMS`.
`build-info.json` records the version, source revision, and build dependencies.

## Data, updates, and troubleshooting

Patient records, settings, images, and reports are stored outside the program:

- Windows: `%USERPROFILE%\.phototrichogram`
- Linux: `~/.phototrichogram`

Before updating, close the application and use **Файл → Резервная копия базы**
(File → Database backup). Select the full `.ptg` archive: it includes the database and all linked originals
and annotations. Legacy `.db` backups contain only the database, so retain their
`images/` folder separately. Settings and exported PDF reports are not included
in `.ptg`; preserve them separately. Extract the
new version into a separate folder; do not overwrite or remove the data folder.
Keep the old application folder for rollback. If a version changes the database
schema, rollback may also require restoring a compatible database backup.
Do not run two versions on the same data at the same time.

If startup fails, check the full extraction and the `phototrichogram.log` file
in the data folder. If no log was created, retain the operating system error
message and the release number. Report these details with the operating system
and microscope model; do not publish patient information in issue attachments.

Before using measurements, calibrate your own microscope using a known scale.
Default calibration values are not a substitute for measuring your setup.
Reference ranges are explanatory aids, not diagnoses. See the
[Russian methodology](METHODOLOGY.md) for limitations and protocols.

## Moving a database and opening pictures

Managed pictures live in `images/` beside the actual database file. Copy the
whole database-plus-images folder, or use the full `.ptg` backup/restore. Moving
only the `.db` file does not move pictures. Newly imported source files and their
annotations are copied into managed storage, independently of the application
folder or working directory. This applies to Windows and Linux.

In **История** (History), select the exact examination row and use **Снимки**
(Images), double-click that row, or press Enter. This also distinguishes multiple
examinations on the same day. Old records that never saved a picture cannot
acquire one automatically; the application must not invent an association.
