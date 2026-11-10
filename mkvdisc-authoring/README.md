# MKV-DISC Authoring

Native PySide6 desktop authoring UI for MKV-DISC manifest version 0.2.

For a step-by-step project walkthrough, see the
[authoring guide](../docs/authoring-guide.md) and
[manifest v0.2 reference](../docs/manifest-v0.2.md).

## Run on Windows

From this directory, install the declared dependency and launch the app:

```powershell
python -m pip install -r requirements.txt
python main.py
```

The app loads the Rust validator from `..\target\release\mkvdisc.dll`. Build it
from the workspace root with `cargo build --release -p mkvdisc`, or set
`MKVDISC_DLL` to an alternate DLL path.

MKVToolNix `mkvmerge.exe` is required for builds. The app searches `PATH`, the
standard MKVToolNix install locations, and the workspace root.

## Authoring and builds

Import one or more MKV title files, edit their trim bounds and menu return UID
in the Titles tab, and add image/video assets for menu backgrounds and button
states. Imported files are identified with `mkvmerge` so video track IDs can be
used by embedded-mode titles. Legacy projects with 64-bit Matroska track UIDs
are migrated to the corresponding `mkvmerge` track IDs from their source files
when opened, saved, or exported. Choose the output format in the Export panel:

- **Directory package:** Copies the manifest, title MKVs, and referenced assets
  into the selected folder and writes `source_mode: "directory"`.
- **Embedded Matroska:** Multiplexes the distinct title sources and attached
  assets into an `.mkv`, maps each title to its video track in the combined
  container, then attaches the validated manifest and writes
  `source_mode: "embedded"`.

The title trim end defaults to one second on import; edit it to the desired
nanosecond bound before validating or exporting. Menu image backgrounds and
looping video backgrounds are selectable with the Menu Background button or
asset-bin drag-and-drop. Video backgrounds loop silently in the canvas preview
and are bundled with the project output.

The build is run asynchronously with `QProcess`; stdout and stderr are streamed
to the Compiler & Validation Log, and the build button stays disabled until
the process completes. Directory copies also run in a background thread. The
Rust `mkvdisc_validate_manifest` API is used for preflight; its returned error
text is logged and its result is freed in a `finally` block.

## mpv playback

Build the native mpv plugin from the workspace root and load it with the
exported Matroska:

```powershell
cargo build --release -p mkvdisc-mpv
mpv --script="target\release\mkvdisc_mpv.dll" "path\to\disc.mkv"
```

The plugin reads the embedded manifest and attachments, then starts the
`first_play` menu. Its C-plugin entry point must remain active until mpv shuts
down so it can retain the mpv handle and process playback events. In menu mode,
left-click a button's visible bounds to focus and activate it; arrow keys and
Enter remain available for keyboard or remote navigation. Press **Ctrl+m**
during title playback to return to that title's configured menu-return target
(or the first-play menu if the target is unavailable). mpv's native right-click
Disc Menu item is reserved for DVD/Blu-ray navigation and cannot be extended
by this plugin.

## Canvas preview

Buttons use user-provided image assets only. Idle and focused are alternative
base images. When a button's active condition matches the manifest's current
state variables, its active image is composited above the base image. The
Preview selector can force Idle, Focused, or Active display for authoring.
Double-click empty canvas space to add a button; drag to reposition and drag
the eight selection handles to resize. Grid selection controls snapping. Drag
assets from the bin onto a button to assign its idle art, or onto empty canvas
space to set the menu background. Clicking a set-variable button in the canvas
preview updates the state table and refreshes active overlays.
