# Authoring an MKV-DISC v0.2 project

This guide walks through creating a menu and wiring its buttons using the
PySide6 authoring app. Menus are authored on a 1920 x 1080 canvas. EidoDisc
does not generate button artwork: import your own image assets for each button
state.

## Basic concepts

- **Menu**: one screen/page in the interactive disc, such as Main Menu,
  Audio Setup, or Extras. Menus have their own backgrounds and buttons.
- **Button**: a selectable rectangle on a menu. It has a user-visible label,
  a unique UID for links, artwork, and an action.
- **Title**: a playable video entry. A title points to a source MKV, a video
  track within that file, a time range, and the menu to return to afterward.
- **Track**: one stream inside a media file. A Matroska file may contain
  several video, audio, or subtitle tracks. A track ID is not a menu UID or
  a state-variable value.
- **State variable**: a named, temporary key/value in the menu runtime. It
  remembers a choice such as `audio_lang = en` so a button can show its
  Active artwork. Variable names and values are chosen by the author; names
  such as `audio_lang` are examples, not built-in keywords.
- **Navigation**: the directional links between buttons, used by arrow keys
  or a remote control. Navigation changes focus; the button action runs only
  when the focused button is selected.

The terms `en`, `fr`, `off`, `on`, `audio_lang`, and `subtitle_lang` have no
special meaning to EidoDisc. They are ordinary strings. You could instead use
keys such as `chosen_audio` and values such as `english`; the important part
is to use the exact same spelling in the initial value, button action, and
active condition.

## 1. Start a project and add menus

1. Launch the authoring app. Use **New Project** for a blank project or
   **Open Project…** to continue a saved JSON manifest.
2. Set the project title and language in the top metadata toolbar.
3. Use **Add Menu** to create additional menu pages. The **Disc** hierarchy
   tab lists menus and their buttons.
4. Choose the menu that should open first from the **First play** dropdown.
   The dropdown stores the target menu UID in the manifest.
5. Select a menu and use **Menu Background…** to choose its background. Image
   backgrounds are attached as assets in embedded exports. Video backgrounds
   are supported and loop during playback.

## 2. Import title videos

1. Use **Import Media…** and select the MKV title sources.
2. Open the **Titles** tab in the bottom dock. Each title has a UID, display
   name, source file, video track ID, start/end positions in nanoseconds,
   and a **Menu return** target.
3. Set the desired **Start (ns)** position. One second is `1000000000`
   nanoseconds. The **End (ns)** field is saved in the manifest, but the
   current mpv runtime does not yet stop playback at that end position; title
   playback continues to the end of the source file.
4. Choose a valid menu UID for each title's menu-return destination.

The **Video track ID** identifies the video stream to use for this title; it
is not the Matroska track UID and not the title UID. The app probes imported
files and normally fills in the first video track ID. If the file contains
multiple video tracks, verify or change the ID to the intended video stream.
The source MKV must remain available when exporting.

For embedded output, the exporter multiplexes the title source MKVs and maps
each title's source video track to the correct track ID in the combined file.
For directory output, it copies the title files beside the manifest and the
runtime opens the selected title file.

### Audio and subtitle tracks

Embedded export maps each title source's audio and subtitle tracks to their
track IDs in the combined MKV. When a title starts, the mpv plugin selects
that source's default audio track (or its first audio track if no default is
marked) and its default subtitle track, if one is marked. Directory playback
continues to use the normal track selection of the individual title file.

The plugin cannot remove other titles' streams from mpv's native track list:
mpv's track list is player-owned and not filterable by a C plugin. The native
track menu may therefore still show tracks from other titles, even though the
plugin selects the active title's default streams. Per-title audio/subtitle
switching from MKV-DISC menu buttons is not implemented yet.

Do not assume that video track ID 1 should use audio track ID 1, or that video
track ID 0 should use audio track ID 0. IDs are local to each source file and
track type. Embedded export performs the source-to-output mapping; authors
should set the intended default audio/subtitle flags on each source file in
MKVToolNix. The resulting track IDs are recorded in
`audio_track_uids` / `subtitle_track_uids`, with default selections in
`default_audio_track_uid` / `default_subtitle_track_uid`.

A state value such as `audio_lang = en` only remembers a menu choice and can
change button artwork; it does not switch the actual audio stream. A
language-selection menu is therefore visual/state-only until player actions
for switching among a title's mapped alternatives are added.

### Finding track IDs in an MKV

When a source contains multiple video tracks, use the **Track ID** shown by
MKVToolNix for that source file. Do not use the long Matroska track UID, track
number from another file, or an audio/subtitle ID in the title's **Video track
ID** cell.

1. Open **MKVToolNix GUI** and add the title source MKV to the Multiplexer tab.
2. In the track list, check the **ID**, **Type**, **Language**, **Name**, and
   **Default track** columns. Find the video row you intend to play and note
   its ID.
3. In the authoring app's **Titles** tab, put that ID in **Video track ID** for
   the title. IDs are local to each source file; ID `0` in one file does not
   imply the same content as ID `0` in another.
4. For embedded output, the exporter maps this source ID to the corresponding
   track in the combined MKV. Do not manually replace the source ID with an
   ID copied from an older export.

Language and track name are descriptive Matroska metadata. For example, an
audio track may have language `eng` and name `Commentary`, but those labels do
not define the menu key/value `audio_lang = en`. Track metadata and menu state
are separate concepts.

## 3. Add a button and its artwork

1. Select the destination menu in the hierarchy.
2. Double-click an empty area of the composition canvas to add a button.
   Select the button to edit it in the **Properties** inspector.
3. Set a stable, unique **Button UID** and a readable **Display label**.
   UIDs are used by navigation links and must not be confused with labels.
4. Set X, Y, Width, and Height in the inspector, or drag the button on the
   canvas. Use the resize handles to change its bounds. The optional grid
   controls snapping.
5. Assign image files in the inspector or import assets using
   **Import Assets…**:
   - **Idle** is the normal base image.
   - **Focused** replaces Idle while the button has focus.
   - **Active** is an independent transparent overlay shown when the button's
     active condition matches the current state variables.

For example, a selected language option can show `idle + active` while focus
is elsewhere, and `focused + active` when the author returns focus to it.
Use transparent PNGs when the overlay needs to preserve the art beneath it.
JPEG is supported for opaque artwork.

## 4. Set the button action

Choose one action in the inspector:

- **Play Title**: select an imported title. Selecting the button starts that
  title at its configured trim start; playback end returns to its configured
  menu.
- **Jump Menu**: select another menu. This creates a link to a submenu, such
  as Languages, Chapters, or Extras.
- **Set State Variable**: enter a variable key and value. For example, set
  `audio_lang` to `en` when the user chooses English audio.

### How state variables work

Think of a state variable as a small note the menu keeps while the disc is
playing:

```text
key          value
audio_lang   en
```

Here, `audio_lang` is the note's name and `en` is the remembered value. The
author creates both. The key is not detected from the media file; `en` is not
automatically looked up as an audio track; and changing the note does not
change the video or audio stream.

The **State** tab on the left edits the project's initial key/value table.
Add a row using the blank last row. These values are copied into the runtime
when the disc opens. They are not written back to the manifest during
playback, and changing a button updates the runtime value for the current
session.

### Example: an English/Français choice screen

This example builds the menu behavior and selected-state visuals. It does
**not** switch the title's actual audio stream; audio switching is not
implemented yet.

1. In the **State** tab, add `audio_lang` as the key and `off` as its initial
   value. The value `off` means that neither language choice has been made.
2. Add a menu named **Audio Setup**. Add a **Jump Menu** button to the main
   menu that opens it.
3. Add an **English** button to Audio Setup. Give it a unique UID such as
   `audio-english`, choose **Set State Variable**, and enter:
   - Set variable key: `audio_lang`
   - Set variable value: `en`
   - Active condition key: `audio_lang`
   - Active condition value: `en`
4. Assign the English button its own Idle and Focused images, then an Active
   transparent overlay (for example, a check mark or selected border).
5. Add a **Français** button in the same way, but use `fr` for its set value
   and active-condition value. The shared key stays `audio_lang`.
6. Add a **Back** button with **Jump Menu** to the main menu.
7. Save, export, and play. Select English: the runtime value becomes
   `audio_lang = en`, and its Active overlay appears. Move focus to Français:
   English stays active while Français shows its Focused base art. Select
   Français: the value becomes `fr`, so the English overlay turns off and the
   Français overlay turns on.

This illustrates three separate concepts: **Focus** follows the current
navigation position, **State** remembers a choice, and **Active artwork**
indicates that the remembered value matches the button's condition.

### Other useful state examples

State keys and values are free-form strings. For example:

| Purpose | Key | Example values |
|---|---|---|
| Remember a language choice | `audio_lang` | `off`, `en`, `fr` |
| Remember a subtitle-menu choice | `subtitle_lang` | `off`, `en`, `fr` |
| Remember a presentation choice | `edition` | `theatrical`, `extended` |
| Remember a menu preference | `screen_mode` | `widescreen`, `standard` |

These are only remembered menu values. In the current runtime they do not
select audio, subtitle, edition, or display tracks. An edition choice can
launch a different title only when its buttons use **Play Title** actions
that target distinct title entries.

### Make the selected choice visible

For every option button, use the same key and value in its **Set State
Variable** action and its **Active condition**. For example:

1. Add the variable and initial value in the left **State** tab, such as
   `audio_lang = off`.
2. Configure the button action to set `audio_lang` to `en`.
3. Set **Active condition key** to `audio_lang` and **Active condition value**
   to `en`.
4. Add a separate button for other values (for example `fr`) with a matching
   active condition.

The comparison is exact, including spelling and capitalization. If an
initial-state row has `audio_lang = en` but a button checks for `English`, its
Active overlay will not appear.

## 5. Design directional navigation

Every button can define **Navigate up**, **Navigate down**, **Navigate left**,
and **Navigate right** links. Each dropdown targets another button in the
project; the menu runtime uses arrow keys to move focus. The inspector may
show buttons from the whole project, but navigation should target buttons on
the current menu.

Recommended workflow:

1. Lay out the buttons visually before wiring the links.
2. For every button, assign each direction that should move to a neighboring
   choice. Leave directions with no intended destination as **(none)**.
3. Link back in the opposite direction. For a horizontal row, if button A's
   right target is B, make B's left target A. For a vertical list, pair down
   and up similarly.
4. Check edge buttons, submenu-entry buttons, and return buttons with the
   keyboard. Do not rely only on mouse clicks: a TV remote generally uses
   directional input and Select/Enter.
5. Give every submenu an explicit way back to its parent, usually a button
   with **Jump Menu** to the parent menu.

Navigation targets use UIDs, not labels. If you rename a UID, revisit every
button that links to it.

## 6. Preview and save

- Use the **Preview** selector to inspect Live, Idle, Focused, and Active
  visual states.
- Click set-variable buttons in the canvas preview to update the authoring
  state and inspect active overlays.
- Use **Save Project** to save the editable manifest. Keep the referenced
  title files and image/video assets available; the saved project records
  their paths.

## 7. Validate and export

Open the **Export** tab and choose:

- **Directory package**: writes `mkvdisc.json`, title videos, and referenced
  assets to a folder. This is easiest to inspect and share as a folder.
- **Embedded Matroska (.mkv)**: combines the title sources into one MKV,
  maps each title's video, audio, and subtitle tracks to the output, and
  attaches the manifest and image assets.

Choose a destination and click **Validate & Export**. The Rust validator
preflights the v0.2 manifest. Build output is streamed into **Compiler &
Validation Log**, and the Export panel shows progress during validation,
multiplexing or file copying, and finalization. Wait for the success message
before opening the export.
Never overwrite an input title source with the output file.

## 8. Play with the current mpv plugin

Build the plugin from the repository root and pass it to mpv:

```powershell
$repo = (git rev-parse --show-toplevel).Trim()
cargo build --release -p mkvdisc-mpv
$mpv = (Get-Command mpv.exe -ErrorAction SilentlyContinue).Source
if (-not $mpv) {
    $mpv = Read-Host "Enter the path to mpv.exe (or its containing folder)"
}
if (Test-Path -LiteralPath $mpv -PathType Container) {
    $mpv = Join-Path $mpv "mpv.exe"
}
if (-not (Test-Path -LiteralPath $mpv -PathType Leaf)) {
    throw "mpv.exe not found at '$mpv'. Enter the executable path or its folder."
}
$plugin = Join-Path $repo "target\release\mkvdisc_mpv.dll"
& $mpv --script="$plugin" "path\to\disc.mkv"
```

The plugin reads an embedded manifest and its attachments, or the sidecar
manifest and media in a directory package. In a menu, click a button or use
the arrow keys and Enter. Mouse hit testing scales with the player window and
accounts for letterboxing; button artwork and background overlays use the
same video viewport and are repositioned when the player window is resized.
Moving the pointer over a button updates its focused artwork; clicking selects
it.
During title playback, **Ctrl+m** returns to the configured menu-return
target. For embedded titles, the plugin selects their mapped default
audio/subtitle tracks; mpv's native track menu may still list tracks from
other titles. The plugin depends on MKVToolNix's
`mkvmerge.exe` and `mkvextract.exe` being available.

If PowerShell reports that `mpv` is not recognized, mpv is not on `PATH`;
enter the full path to `mpv.exe` or its containing folder when prompted. The
command finds the repository root, so it also works from subfolders. Use only
one `--script` option and replace the example media path with the exported
MKV's actual path.

To load the plugin automatically on each mpv launch, add a script entry in
mpv's `mpv.conf`:

```text
script=FULL\PATH\TO\mkvdisc_mpv.dll
```

Use the absolute path to the built DLL. This is a one-time mpv configuration;
the plugin is still a native mpv-specific plugin and does not add MKV-DISC
support to VLC.

## Troubleshooting

- **No menu appears:** ensure the plugin is built for the mpv process
  architecture, loaded with `--script` or `mpv.conf`, and that the file is a
  valid MKV-DISC export.
- **The wrong title plays:** export again with the current authoring app so
  it can write the embedded output track mapping. Older embedded files may
  contain source-local track IDs and should be rebuilt.
- **Missing artwork or background:** confirm the asset path is valid before
  export and inspect the Compiler & Validation Log.
- **Build fails to start:** install MKVToolNix and make sure its executables
  are discoverable by the app.
