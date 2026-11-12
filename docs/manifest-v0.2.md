# MKV-DISC manifest v0.2 reference

The manifest is UTF-8 JSON with `"format": "MKV-DISC"` and `"version": "0.2"`.
The authoring app writes and validates this format. UIDs are stable identifiers
used by `first_play`, actions, navigation, and title return links.

## Root fields

| Field | Meaning |
|---|---|
| `disc_uid` | Disc/project identifier, normally a URN UUID. |
| `source_mode` | `"directory"` for a sidecar package or `"embedded"` for one Matroska file. |
| `metadata` | Display title and language. |
| `first_play` | UID of the menu shown at startup. |
| `initial_state_variables` | Initial string-to-string reactive state map. |
| `titles` | Playable video entries, their trim bounds, track IDs, and menu return targets. |
| `menus` | Menu pages containing canvas, background, and buttons. |
| `default_audio_uid`, `default_subtitle_uid` | Optional Matroska track identifiers. |

## What track and state fields do today

The embedded exporter multiplexes the source files' tracks into the output
MKV. The mpv runtime currently maps/selects only each title's video stream
using `video_track_uid`. It does not record or select the corresponding
per-title audio/subtitle streams. Consequently, embedded playback with
multiple title sources may use mpv's normal track-selection behavior instead
of each title's original audio/subtitle.

The manifest has `audio_track_uids`, `subtitle_track_uids`,
`default_audio_uid`, and `default_subtitle_uid` fields, but the current
authoring UI/runtime does not implement per-title audio/subtitle mapping or
language switching. Authors do not need to fill these fields in.

Track IDs are local to their source file and track type. A video ID of `1`
does not imply that audio ID `1` belongs to it; IDs cannot be paired by
matching their numbers. Correct embedded support requires explicit source to
output mappings for each title and player-side selection of those mapped
tracks.

`initial_state_variables` and a button's `set_var_key` / `set_var_value` are
menu logic, not Matroska track references. For example,
`"audio_lang": "en"` means only that the menu remembers the string value `en`
for a variable named `audio_lang`. It does not inspect the media, select an
English audio track, or turn subtitles on or off. The names and values are
chosen by the author and compared as exact strings for active artwork.

## Title

```json
{
  "uid": "feature",
  "name": "Feature film",
  "video_track_uid": 0,
  "video_track_file": "feature.mkv",
  "start_ns": 0,
  "end_ns": 5400000000000,
  "menu_return": "menu-main"
}
```

`video_track_uid` is the `mkvmerge` video track ID. `video_track_file` points
to the source file in a project and directory export. An embedded export
rewrites the manifest with the corresponding output-container track ID and
omits the workstation source path. Bounds are nanoseconds. `menu_return` is a
menu UID.

The video track ID is local to the source MKV before export. Use MKVToolNix
GUI's track list to distinguish a video track from audio and subtitle tracks.
Do not substitute the Matroska track UID, which is a separate, usually much
larger number.

The runtime seeks to `start_ns` when title playback begins. Although
`end_ns` is stored in the manifest, the current mpv plugin does not yet stop
at that bound; playback continues until the source file ends.

## Menu and button

```json
{
  "uid": "menu-main",
  "name": "Main Menu",
  "canvas": { "width": 1920, "height": 1080 },
  "background": { "type": "attachment", "name": "main.png" },
  "buttons": [
    {
      "uid": "button-play",
      "label": "Play",
      "rect": [120, 820, 400, 90],
      "assets": {
        "idle": "play-idle.png",
        "focused": "play-focused.png",
        "active": null
      },
      "navigation": {
        "up": null,
        "down": "button-extras",
        "left": null,
        "right": "button-extras"
      },
      "action": {
        "type": "play_title",
        "title_uid": "feature"
      }
    }
  ]
}
```

`rect` is `[x, y, width, height]` on the canvas. Button assets are optional
transparent PNG/JPG layers. Idle and Focused are alternative base states;
Active is drawn over the selected base state when the action's active
condition matches current state.

## Actions

`action.type` is one of:

- `play_title` with a `title_uid`.
- `jump_menu` with a `menu_uid`.
- `set_variable` with `set_var_key` and `set_var_value`.

Any action can also specify `active_condition_key` and
`active_condition_value`. The active image appears when the named state
variable equals that value. Example:

```json
{
  "initial_state_variables": { "audio_lang": "off" },
  "action": {
    "type": "set_variable",
    "set_var_key": "audio_lang",
    "set_var_value": "en",
    "active_condition_key": "audio_lang",
    "active_condition_value": "en"
  }
}
```

## Navigation

Each direction in `navigation` is either null or the UID of another button.
The authoring inspector lists buttons from the project, but the current mpv
runtime follows directional navigation only to a button in the current menu.
Use same-menu targets. For predictable remote control, explicitly set
reciprocal links where appropriate: if A points right to B, set B's left
target to A.

## v0.2 complete small example

```json
{
  "format": "MKV-DISC",
  "version": "0.2",
  "disc_uid": "urn:uuid:replace-with-a-uuid",
  "source_mode": "directory",
  "metadata": { "title": "Sample Disc", "language": "en" },
  "first_play": "menu-main",
  "initial_state_variables": { "audio_lang": "off" },
  "titles": [
    {
      "uid": "feature",
      "name": "Feature",
      "video_track_uid": 0,
      "video_track_file": "feature.mkv",
      "start_ns": 0,
      "end_ns": 5400000000000,
      "menu_return": "menu-main"
    }
  ],
  "menus": [
    {
      "uid": "menu-main",
      "name": "Main Menu",
      "canvas": { "width": 1920, "height": 1080 },
      "background": { "type": "attachment", "name": "main.png" },
      "buttons": [
        {
          "uid": "button-play",
          "label": "Play",
          "rect": [120, 820, 400, 90],
          "assets": { "idle": "play-idle.png", "focused": "play-focus.png", "active": null },
          "navigation": { "up": null, "down": null, "left": null, "right": null },
          "action": { "type": "play_title", "title_uid": "feature" }
        }
      ]
    }
  ],
  "default_audio_uid": null,
  "default_subtitle_uid": null
}
```

Use the Rust validator or the authoring app to validate manifests rather than
assuming that syntactically valid JSON is semantically correct.
