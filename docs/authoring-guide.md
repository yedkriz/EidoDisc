# Authoring an MKV-DISC v0.2 project

This guide walks through creating a menu and wiring its buttons using the
PySide6 authoring app. Menus are authored on a 1920 x 1080 canvas. EidoDisc
does not generate button artwork: import your own image assets for each button
state.

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
   name, source file, video track ID, start/end trim bounds in nanoseconds,
   and a **Menu return** target.
3. Change the default one-second end bound to the desired end time. The end
   bound is in nanoseconds: one second is `1000000000`.
4. Choose a valid menu UID for each title's menu-return destination.

For embedded output, leave each title's source file in the project: the
exporter needs it to multiplex the picture and map its source track into the
combined MKV. Track IDs are identified from source files and remapped for the
final output. Directory output copies the title files beside the manifest.

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

To keep a selected option visibly selected:

1. Add the variable and initial value in the left **State** tab, such as
   `audio_lang = off`.
2. Configure the button action to set `audio_lang` to `en`.
3. Set **Active condition key** to `audio_lang` and **Active condition value**
   to `en`.
4. Add a separate button for other values (for example `fr`) with a matching
   active condition.

State is currently represented as string key/value pairs. A set-variable
button changes the runtime state; it does not by itself switch the encoded
audio stream. Actual audio/subtitle track switching is a separate playback
feature.

## 5. Design directional navigation

Every button can define **Navigate up**, **Navigate down**, **Navigate left**,
and **Navigate right** links. Each dropdown targets another button in the
project; the menu runtime uses arrow keys to move focus.

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
  maps each title to the correct output video track, and attaches the
  manifest and image assets.

Choose a destination and click **Validate & Export**. The Rust validator
preflights the v0.2 manifest. Build output is streamed into **Compiler &
Validation Log**. Wait for the success message before opening the export.
Never overwrite an input title source with the output file.

## 8. Play with the current mpv plugin

Build the plugin from the repository root and pass it to mpv:

```powershell
cargo build --release -p mkvdisc-mpv
mpv --script="target\release\mkvdisc_mpv.dll" "path\to\disc.mkv"
```

The plugin reads an embedded manifest and its attachments, or the sidecar
manifest and media in a directory package. In a menu, click a button or use
the arrow keys and Enter. During title playback, **Ctrl+m** returns to the
configured menu-return target. The plugin depends on MKVToolNix's
`mkvmerge.exe` and `mkvextract.exe` being available.

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
