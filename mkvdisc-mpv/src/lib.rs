#![allow(non_upper_case_globals)]
#![allow(non_camel_case_types)]
#![allow(non_snake_case)]
#![allow(dead_code)]

use std::collections::HashMap;
use std::ffi::{CStr, CString};
use std::fs;
use std::os::raw::{c_char, c_int, c_void};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::ptr::null_mut;

include!(concat!(env!("OUT_DIR"), "/mpv_bindings.rs"));

use mkvdisc::Manifest;

struct PlayerState {
    manifest: Manifest,
    assets_dir: PathBuf,
    cache_dir: PathBuf,
    disc_path: PathBuf,
    active_menu_uid: String,
    focused_button_uid: String,
    state_vars: HashMap<String, String>,
    active_overlay_ids: Vec<u32>,
    in_menu_mode: bool,
    menu_rendered_once: bool,
    menu_background_video_loaded: bool,
    active_background_video: Option<PathBuf>,
    pending_menu_video: bool,
    pending_disc_restore: bool,
    pending_title_uid: Option<String>,
    active_title_uid: Option<String>,
}

static mut PLAYER_STATE: *mut PlayerState = null_mut();

type MpvWaitEvent = unsafe extern "C" fn(*mut mpv_handle, f64) -> *mut mpv_event;
type MpvCommand = unsafe extern "C" fn(*mut mpv_handle, *mut *const c_char) -> c_int;
type MpvSetPropertyString =
    unsafe extern "C" fn(*mut mpv_handle, *const c_char, *const c_char) -> c_int;
type MpvGetProperty =
    unsafe extern "C" fn(*mut mpv_handle, *const c_char, mpv_format, *mut c_void) -> c_int;
type MpvFree = unsafe extern "C" fn(*mut c_void);
type MpvFreeNodeContents = unsafe extern "C" fn(*mut mpv_node);

#[repr(C)]
struct MousePosNode {
    value: MousePosNodeValue,
    format: mpv_format,
}

#[repr(C)]
union MousePosNodeValue {
    string: *mut c_char,
    flag: c_int,
    int64: i64,
    double_: f64,
    list: *mut MousePosNodeList,
    byte_array: *mut c_void,
}

#[repr(C)]
struct MousePosNodeList {
    count: c_int,
    values: *mut MousePosNode,
    keys: *mut *mut c_char,
}

#[repr(C)]
struct MpvNodeListView {
    count: c_int,
    values: *mut mpv_node,
    keys: *mut *mut c_char,
}

#[derive(Debug, PartialEq)]
struct MpvVideoTrack {
    id: i64,
    source_id: i64,
}

#[derive(Clone, Copy)]
struct MpvApi {
    wait_event: MpvWaitEvent,
    command: MpvCommand,
    set_property_string: MpvSetPropertyString,
    get_property: MpvGetProperty,
    free: MpvFree,
    free_node_contents: MpvFreeNodeContents,
}

static mut MPV_API: Option<MpvApi> = None;

unsafe fn mpv_api() -> MpvApi {
    MPV_API.expect("mpv host API must be resolved before plugin callbacks run")
}

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn GetModuleHandleA(module_name: *const c_char) -> *mut c_void;
    fn GetProcAddress(module: *mut c_void, proc_name: *const c_char) -> *mut c_void;
}

impl MpvApi {
    #[cfg(windows)]
    unsafe fn resolve() -> Result<Self, &'static str> {
        let module = GetModuleHandleA(null_mut());
        if module.is_null() {
            return Err("GetModuleHandleA(NULL) failed");
        }

        macro_rules! resolve {
            ($symbol:literal, $type:ty) => {{
                let address =
                    GetProcAddress(module, concat!($symbol, "\0").as_ptr() as *const c_char);
                if address.is_null() {
                    return Err($symbol);
                }
                std::mem::transmute::<*mut c_void, $type>(address)
            }};
        }

        Ok(Self {
            wait_event: resolve!("mpv_wait_event", MpvWaitEvent),
            command: resolve!("mpv_command", MpvCommand),
            set_property_string: resolve!("mpv_set_property_string", MpvSetPropertyString),
            get_property: resolve!("mpv_get_property", MpvGetProperty),
            free: resolve!("mpv_free", MpvFree),
            free_node_contents: resolve!("mpv_free_node_contents", MpvFreeNodeContents),
        })
    }

    #[cfg(not(windows))]
    unsafe fn resolve() -> Result<Self, &'static str> {
        Err("the mpv plugin host symbol resolver requires Windows")
    }
}

#[no_mangle]
pub unsafe extern "C" fn mpv_open_cplugin(ctx: *mut mpv_handle) -> c_int {
    if ctx.is_null() {
        return -1;
    }

    let api = match MpvApi::resolve() {
        Ok(api) => api,
        Err(symbol) => {
            eprintln!("[!] Unable to resolve mpv host symbol: {}", symbol);
            return -1;
        }
    };
    MPV_API = Some(api);

    println!("[*] MKV-DISC v0.2 Player Plugin mounted.");

    loop {
        let event = (api.wait_event)(ctx, -1.0);
        if event.is_null() {
            continue;
        }

        let event_id = (*event).event_id;
        if event_id == MPV_EVENT_SHUTDOWN {
            cleanup_state();
            break;
        } else if event_id == MPV_EVENT_FILE_LOADED {
            if !handle_internal_file_loaded(ctx) {
                on_file_loaded(ctx);
            }
        } else if event_id == MPV_EVENT_END_FILE {
            handle_end_file(ctx);
        } else if event_id == MPV_EVENT_VIDEO_RECONFIG {
            if !PLAYER_STATE.is_null() {
                let state = &mut *PLAYER_STATE;
                if state.in_menu_mode && !state.menu_rendered_once {
                    state.menu_rendered_once = true;
                    if !state.menu_background_video_loaded {
                        set_mpv_pause(ctx, true);
                    }
                    render_active_menu(ctx);
                }
            }
        } else if event_id == MPV_EVENT_CLIENT_MESSAGE {
            let msg = *((*event).data as *mut mpv_event_client_message);
            if msg.num_args > 0 {
                let cmd = CStr::from_ptr(*msg.args).to_string_lossy();
                handle_input_command(ctx, &cmd);
            }
        }
    }

    0
}

unsafe fn on_file_loaded(ctx: *mut mpv_handle) {
    if !PLAYER_STATE.is_null() {
        clear_all_overlays(ctx, &mut *PLAYER_STATE);
        cleanup_state();
    }

    let path_str = match get_mpv_string_prop(ctx, "path") {
        Some(p) => p,
        None => {
            eprintln!("[!] Unable to read the current media path from mpv.");
            return;
        }
    };

    let media_path = Path::new(&path_str);
    if !media_path.exists() {
        eprintln!(
            "[!] Current media path does not exist: {}",
            media_path.display()
        );
        return;
    }

    let temp_dir = std::env::temp_dir().join("mkvdisc_runtime_cache");
    if let Err(error) = fs::remove_dir_all(&temp_dir) {
        if error.kind() != std::io::ErrorKind::NotFound {
            eprintln!("[!] Unable to clear runtime cache: {}", error);
            return;
        }
    }
    if let Err(error) = fs::create_dir_all(&temp_dir) {
        eprintln!("[!] Unable to create runtime cache: {}", error);
        return;
    }

    let extracted_manifest = match extract_attachments_from_mkv(media_path, &temp_dir) {
        Ok(path) => path,
        Err(error) => {
            eprintln!("[!] Unable to extract MKV attachments: {}", error);
            None
        }
    };
    let manifest_text = extracted_manifest
        .and_then(|path| match fs::read_to_string(&path) {
            Ok(text) => Some(text),
            Err(error) => {
                eprintln!("[!] Unable to read embedded manifest: {}", error);
                None
            }
        })
        .or_else(|| {
            media_path
                .parent()
                .map(|p| p.join("mkvdisc.json"))
                .filter(|p| p.exists())
                .and_then(|path| match fs::read_to_string(path) {
                    Ok(text) => Some(text),
                    Err(error) => {
                        eprintln!("[!] Unable to read sidecar manifest: {}", error);
                        None
                    }
                })
        });

    let raw_json = match manifest_text {
        Some(j) => j,
        None => {
            println!("[*] Standard MKV detected (no mkvdisc.json found). Normal playback.");
            return;
        }
    };

    let manifest = match Manifest::from_json_str(&raw_json) {
        Ok(m) => m,
        Err(e) => {
            eprintln!("[!] MKV-DISC Manifest Validation Error: {}", e);
            return;
        }
    };

    let assets_base = if manifest.source_mode == "directory" {
        media_path.parent().unwrap_or(Path::new(".")).to_path_buf()
    } else {
        temp_dir.clone()
    };

    let first_menu_uid = if !manifest.first_play.is_empty() {
        manifest.first_play.clone()
    } else if let Some(first_menu) = manifest.menus.first() {
        first_menu.uid.clone()
    } else {
        return;
    };

    let first_btn_uid = manifest
        .menus
        .iter()
        .find(|m| m.uid == first_menu_uid)
        .and_then(|m| m.buttons.first())
        .map(|b| b.uid.clone())
        .unwrap_or_default();

    cleanup_state();
    let state = Box::new(PlayerState {
        state_vars: manifest.initial_state_variables.clone(),
        manifest,
        assets_dir: assets_base,
        cache_dir: temp_dir,
        active_menu_uid: first_menu_uid,
        focused_button_uid: first_btn_uid,
        active_overlay_ids: Vec::new(),
        in_menu_mode: true,
        menu_rendered_once: false,
        disc_path: media_path.to_path_buf(),
        menu_background_video_loaded: false,
        active_background_video: None,
        pending_menu_video: false,
        pending_disc_restore: false,
        pending_title_uid: None,
        active_title_uid: None,
    });
    PLAYER_STATE = Box::into_raw(state);

    define_menu_input(ctx);
    println!("[+] MKV-DISC v0.2 Manifest loaded. Waiting for video output initialization...");
    activate_menu(ctx);
}

fn extract_attachments_from_mkv(
    mkv_path: &Path,
    out_dir: &Path,
) -> Result<Option<PathBuf>, String> {
    let mkvmerge = find_mkvtoolnix_bin("mkvmerge.exe");
    let mkvextract = find_mkvtoolnix_bin("mkvextract.exe");

    let info_output = Command::new(&mkvmerge)
        .arg("-J")
        .arg(mkv_path)
        .output()
        .map_err(|error| format!("failed to run {}: {}", mkvmerge, error))?;
    if !info_output.status.success() {
        return Err(format!(
            "{} failed: {}",
            mkvmerge,
            String::from_utf8_lossy(&info_output.stderr).trim()
        ));
    }
    let parsed: serde_json::Value = serde_json::from_slice(&info_output.stdout)
        .map_err(|error| format!("invalid mkvmerge JSON: {}", error))?;
    let attachments = parsed
        .get("attachments")
        .and_then(|value| value.as_array())
        .ok_or_else(|| "mkvmerge JSON has no attachments array".to_string())?;
    if attachments.is_empty() {
        return Ok(None);
    }

    let mut args = vec![
        mkv_path.to_string_lossy().to_string(),
        "attachments".to_string(),
    ];
    let mut manifest_path = None;

    for att in attachments {
        let id = att
            .get("id")
            .and_then(|value| value.as_u64())
            .ok_or_else(|| "attachment has no numeric id".to_string())?;
        let name = att
            .get("file_name")
            .and_then(|value| value.as_str())
            .ok_or_else(|| format!("attachment {} has no filename", id))?;
        let file_name = Path::new(name)
            .file_name()
            .filter(|file_name| !file_name.is_empty())
            .ok_or_else(|| format!("attachment {} has an invalid filename", id))?;
        let dest = out_dir.join(file_name);
        if file_name
            .to_string_lossy()
            .eq_ignore_ascii_case("mkvdisc.json")
        {
            manifest_path = Some(dest.clone());
        }
        args.push(format!("{}:{}", id, dest.to_string_lossy()));
    }

    let extract_output = Command::new(&mkvextract)
        .args(&args)
        .output()
        .map_err(|error| format!("failed to run {}: {}", mkvextract, error))?;
    if !extract_output.status.success() {
        return Err(format!(
            "{} failed: {}",
            mkvextract,
            String::from_utf8_lossy(&extract_output.stderr).trim()
        ));
    }
    if let Some(path) = &manifest_path {
        if !path.is_file() {
            return Err(format!("{} was not extracted", path.display()));
        }
    }

    Ok(manifest_path)
}

fn find_mkvtoolnix_bin(exe_name: &str) -> String {
    let win_default = Path::new(r"C:\Program Files\MKVToolNix").join(exe_name);
    if win_default.exists() {
        win_default.to_string_lossy().to_string()
    } else {
        exe_name.to_string()
    }
}

unsafe fn handle_internal_file_loaded(ctx: *mut mpv_handle) -> bool {
    if PLAYER_STATE.is_null() {
        return false;
    }
    let state = &mut *PLAYER_STATE;
    if state.pending_menu_video {
        state.pending_menu_video = false;
        state.pending_disc_restore = false;
        state.menu_background_video_loaded = true;
        state.menu_rendered_once = false;
        set_mpv_pause(ctx, false);
        return true;
    }
    if state.pending_disc_restore {
        state.pending_disc_restore = false;
        state.menu_background_video_loaded = false;
        state.active_background_video = None;
        state.in_menu_mode = true;
        state.menu_rendered_once = true;
        set_menu_input_enabled(ctx, true);
        set_mpv_pause(ctx, true);
        render_active_menu(ctx);
        return true;
    }
    if let Some(title_uid) = state.pending_title_uid.take() {
        state.active_title_uid = Some(title_uid.clone());
        state.in_menu_mode = false;
        state.menu_rendered_once = false;
        start_title_playback(ctx, &title_uid);
        return true;
    }
    false
}

unsafe fn start_pending_title(ctx: *mut mpv_handle) {
    if PLAYER_STATE.is_null() {
        return;
    }
    let state = &mut *PLAYER_STATE;
    let title_uid = match state.pending_title_uid.take() {
        Some(uid) => uid,
        None => return,
    };
    state.active_title_uid = Some(title_uid.clone());
    state.in_menu_mode = false;
    state.menu_rendered_once = false;
    start_title_playback(ctx, &title_uid);
}

unsafe fn start_title_playback(ctx: *mut mpv_handle, title_uid: &str) {
    if PLAYER_STATE.is_null() {
        return;
    }
    set_menu_input_enabled(ctx, false);
    let state = &mut *PLAYER_STATE;
    if let Some(title) = state.manifest.titles.iter().find(|t| t.uid == title_uid) {
        if let Some(track_id) = title.video_track_uid {
            set_mpv_video_track(ctx, track_id);
        }
        seek_mpv(ctx, title.start_ns as f64 / 1_000_000_000.0);
    }
    set_mpv_pause(ctx, false);
}

unsafe fn set_mpv_video_track(ctx: *mut mpv_handle, track_id: u32) {
    let api = mpv_api();
    let property = CString::new("track-list").unwrap();
    let mut node: mpv_node = std::mem::zeroed();
    let status = (api.get_property)(
        ctx,
        property.as_ptr(),
        MPV_FORMAT_NODE,
        &mut node as *mut mpv_node as *mut c_void,
    );
    if status < 0 {
        eprintln!(
            "[!] Could not resolve video track {track_id} from mpv's track list: error {status}."
        );
        return;
    }

    let tracks = read_mpv_video_tracks(&node);
    (api.free_node_contents)(&mut node);
    let mpv_track_id = match find_mpv_video_track_id(track_id, &tracks) {
        Some(id) => id.to_string(),
        None => {
            eprintln!(
                "[!] Video track {track_id} was not found uniquely in mpv's track list."
            );
            return;
        }
    };
    let property = CString::new("vid").unwrap();
    let value = CString::new(mpv_track_id).unwrap();
    let status = (api.set_property_string)(ctx, property.as_ptr(), value.as_ptr());
    if status < 0 {
        eprintln!(
            "[!] mpv could not select video track {}: error {}",
            track_id, status
        );
    }
}

unsafe fn read_mpv_video_tracks(node: &mpv_node) -> Vec<MpvVideoTrack> {
    if node.format != MPV_FORMAT_NODE_ARRAY || node.u.list.is_null() {
        return Vec::new();
    }
    let list = &*(node.u.list as *const MpvNodeListView);
    if list.count <= 0 || list.values.is_null() {
        return Vec::new();
    }
    let mut tracks = Vec::new();
    for index in 0..list.count as usize {
        let track_node = list.values.add(index);
        if (*track_node).format != MPV_FORMAT_NODE_MAP || (*track_node).u.list.is_null() {
            continue;
        }
        let track_list = (*track_node).u.list;
        let type_node = mpv_map_value(track_list, b"type");
        if !mpv_node_string_equals(type_node, b"video") {
            continue;
        }
        let id = mpv_node_integer(mpv_map_value(track_list, b"id"));
        let source_id = mpv_node_integer(mpv_map_value(track_list, b"src-id"));
        if let (Some(id), Some(source_id)) = (id, source_id) {
            tracks.push(MpvVideoTrack { id, source_id });
        }
    }
    tracks
}

unsafe fn mpv_map_value(list: *mut mpv_node_list, key: &[u8]) -> Option<*const mpv_node> {
    if list.is_null() {
        return None;
    }
    let list = &*(list as *const MpvNodeListView);
    if list.count <= 0 || list.values.is_null() || list.keys.is_null() {
        return None;
    }
    for index in 0..list.count as usize {
        let key_ptr = *list.keys.add(index);
        if !key_ptr.is_null() && CStr::from_ptr(key_ptr).to_bytes() == key {
            return Some(list.values.add(index));
        }
    }
    None
}

unsafe fn mpv_node_string_equals(node: Option<*const mpv_node>, expected: &[u8]) -> bool {
    let node = match node {
        Some(node) if !node.is_null() && (*node).format == MPV_FORMAT_STRING => node,
        _ => return false,
    };
    let value = (*node).u.string;
    !value.is_null() && CStr::from_ptr(value).to_bytes() == expected
}

unsafe fn mpv_node_integer(node: Option<*const mpv_node>) -> Option<i64> {
    let node = node?;
    if node.is_null() || (*node).format != MPV_FORMAT_INT64 {
        return None;
    }
    Some((*node).u.int64)
}

fn find_mpv_video_track_id(source_track_id: u32, tracks: &[MpvVideoTrack]) -> Option<i64> {
    let source_id = i64::from(source_track_id).checked_add(1)?;
    let mut matching = tracks
        .iter()
        .filter(|track| track.source_id == source_id)
        .map(|track| track.id);
    let id = matching.next()?;
    if matching.next().is_some() {
        return None;
    }
    Some(id)
}

unsafe fn handle_end_file(ctx: *mut mpv_handle) {
    if PLAYER_STATE.is_null() {
        return;
    }
    let state = &mut *PLAYER_STATE;
    if state.pending_menu_video || state.pending_disc_restore || state.pending_title_uid.is_some()
    {
        return;
    }
    let title_uid = match state.active_title_uid.take() {
        Some(uid) => uid,
        None => return,
    };
    state.active_menu_uid = title_menu_return(&state.manifest, &title_uid);
    state.in_menu_mode = true;
    state.menu_rendered_once = false;
    activate_menu(ctx);
}

fn title_menu_return(manifest: &Manifest, title_uid: &str) -> String {
    manifest
        .titles
        .iter()
        .find(|title| title.uid == title_uid)
        .map(|title| title.menu_return.as_str())
        .filter(|menu_uid| manifest.menus.iter().any(|menu| menu.uid == *menu_uid))
        .unwrap_or(&manifest.first_play)
        .to_string()
}

unsafe fn activate_menu(ctx: *mut mpv_handle) {
    if PLAYER_STATE.is_null() {
        return;
    }
    let state = &mut *PLAYER_STATE;
    state.in_menu_mode = true;
    state.menu_rendered_once = false;
    set_menu_input_enabled(ctx, true);
    let menu = match state
        .manifest
        .menus
        .iter()
        .find(|menu| menu.uid == state.active_menu_uid)
        .cloned()
    {
        Some(menu) => menu,
        None => return,
    };

    if menu.background.bg_type == "video" {
        let bg_name = match menu.background.name {
            Some(name) => name,
            None => {
                eprintln!("[!] Menu '{}' has video background type but no filename.", menu.uid);
                set_mpv_pause(ctx, true);
                render_active_menu(ctx);
                return;
            }
        };
        let background_path = state.assets_dir.join(bg_name);
        if !background_path.is_file() {
            eprintln!(
                "[!] Menu background video does not exist: {}",
                background_path.display()
            );
            set_mpv_pause(ctx, true);
            render_active_menu(ctx);
            return;
        }

        if state.menu_background_video_loaded
            && state.active_background_video.as_ref() == Some(&background_path)
        {
            set_mpv_loop(ctx, true);
            set_mpv_pause(ctx, false);
            render_active_menu(ctx);
            state.menu_rendered_once = true;
            return;
        }
        clear_all_overlays(ctx, state);
        state.pending_menu_video = true;
        state.pending_disc_restore = false;
        state.active_background_video = Some(background_path.clone());
        set_mpv_loop(ctx, true);
        if !load_file(ctx, &background_path) {
            state.pending_menu_video = false;
            state.active_background_video = None;
            state.menu_background_video_loaded = false;
            eprintln!(
                "[!] Could not load menu background video: {}",
                background_path.display()
            );
            set_mpv_pause(ctx, true);
            render_active_menu(ctx);
            state.menu_rendered_once = true;
        }
        return;
    }

    if state.menu_background_video_loaded {
        clear_all_overlays(ctx, state);
        state.pending_disc_restore = true;
        state.pending_menu_video = false;
        set_mpv_loop(ctx, false);
        if !load_file(ctx, &state.disc_path) {
            state.pending_disc_restore = false;
            state.menu_background_video_loaded = false;
            state.active_background_video = None;
            eprintln!("[!] Could not restore the disc video after menu background.");
            set_mpv_pause(ctx, true);
            render_active_menu(ctx);
            state.menu_rendered_once = true;
        }
        return;
    }

    set_mpv_loop(ctx, false);
    set_mpv_pause(ctx, true);
    render_active_menu(ctx);
    state.menu_rendered_once = true;
}

unsafe fn load_file(ctx: *mut mpv_handle, path: &Path) -> bool {
    let command = CString::new("loadfile").unwrap();
    let file = match CString::new(path.to_string_lossy().as_bytes()) {
        Ok(file) => file,
        Err(_) => {
            eprintln!("[!] Cannot load a path containing a NUL byte.");
            return false;
        }
    };
    let replace = CString::new("replace").unwrap();
    let args: [*const c_char; 4] = [
        command.as_ptr(),
        file.as_ptr(),
        replace.as_ptr(),
        null_mut(),
    ];
    let status = (mpv_api().command)(ctx, args.as_ptr() as *mut *const c_char);
    if status < 0 {
        eprintln!(
            "[!] mpv could not load '{}': error {}",
            path.display(),
            status
        );
        return false;
    }
    true
}

unsafe fn set_mpv_loop(ctx: *mut mpv_handle, enabled: bool) {
    let property = CString::new("loop-file").unwrap();
    let value = CString::new(if enabled { "inf" } else { "no" }).unwrap();
    let status =
        (mpv_api().set_property_string)(ctx, property.as_ptr(), value.as_ptr());
    if status < 0 {
        eprintln!("[!] mpv rejected loop-file setting with error {}", status);
    }
}

unsafe fn handle_input_command(ctx: *mut mpv_handle, cmd: &str) {
    if PLAYER_STATE.is_null() {
        return;
    }
    if cmd == "mkvdisc-click" {
        if let Some((x, y)) = get_mouse_position(ctx) {
            let state = &mut *PLAYER_STATE;
            if !state.in_menu_mode {
                return;
            }
            let button_uid = state
                .manifest
                .menus
                .iter()
                .find(|menu| menu.uid == state.active_menu_uid)
                .and_then(|menu| {
                    menu.buttons
                        .iter()
                        .find(|button| point_inside_button(x, y, button.rect))
                })
                .map(|button| button.uid.clone());
            if let Some(button_uid) = button_uid {
                state.focused_button_uid = button_uid;
                render_active_menu(ctx);
                handle_input_command(ctx, "mkvdisc-select");
            }
        }
        return;
    }
    let state = &mut *PLAYER_STATE;

    if cmd == "mkvdisc-menu" {
        if let Some(title_uid) = state.active_title_uid.as_deref() {
            state.active_menu_uid = title_menu_return(&state.manifest, title_uid);
        } else if !state
            .manifest
            .menus
            .iter()
            .any(|menu| menu.uid == state.active_menu_uid)
        {
            state.active_menu_uid = state.manifest.first_play.clone();
        }
        state.in_menu_mode = true;
        state.active_title_uid = None;
        activate_menu(ctx);
        return;
    }

    if !state.in_menu_mode {
        return;
    }

    let menu = match state
        .manifest
        .menus
        .iter()
        .find(|m| m.uid == state.active_menu_uid)
    {
        Some(m) => m,
        None => return,
    };

    let current_idx = menu
        .buttons
        .iter()
        .position(|b| b.uid == state.focused_button_uid)
        .unwrap_or(0);
    let current_btn = match menu.buttons.get(current_idx) {
        Some(b) => b,
        None => return,
    };

    match cmd {
        "mkvdisc-up" | "mkvdisc-down" | "mkvdisc-left" | "mkvdisc-right" => {
            let explicit_target = match cmd {
                "mkvdisc-up" => current_btn.navigation.up.as_ref(),
                "mkvdisc-down" => current_btn.navigation.down.as_ref(),
                "mkvdisc-left" => current_btn.navigation.left.as_ref(),
                "mkvdisc-right" => current_btn.navigation.right.as_ref(),
                _ => None,
            };

            if let Some(target_uid) = explicit_target {
                if menu.buttons.iter().any(|b| &b.uid == target_uid) {
                    state.focused_button_uid = target_uid.clone();
                }
            } else if !menu.buttons.is_empty() {
                let next_idx = if cmd == "mkvdisc-up" || cmd == "mkvdisc-left" {
                    (current_idx + menu.buttons.len() - 1) % menu.buttons.len()
                } else {
                    (current_idx + 1) % menu.buttons.len()
                };
                state.focused_button_uid = menu.buttons[next_idx].uid.clone();
            }
            render_active_menu(ctx);
        }
        "mkvdisc-select" => {
            if let Some(action) = &current_btn.action.clone() {
                if let (Some(k), Some(v)) = (&action.set_var_key, &action.set_var_value) {
                    state.state_vars.insert(k.clone(), v.clone());
                    println!("[*] Reactive State Updated: {} = {}", k, v);
                }

                match action.action_type.as_str() {
                    "jump_menu" => {
                        if let Some(target_menu) = &action.menu_uid {
                            state.active_menu_uid = target_menu.clone();
                            if let Some(m) =
                                state.manifest.menus.iter().find(|m| &m.uid == target_menu)
                            {
                                state.focused_button_uid =
                                    m.buttons.first().map(|b| b.uid.clone()).unwrap_or_default();
                            }
                            activate_menu(ctx);
                        }
                    }
                    "play_title" => {
                        if let Some(title_uid) = &action.title_uid {
                            if let Some(title) = state
                                .manifest
                                .titles
                                .iter()
                                .find(|title| &title.uid == title_uid)
                                .cloned()
                            {
                                clear_all_overlays(ctx, state);
                                state.in_menu_mode = false;
                                state.menu_rendered_once = false;
                                let was_menu_video = state.menu_background_video_loaded;
                                state.menu_background_video_loaded = false;
                                state.pending_title_uid = Some(title.uid.clone());
                                if let Some(video_file) = &title.video_track_file {
                                    let title_path = Path::new(video_file);
                                    let title_path = if title_path.is_absolute() {
                                        title_path.to_path_buf()
                                    } else {
                                        state.assets_dir.join(title_path)
                                    };
                                    set_mpv_loop(ctx, false);
                                    if !load_file(ctx, &title_path) {
                                        state.pending_title_uid = None;
                                        state.in_menu_mode = true;
                                        activate_menu(ctx);
                                    }
                                } else if was_menu_video {
                                    state.pending_disc_restore = false;
                                    set_mpv_loop(ctx, false);
                                    if !load_file(ctx, &state.disc_path) {
                                        state.pending_title_uid = None;
                                        state.in_menu_mode = true;
                                        activate_menu(ctx);
                                    }
                                } else {
                                    start_pending_title(ctx);
                                }
                            }
                        }
}
"set_variable" => {
render_active_menu(ctx);
}
_ => {}
}
}
}
_ => {}
}
}
unsafe fn render_active_menu(ctx: *mut mpv_handle) {
if PLAYER_STATE.is_null() {
return;
}
let state = &mut *PLAYER_STATE;
clear_all_overlays(ctx, state);
let menu = match state
.manifest
.menus
.iter()
.find(|m| m.uid == state.active_menu_uid)
.cloned()
{
Some(m) => m,
None => return,
};
let mut next_overlay_id: u32 = 1;
if menu.background.bg_type != "video" {
if let Some(bg_name) = &menu.background.name {
let bg_path = state.assets_dir.join(bg_name);
if push_image_overlay(
ctx,
state,
next_overlay_id,
&bg_path,
0,
0,
menu.canvas.width,
menu.canvas.height,
) {
next_overlay_id += 1;
}
}
}
for btn in &menu.buttons {
let is_focused = btn.uid == state.focused_button_uid;
let is_active = btn
.action
.as_ref()
.and_then(|a| {
if let (Some(k), Some(v)) = (&a.active_condition_key, &a.active_condition_value) {
Some(state.state_vars.get(k).map(|cur| cur == v).unwrap_or(false))
} else {
None
}
})
.unwrap_or(false);
let (x, y, w, h) = btn.rect;
if w <= 0 || h <= 0 {
continue;
}
let base_asset = if is_focused {
btn.assets.focused.as_ref().or(btn.assets.idle.as_ref())
} else {
btn.assets.idle.as_ref()
};
if let Some(asset_file) = base_asset {
let full_path = state.assets_dir.join(asset_file);
if push_image_overlay(ctx, state, next_overlay_id, &full_path, x, y, w as u32, h as u32)
{
next_overlay_id += 1;
}
}
if is_active {
if let Some(active_file) = &btn.assets.active {
let full_path = state.assets_dir.join(active_file);
if push_image_overlay(
ctx,
state,
next_overlay_id,
&full_path,
x,
y,
w as u32,
h as u32,
) {
next_overlay_id += 1;
}
}
}
}
}
unsafe fn push_image_overlay(
ctx: *mut mpv_handle,
state: &mut PlayerState,
overlay_id: u32,
img_path: &Path,
x: i32,
y: i32,
target_w: u32,
target_h: u32,
) -> bool {
if target_w == 0 || target_h == 0 {
eprintln!("[!] Skipping zero-sized menu image: {}", img_path.display());
return false;
}
let img = match image::open(img_path) {
Ok(image) => {
    image.resize_exact(target_w, target_h, image::imageops::FilterType::Lanczos3)
}
Err(error) => {
    eprintln!("[!] Unable to decode menu image {}: {}", img_path.display(), error);
    return false;
}
};
let rgba = img.to_rgba8();
let mut bgra = Vec::with_capacity((target_w * target_h * 4) as usize);
for px in rgba.pixels() {
bgra.push(px.0[2]); // B
bgra.push(px.0[1]); // G
bgra.push(px.0[0]); // R
bgra.push(px.0[3]); // A
}
// Write raw BGRA bitmap to disk so mpv maps a safe file path rather than a raw memory pointer
let bgra_file = state.cache_dir.join(format!("overlay_{}.bgra", overlay_id));
if let Err(error) = fs::write(&bgra_file, &bgra) {
eprintln!("[!] Unable to write menu overlay {}: {}", bgra_file.display(), error);
return false;
}
let cmd = CString::new("overlay-add").unwrap();
let id_s = CString::new(overlay_id.to_string()).unwrap();
let x_s = CString::new(x.to_string()).unwrap();
let y_s = CString::new(y.to_string()).unwrap();
let file_s = CString::new(bgra_file.to_string_lossy().to_string()).unwrap();
let off_s = CString::new("0").unwrap();
let fmt_s = CString::new("bgra").unwrap();
let w_s = CString::new(target_w.to_string()).unwrap();
let h_s = CString::new(target_h.to_string()).unwrap();
let stride_s = CString::new((target_w * 4).to_string()).unwrap();
let args: [*const c_char; 11] = [
cmd.as_ptr(),
id_s.as_ptr(),
x_s.as_ptr(),
y_s.as_ptr(),
file_s.as_ptr(),
off_s.as_ptr(),
fmt_s.as_ptr(),
w_s.as_ptr(),
h_s.as_ptr(),
stride_s.as_ptr(),
null_mut(),
];
let status = (mpv_api().command)(ctx, args.as_ptr() as *mut *const c_char);
if status < 0 {
    eprintln!(
        "[!] mpv rejected menu overlay {} with error {}",
        overlay_id, status
    );
    return false;
}
state.active_overlay_ids.push(overlay_id);
true
}
unsafe fn clear_all_overlays(ctx: *mut mpv_handle, state: &mut PlayerState) {
let cmd = CString::new("overlay-remove").unwrap();
for id in state.active_overlay_ids.drain(..) {
let id_s = CString::new(id.to_string()).unwrap();
let args: [*const c_char; 3] = [cmd.as_ptr(), id_s.as_ptr(), null_mut()];
let status = (mpv_api().command)(ctx, args.as_ptr() as *mut *const c_char);
if status < 0 {
    eprintln!("[!] mpv rejected overlay removal {} with error {}", id, status);
}
}
}
unsafe fn set_mpv_pause(ctx: *mut mpv_handle, paused: bool) {
let prop = CString::new("pause").unwrap();
let val = CString::new(if paused { "yes" } else { "no" }).unwrap();
let status = (mpv_api().set_property_string)(ctx, prop.as_ptr(), val.as_ptr());
if status < 0 {
    eprintln!("[!] mpv rejected pause={} with error {}", paused, status);
}
}
unsafe fn seek_mpv(ctx: *mut mpv_handle, seconds: f64) {
let cmd = CString::new("seek").unwrap();
let sec = CString::new(format!("{:.3}", seconds)).unwrap();
let mode = CString::new("absolute").unwrap();
let args: [*const c_char; 4] = [cmd.as_ptr(), sec.as_ptr(), mode.as_ptr(), null_mut()];
(mpv_api().command)(ctx, args.as_ptr() as *mut *const c_char);
}
unsafe fn get_mpv_string_prop(ctx: *mut mpv_handle, name: &str) -> Option<String> {
let prop = CString::new(name).unwrap();
let mut ptr: *mut c_char = null_mut();
let api = mpv_api();
let status = (api.get_property)(
ctx,
prop.as_ptr(),
MPV_FORMAT_STRING,
&mut ptr as *mut *mut c_char as *mut c_void,
);
if status >= 0 && !ptr.is_null() {
let res = CStr::from_ptr(ptr).to_string_lossy().to_string();
(api.free)(ptr as *mut c_void);
Some(res)
} else {
None
}
}
unsafe fn get_mouse_position(ctx: *mut mpv_handle) -> Option<(f64, f64)> {
    const MPV_FORMAT_NODE: mpv_format = 6;
    const MPV_FORMAT_INT64: mpv_format = 4;
    const MPV_FORMAT_DOUBLE: mpv_format = 5;

    let property = CString::new("mouse-pos").unwrap();
    let mut node = MousePosNode {
        value: MousePosNodeValue { int64: 0 },
        format: 0,
    };
    let api = mpv_api();
    let status = (api.get_property)(
        ctx,
        property.as_ptr(),
        MPV_FORMAT_NODE,
        &mut node as *mut MousePosNode as *mut c_void,
    );
    if status < 0 {
        return None;
    }

    let position = if node.format == MPV_FORMAT_NODE_MAP {
        let list = node.value.list;
        if list.is_null() || (*list).count < 1 || (*list).values.is_null() || (*list).keys.is_null()
        {
            None
        } else {
            let mut x = None;
            let mut y = None;
            for index in 0..(*list).count as usize {
                let key = *(*list).keys.add(index);
                if key.is_null() {
                    continue;
                }
                let key = CStr::from_ptr(key).to_bytes();
                let value = &*(*list).values.add(index);
                let number = match value.format {
                    MPV_FORMAT_INT64 => Some(value.value.int64 as f64),
                    MPV_FORMAT_DOUBLE => Some(value.value.double_),
                    _ => None,
                };
                match key {
                    b"x" => x = number,
                    b"y" => y = number,
                    _ => {}
                }
            }
            x.zip(y)
        }
    } else {
        None
    };
    (api.free_node_contents)(&mut node as *mut MousePosNode as *mut mpv_node);
    position
}

fn point_inside_button(x: f64, y: f64, rect: (i32, i32, i32, i32)) -> bool {
    let (left, top, width, height) = rect;
    width > 0
        && height > 0
        && x >= f64::from(left)
        && y >= f64::from(top)
        && x < f64::from(left) + f64::from(width)
        && y < f64::from(top) + f64::from(height)
}

unsafe fn define_menu_input(ctx: *mut mpv_handle) {
    let command = CString::new("define-section").unwrap();
    let section = CString::new("mkvdisc-menu").unwrap();
    let bindings = CString::new(
        "UP script-message mkvdisc-up\n\
         DOWN script-message mkvdisc-down\n\
         LEFT script-message mkvdisc-left\n\
         RIGHT script-message mkvdisc-right\n\
         ENTER script-message mkvdisc-select\n\
         MBTN_LEFT script-message mkvdisc-click",
    )
    .unwrap();
    let force = CString::new("force").unwrap();
    let args: [*const c_char; 5] = [
        command.as_ptr(),
        section.as_ptr(),
        bindings.as_ptr(),
        force.as_ptr(),
        null_mut(),
    ];
    let status = (mpv_api().command)(ctx, args.as_ptr() as *mut *const c_char);
    if status < 0 {
        eprintln!("[!] Could not define MKV-DISC menu controls: mpv error {status}.");
    }

    let global_section = CString::new("mkvdisc-global").unwrap();
    let global_binding = CString::new("Ctrl+m script-message mkvdisc-menu").unwrap();
    let global_args: [*const c_char; 5] = [
        command.as_ptr(),
        global_section.as_ptr(),
        global_binding.as_ptr(),
        force.as_ptr(),
        null_mut(),
    ];
    let status = (mpv_api().command)(ctx, global_args.as_ptr() as *mut *const c_char);
    if status < 0 {
        eprintln!("[!] Could not define MKV-DISC global controls: mpv error {status}.");
        return;
    }

    let enable = CString::new("enable-section").unwrap();
    let flags = CString::new("allow-hide-cursor+allow-vo-dragging").unwrap();
    let enable_args: [*const c_char; 4] = [
        enable.as_ptr(),
        global_section.as_ptr(),
        flags.as_ptr(),
        null_mut(),
    ];
    let status = (mpv_api().command)(ctx, enable_args.as_ptr() as *mut *const c_char);
    if status < 0 {
        eprintln!("[!] Could not enable MKV-DISC global controls: mpv error {status}.");
    }
}

unsafe fn set_menu_input_enabled(ctx: *mut mpv_handle, enabled: bool) {
    let command = CString::new(if enabled {
        "enable-section"
    } else {
        "disable-section"
    })
    .unwrap();
    let section = CString::new("mkvdisc-menu").unwrap();
    let flags = CString::new("allow-hide-cursor+allow-vo-dragging").unwrap();
    let args: [*const c_char; 4] = if enabled {
        [
            command.as_ptr(),
            section.as_ptr(),
            flags.as_ptr(),
            null_mut(),
        ]
    } else {
        [command.as_ptr(), section.as_ptr(), null_mut(), null_mut()]
    };
    let status = (mpv_api().command)(ctx, args.as_ptr() as *mut *const c_char);
    if status < 0 {
        eprintln!(
            "[!] Could not {} MKV-DISC menu controls: mpv error {status}.",
            if enabled { "enable" } else { "disable" }
        );
    }
}

#[cfg(test)]
mod tests {
    use super::{find_mpv_video_track_id, point_inside_button, MpvVideoTrack};

    #[test]
    fn button_hit_test_uses_half_open_rectangle() {
        assert!(point_inside_button(10.0, 20.0, (10, 20, 30, 40)));
        assert!(point_inside_button(39.99, 59.99, (10, 20, 30, 40)));
        assert!(!point_inside_button(40.0, 60.0, (10, 20, 30, 40)));
        assert!(!point_inside_button(9.99, 20.0, (10, 20, 30, 40)));
        assert!(!point_inside_button(10.0, 20.0, (10, 20, 0, 40)));
    }

    #[test]
    fn video_track_mapping_uses_mpvs_type_specific_id() {
        let tracks = [
            MpvVideoTrack {
                id: 1,
                source_id: 1,
            },
            MpvVideoTrack {
                id: 2,
                source_id: 2,
            },
            MpvVideoTrack {
                id: 3,
                source_id: 8,
            },
        ];
        assert_eq!(find_mpv_video_track_id(7, &tracks), Some(3));
        assert_eq!(find_mpv_video_track_id(8, &tracks), None);
    }
}
unsafe fn cleanup_state() {
if !PLAYER_STATE.is_null() {
let _ = Box::from_raw(PLAYER_STATE);
PLAYER_STATE = null_mut();
}
}