# Contributing

Thanks for helping improve EidoDisc. Please open an issue before a large
change so the project can agree on the manifest/runtime behavior first.

## Scope and compatibility

- The supported authoring format is MKV-DISC manifest v0.2.
- Keep the Rust validator, Python model/exporter, and mpv runtime behavior
  aligned when changing manifest fields.
- Directory and embedded Matroska exports are both supported and should remain
  covered by tests.
- All desktop authoring UI must remain native PySide6/Qt Widgets. Do not add
  browser-based UI dependencies.
- Authors provide their own transparent image assets for button states.
- The mpv plugin is not a VLC plugin. Do not describe VLC integration as
  available until it has been implemented and tested.
- The separate `mkvdisc-studio` project is intentionally not part of this
  repository.

## Development setup

Install Rust stable, Python 3.11+, MKVToolNix, and mpv. On Windows, build the
validator and run the test suites from the repository root:

```powershell
cargo build --release -p mkvdisc
cargo test --release -p mkvdisc
cargo test --release -p mkvdisc-mpv
python -m pip install -r mkvdisc-authoring/requirements.txt
$env:QT_QPA_PLATFORM = "offscreen"
Push-Location mkvdisc-authoring
python -m unittest discover -s tests -p test_authoring.py
Pop-Location
```

When running the authoring tests directly, the Rust validator is loaded from
`target/release/mkvdisc.dll`. Set `MKVDISC_DLL` if it is in another location.

## Pull requests

- Explain the behavior change and how you tested it.
- Include or update focused tests for manifest, authoring, export, or playback
  behavior.
- Do not commit generated `target/` files, virtual environments, personal
  media, exported discs, or locally installed tool binaries.
- Keep changes focused; include documentation updates for user-visible
  behavior or manifest changes.
- Preserve existing copyright/license notices and use assets you have rights
  to distribute.

## Reporting issues

Include operating system, Rust/Python/mpv versions, exact reproduction steps,
the relevant manifest excerpt with private paths removed, and build/runtime
logs. Do not attach copyrighted video or image assets unless you have
permission to share them.
