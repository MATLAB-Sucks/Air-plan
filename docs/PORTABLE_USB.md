# Portable USB edition (Windows + macOS on one stick)

## What "one app" means here

A single executable cannot run on both Windows and macOS; they use different
binary formats. The portable edition therefore carries **two builds of the
same application** on one stick. Both builds read and write **one shared data
folder**:

```
USB stick (exFAT)/
├── Aircraft Foundry.app                  ← macOS (Intel build; runs on Apple Silicon via Rosetta 2)
├── Start Aircraft Foundry (Windows).bat  ← Windows 10/11 64-bit
├── Mac first-run fix.command             ← removes the download quarantine flag (run once if needed)
├── Windows/AircraftFoundry/…             ← Windows build
├── AircraftFoundryData/                  ← SHARED by both
│   ├── portable.json                     ← marker that switches on portable mode
│   ├── library.sqlite                    ← design library (all saved aircraft)
│   ├── projects/   exports/   settings.ini
└── README-USB.txt
```

When the app starts, it looks for `AircraftFoundryData/portable.json` next to
itself, or up to three folders above. If found, everything goes there:
library, projects, exports and settings (an INI file, not the Windows
registry or macOS preferences). No absolute paths are stored anywhere, so the
stick works whatever drive letter or volume name it gets. Without the marker,
the app uses the normal per-user folder.

## Building the stick

**Option A – GitHub Actions (recommended; no Windows PC needed).**
1. Push the project to a GitHub repository.
2. Open **Actions ▸ Build portable USB edition ▸ Run workflow**.

The workflow:
* builds on `macos-15-intel` and `windows-latest`;
* runs the test suite and the packaged-app self-test on each platform;
* uploads `AircraftFoundry-Portable.zip`.

Unzip it and copy its contents to the root of an exFAT-formatted stick.
`macos-15-intel` is GitHub's last Intel Mac runner, supported until August 2027.

**Option B – build locally.**
1. On the Intel Mac: run `packaging/build_macos_app.sh`.
2. On a Windows PC: run `scripts\setup_windows.ps1`, then `packaging\build_windows.ps1`.
3. Put both outputs on one machine and run:
   ```
   python packaging/assemble_usb.py --windows <dist\AircraftFoundry> --mac "<Aircraft Foundry.app>" --out USB
   ```

## The shared library

The library is a single SQLite file, `library.sqlite`. Its format is the same
on every operating system and processor. It stores only parameters, the
mission, cached metrics and a PNG thumbnail; geometry is regenerated on open.

It is set up for removable media:
* rollback journal, not WAL, with full syncs;
* a connection is opened per operation and closed straight away;
* a write-protected stick opens read-only, so you can still browse and open
  designs;
* a library created by a newer version is never written to by an older one.

Use it from one computer at a time. It is not designed for simultaneous use
over a network share.

## Known platform caveats

* **Format the stick as exFAT.** macOS cannot write to NTFS. FAT32 also works,
  because no single file is larger than 4 GB.
* **macOS Gatekeeper.** The app is unsigned.
  * The first time, right-click it and choose **Open**.
  * If the app was downloaded, macOS may quarantine it and run it from a
    hidden read-only copy ("app translocation"). Aircraft Foundry detects this
    and searches mounted volumes for the data folder.
  * `Mac first-run fix.command` removes the quarantine flag permanently.
  * To distribute to other people, sign and notarise the app (see
    `packaging/build_macos_app.sh`).
* **macOS removable-volume permission.** The first time the app writes to the
  stick, macOS may ask for permission to access files on a removable volume.
  Allow it.
* **Windows SmartScreen** may warn about an unsigned app: choose
  **More info ▸ Run anyway**. Code-signing removes the warning.
* **OpenGL 3.2 is required** for the 3D view. Some remote-desktop and virtual
  machine sessions do not provide it.
* **Size:** about 1.4 GB per build, so about 3 GB in total. USB 3 sticks start
  much faster than USB 2.

## What has been verified

* The pinned dependency set installs from pre-built wheels only, with no
  compiling, on Windows x64, Intel macOS and Apple Silicon macOS (lock files
  in `requirements/`).
* A frozen build, placed in the USB layout and launched from the
  `Windows/AircraftFoundry` position, detects the shared data folder and
  creates the library and settings there. Its self-test passes. This was run
  on Linux, using the same code path.
* The library round-trip, read-only media (tested as an unprivileged user),
  schema protection, translocation fallback and USB assembly are all covered
  by automated tests.

**Not verified here:** the actual Windows and macOS binaries, because they
cannot be cross-built on Linux. The GitHub workflow builds both and runs the
self-test on real Windows and macOS runners. Treat its first run as the real
acceptance test.
