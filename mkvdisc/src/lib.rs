pub mod ffi;

use serde::{Deserialize, Serialize};
use std::collections::HashMap;

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Metadata {
    pub title: String,
    pub language: String,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Title {
    pub uid: String,
    pub name: String,
    pub start_ns: u64,
    pub end_ns: u64,
    pub menu_return: String,
    #[serde(default)]
    pub video_track_uid: Option<u32>,
    #[serde(default)]
    pub video_track_file: Option<String>,
    #[serde(default)]
    pub audio_track_uids: Option<Vec<u32>>,
    #[serde(default)]
    pub subtitle_track_uids: Option<Vec<u32>>,
    #[serde(default)]
    pub default_audio_track_uid: Option<u32>,
    #[serde(default)]
    pub default_subtitle_track_uid: Option<u32>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Canvas {
    pub width: u32,
    pub height: u32,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct MenuBackground {
    #[serde(rename = "type")]
    pub bg_type: String,
    #[serde(default)]
    pub name: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct ButtonAction {
    #[serde(rename = "type")]
    pub action_type: String,
    #[serde(default)]
    pub title_uid: Option<String>,
    #[serde(default)]
    pub menu_uid: Option<String>,
    #[serde(default)]
    pub set_var_key: Option<String>,
    #[serde(default)]
    pub set_var_value: Option<String>,
    #[serde(default)]
    pub active_condition_key: Option<String>,
    #[serde(default)]
    pub active_condition_value: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct ButtonAssets {
    #[serde(default)]
    pub idle: Option<String>,
    #[serde(default)]
    pub focused: Option<String>,
    #[serde(default)]
    pub active: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone, Default)]
pub struct NavigationTargets {
    #[serde(default)]
    pub up: Option<String>,
    #[serde(default)]
    pub down: Option<String>,
    #[serde(default)]
    pub left: Option<String>,
    #[serde(default)]
    pub right: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Button {
    pub uid: String,
    pub label: String,
    pub rect: (i32, i32, i32, i32),
    #[serde(default)]
    pub action: Option<ButtonAction>,
    #[serde(default)]
    pub assets: ButtonAssets,
    #[serde(default)]
    pub navigation: NavigationTargets,
    #[serde(default)]
    pub vector_focus_shape: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Menu {
    pub uid: String,
    pub name: String,
    #[serde(default)]
    pub buttons: Vec<Button>,
    pub canvas: Canvas,
    pub background: MenuBackground,
    #[serde(default)]
    pub vector_ui_layer: Option<String>,
}

#[derive(Debug, Serialize, Deserialize, Clone)]
pub struct Manifest {
    pub format: String,
    pub version: String,
    pub disc_uid: String,
    pub source_mode: String,
    pub metadata: Metadata,
    pub first_play: String,
    #[serde(default)]
    pub titles: Vec<Title>,
    #[serde(default)]
    pub menus: Vec<Menu>,
    #[serde(default)]
    pub initial_state_variables: HashMap<String, String>,
    #[serde(default)]
    pub default_audio_uid: Option<u32>,
    #[serde(default)]
    pub default_subtitle_uid: Option<u32>,
}

impl Manifest {
    pub fn from_json_str(raw: &str) -> Result<Self, String> {
        let manifest: Manifest =
            serde_json::from_str(raw).map_err(|e| format!("JSON Parse Error: {}", e))?;
        manifest.validate()?;
        Ok(manifest)
    }

    pub fn validate(&self) -> Result<(), String> {
        if self.format != "MKV-DISC" {
            return Err(format!("Invalid format '{}'. Expected 'MKV-DISC'.", self.format));
        }
        if self.version != "0.1" && self.version != "0.2" {
            return Err(format!("Unsupported manifest version '{}'.", self.version));
        }
        if self.source_mode != "embedded" && self.source_mode != "directory" {
            return Err(format!("Invalid source_mode '{}'.", self.source_mode));
        }
        if self.menus.len() > 256 {
            return Err("Security limit exceeded: maximum 256 menus allowed.".to_string());
        }
        for menu in &self.menus {
            if menu.buttons.len() > 256 {
                return Err(format!(
                    "Security limit exceeded in menu '{}': maximum 256 buttons allowed.",
                    menu.uid
                ));
            }
        }
        if !self.first_play.is_empty()
            && !self.menus.iter().any(|m| m.uid == self.first_play)
            && !self.titles.iter().any(|t| t.uid == self.first_play)
        {
            return Err(format!(
                "first_play target '{}' does not match any menu or title UID.",
                self.first_play
            ));
        }
        Ok(())
    }
}
