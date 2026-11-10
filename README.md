# EidoDisc (MKV-DISC)

EidoDisc is an open-source prototype for authoring and playing interactive
Matroska disc-style projects. A project manifest describes menu pages, buttons,
title playback targets, directional navigation, and reactive state variables.
Authors supply their own visual assets; the project does not impose a template
or draw button art for them.

The current implementation includes:

- A Rust manifest model and validator for MKV-DISC v0.1/v0.2.
- A native Windows mpv C-plugin that reads the manifest and assets from an
  embedded Matroska file or directory package.
- A native PySide6 authoring application for manifest v0.2, including both
  directory and embedded Matroska export.

VLC support is **not implemented yet**. The mpv plugin uses mpv's C-plugin API
and cannot be loaded by VLC; a VLC integration would be a separate project
component.

> **Prototype notice:** This is community-contributed software under active
> development. Back up project files and test exports before relying on them.

## Get started

- [Authoring guide](docs/authoring-guide.md): create menus, buttons, routing,
  stateful selections, and exports.
- [Manifest v0.2 reference](docs/manifest-v0.2.md): JSON fields and examples.
- [Contributing and tests](CONTRIBUTING.md): development setup and checks.
- [PySide6 authoring app](mkvdisc-authoring/README.md): app-specific setup.

## Requirements

- Windows 10/11 x64 for the currently supported authoring and mpv-plugin
  workflow.
- Rust stable and Cargo.
- Python 3.11 or newer for the authoring app.
- MKVToolNix (`mkvmerge.exe`, `mkvextract.exe`, and `mkvpropedit.exe`) for
  authoring exports and embedded playback.
- mpv for playback. The plugin DLL must be built for an mpv-compatible Windows
  x64 C-plugin host.

The required mpv API headers are in `mpv-sdk/include`; local mpv runtime DLLs
are not part of this repository.

## Build and test

From the repository root in PowerShell:

```powershell
cargo build --release -p mkvdisc
cargo test --release -p mkvdisc
cargo test --release -p mkvdisc-mpv
cargo build --release -p mkvdisc-mpv
```

For authoring tests, first build the validator DLL, install Python dependencies,
and then run:

```powershell
python -m pip install -r mkvdisc-authoring/requirements.txt
$env:QT_QPA_PLATFORM = "offscreen"
Push-Location mkvdisc-authoring
python -m unittest discover -s tests -p test_authoring.py
Pop-Location
```

Run the authoring app with:

```powershell
python mkvdisc-authoring/main.py
```

## Play an export in mpv

Build the plugin and load it with the export:

```powershell
cargo build --release -p mkvdisc-mpv
mpv --script="target\release\mkvdisc_mpv.dll" "path\to\disc.mkv"
```

An mpv C plugin is not an ordinary media codec plugin: mpv loads it through
its script-loading option. To avoid supplying the option for every launch,
users can configure mpv to load the DLL by default. See the mpv section of the
[authoring guide](docs/authoring-guide.md).

## License

Unless stated otherwise, EidoDisc source code is offered under
[GNU GPL-3.0-or-later](LICENSE).
