use mkvdisc::Manifest;
use serde_json::{json, Value};

fn valid_v02_manifest() -> Value {
    json!({
        "format": "MKV-DISC",
        "version": "0.2",
        "disc_uid": "urn:uuid:test-v02",
        "source_mode": "embedded",
        "metadata": { "title": "Test Disc", "language": "en" },
        "first_play": "menu-main",
        "initial_state_variables": {
            "audio_lang": "off",
            "subtitle_lang": "off"
        },
        "titles": [{
            "uid": "feature",
            "name": "Feature",
            "video_track_uid": 0,
            "start_ns": 0,
            "end_ns": 5_000_000_000_u64,
            "menu_return": "menu-main"
        }],
        "menus": [{
            "uid": "menu-main",
            "name": "Main Menu",
            "canvas": { "width": 1920, "height": 1080 },
            "background": { "type": "attachment", "name": "main.png" },
            "buttons": [{
                "uid": "button-play",
                "label": "Play",
                "rect": [100, 800, 400, 90],
                "assets": {
                    "idle": "play-idle.png",
                    "focused": "play-focused.png",
                    "active": null
                },
                "navigation": {
                    "up": null,
                    "down": "button-audio",
                    "left": null,
                    "right": null
                },
                "action": {
                    "type": "play_title",
                    "title_uid": "feature"
                }
            }, {
                "uid": "button-audio",
                "label": "English Audio",
                "rect": [100, 920, 400, 90],
                "assets": {
                    "idle": "audio-idle.png",
                    "focused": "audio-focused.png",
                    "active": "audio-active.png"
                },
                "navigation": {
                    "up": "button-play",
                    "down": null,
                    "left": null,
                    "right": null
                },
                "action": {
                    "type": "set_variable",
                    "set_var_key": "audio_lang",
                    "set_var_value": "en",
                    "active_condition_key": "audio_lang",
                    "active_condition_value": "en"
                }
            }]
        }]
    })
}

#[test]
fn accepts_v02_manifest_and_reactive_menu_data() {
    let raw = valid_v02_manifest().to_string();
    let manifest = Manifest::from_json_str(&raw).expect("Valid v0.2 manifest should parse.");

    assert_eq!(
        manifest
            .initial_state_variables
            .get("audio_lang")
            .map(String::as_str),
        Some("off")
    );
    let menu = &manifest.menus[0];
    assert_eq!(
        menu.buttons[0].assets.idle.as_deref(),
        Some("play-idle.png")
    );
    assert_eq!(
        menu.buttons[0].navigation.down.as_deref(),
        Some("button-audio")
    );
    let audio_action = menu.buttons[1].action.as_ref().unwrap();
    assert_eq!(audio_action.set_var_key.as_deref(), Some("audio_lang"));
    assert_eq!(audio_action.active_condition_value.as_deref(), Some("en"));
}

#[test]
fn accepts_legacy_v01_manifest() {
    let mut value = valid_v02_manifest();
    value["version"] = json!("0.1");
    value
        .as_object_mut()
        .unwrap()
        .remove("initial_state_variables");
    let manifest =
        Manifest::from_json_str(&value.to_string()).expect("v0.1 manifests remain supported.");

    assert!(manifest.initial_state_variables.is_empty());
}

#[test]
fn rejects_unknown_format_version_and_source_mode() {
    for (field, invalid_value) in [
        ("format", json!("NOT-A-DISC")),
        ("version", json!("0.3")),
        ("source_mode", json!("unknown")),
    ] {
        let mut value = valid_v02_manifest();
        value[field] = invalid_value;
        assert!(
            Manifest::from_json_str(&value.to_string()).is_err(),
            "Expected invalid {field} to be rejected."
        );
    }
}

#[test]
fn rejects_first_play_that_does_not_reference_a_menu_or_title() {
    let mut value = valid_v02_manifest();
    value["first_play"] = json!("missing-menu");
    let error = Manifest::from_json_str(&value.to_string()).unwrap_err();
    assert!(error.contains("first_play target"));
}

#[test]
fn enforces_menu_and_button_count_limits() {
    let mut too_many_menus = valid_v02_manifest();
    let menu = too_many_menus["menus"][0].clone();
    too_many_menus["menus"] = Value::Array(vec![menu; 257]);
    assert!(Manifest::from_json_str(&too_many_menus.to_string())
        .unwrap_err()
        .contains("maximum 256 menus"));

    let mut too_many_buttons = valid_v02_manifest();
    let button = too_many_buttons["menus"][0]["buttons"][0].clone();
    too_many_buttons["menus"][0]["buttons"] = Value::Array(vec![button; 257]);
    assert!(Manifest::from_json_str(&too_many_buttons.to_string())
        .unwrap_err()
        .contains("maximum 256 buttons"));
}

#[test]
fn rejects_malformed_json() {
    assert!(Manifest::from_json_str("{")
        .unwrap_err()
        .contains("JSON Parse Error"));
}
