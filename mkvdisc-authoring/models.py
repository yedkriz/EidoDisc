from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any
import uuid


@dataclass
class Metadata:
    title: str = "Untitled MKV Disc"
    language: str = "en"


@dataclass
class Title:
    uid: str
    name: str
    start_ns: int
    end_ns: int
    menu_return: str
    video_track_uid: int | None = None
    video_track_file: str | None = None
    audio_track_uids: list[int] | None = None
    subtitle_track_uids: list[int] | None = None
    default_audio_track_uid: int | None = None
    default_subtitle_track_uid: int | None = None


@dataclass
class Canvas:
    width: int = 1920
    height: int = 1080


@dataclass
class MenuBackground:
    type: str = "attachment"
    name: str | None = None


@dataclass
class ButtonAction:
    type: str = "play_title"
    title_uid: str | None = None
    menu_uid: str | None = None
    set_var_key: str | None = None
    set_var_value: str | None = None
    active_condition_key: str | None = None
    active_condition_value: str | None = None


@dataclass
class ButtonAssets:
    idle: str | None = None
    focused: str | None = None
    active: str | None = None


@dataclass
class NavigationTargets:
    up: str | None = None
    down: str | None = None
    left: str | None = None
    right: str | None = None


@dataclass
class Button:
    uid: str
    label: str
    rect: tuple[int, int, int, int]
    action: ButtonAction = field(default_factory=ButtonAction)
    assets: ButtonAssets = field(default_factory=ButtonAssets)
    navigation: NavigationTargets = field(default_factory=NavigationTargets)
    vector_focus_shape: str | None = None


@dataclass
class Menu:
    uid: str
    name: str
    buttons: list[Button] = field(default_factory=list)
    canvas: Canvas = field(default_factory=Canvas)
    background: MenuBackground = field(default_factory=MenuBackground)
    vector_ui_layer: str | None = None


@dataclass
class Manifest:
    disc_uid: str = field(default_factory=lambda: f"urn:uuid:{uuid.uuid4()}")
    source_mode: str = "directory"
    metadata: Metadata = field(default_factory=Metadata)
    first_play: str = ""
    titles: list[Title] = field(default_factory=list)
    menus: list[Menu] = field(default_factory=list)
    initial_state_variables: dict[str, str] = field(default_factory=dict)
    default_audio_uid: int | None = None
    default_subtitle_uid: int | None = None
    format: str = "MKV-DISC"
    version: str = "0.2"

    def to_dict(self) -> dict[str, Any]:
        return {
            "format": self.format,
            "version": self.version,
            "disc_uid": self.disc_uid,
            "source_mode": self.source_mode,
            "metadata": asdict(self.metadata),
            "first_play": self.first_play,
            "titles": [asdict(title) for title in self.titles],
            "menus": [_menu_to_dict(menu) for menu in self.menus],
            "default_audio_uid": self.default_audio_uid,
            "default_subtitle_uid": self.default_subtitle_uid,
            "initial_state_variables": dict(self.initial_state_variables),
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_compiler_json(self) -> str:
        """Use attachment names in the container manifest, not workstation paths."""
        data = self.to_dict()
        if self.source_mode == "embedded":
            for title in data["titles"]:
                title["video_track_file"] = None
        for menu in data["menus"]:
            if menu["background"]["name"]:
                menu["background"]["name"] = Path(
                    menu["background"]["name"]
                ).name
                menu["background"]["type"] = (
                    "video"
                    if Path(menu["background"]["name"]).suffix.casefold()
                    in {".mkv", ".mp4", ".mov", ".webm", ".avi", ".m4v"}
                    else "attachment"
                )
            for button in menu["buttons"]:
                for state, path in button["assets"].items():
                    if path:
                        button["assets"][state] = Path(path).name
        return json.dumps(data, indent=2, ensure_ascii=False)

    def to_directory_json(self) -> str:
        """Create a portable sidecar manifest for a directory-mode export."""
        data = self.to_dict()
        data["source_mode"] = "directory"
        for title in data["titles"]:
            if title["video_track_file"]:
                title["video_track_file"] = Path(title["video_track_file"]).name
        for menu in data["menus"]:
            if menu["background"]["name"]:
                menu["background"]["name"] = Path(menu["background"]["name"]).name
                if Path(menu["background"]["name"]).suffix.casefold() in {
                    ".mkv", ".mp4", ".mov", ".webm", ".avi", ".m4v"
                }:
                    menu["background"]["type"] = "video"
                else:
                    menu["background"]["type"] = "file"
            for button in menu["buttons"]:
                for state, path in button["assets"].items():
                    if path:
                        button["assets"][state] = Path(path).name
        return json.dumps(data, indent=2, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Manifest:
        if data.get("format") != "MKV-DISC" or data.get("version") != "0.2":
            raise ValueError("Only MKV-DISC manifest version 0.2 is supported.")
        if data.get("source_mode") not in {"embedded", "directory"}:
            raise ValueError("source_mode must be 'embedded' or 'directory'.")
        return cls(
            disc_uid=data["disc_uid"],
            source_mode=data["source_mode"],
            metadata=Metadata(**data["metadata"]),
            first_play=data["first_play"],
            titles=[Title(**item) for item in data.get("titles", [])],
            menus=[_menu_from_dict(item) for item in data.get("menus", [])],
            initial_state_variables=data.get("initial_state_variables") or {},
            default_audio_uid=data.get("default_audio_uid"),
            default_subtitle_uid=data.get("default_subtitle_uid"),
        )

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        payload = json.loads(text)
        if not isinstance(payload, dict):
            raise ValueError("Manifest root must be a JSON object.")
        return cls.from_dict(payload)


def _button_to_dict(button: Button) -> dict[str, Any]:
    return {
        "uid": button.uid,
        "label": button.label,
        "rect": list(button.rect),
        "vector_focus_shape": button.vector_focus_shape,
        "assets": asdict(button.assets),
        "action": asdict(button.action),
        "navigation": asdict(button.navigation),
    }


def _menu_to_dict(menu: Menu) -> dict[str, Any]:
    return {
        "uid": menu.uid,
        "name": menu.name,
        "canvas": asdict(menu.canvas),
        "background": {
            "type": menu.background.type,
            "name": menu.background.name,
        },
        "vector_ui_layer": menu.vector_ui_layer,
        "buttons": [_button_to_dict(button) for button in menu.buttons],
    }


def _menu_from_dict(data: dict[str, Any]) -> Menu:
    buttons = []
    for item in data.get("buttons", []):
        assets = item.get("assets") or {}
        action = item.get("action") or {}
        navigation = item.get("navigation") or {}
        buttons.append(
            Button(
                uid=item["uid"],
                label=item["label"],
                rect=tuple(item["rect"]),
                vector_focus_shape=item.get("vector_focus_shape"),
                assets=ButtonAssets(**assets),
                action=ButtonAction(**action),
                navigation=NavigationTargets(**navigation),
            )
        )
    background = data.get("background") or {}
    return Menu(
        uid=data["uid"],
        name=data["name"],
        canvas=Canvas(**data.get("canvas", {})),
        background=MenuBackground(**background),
        vector_ui_layer=data.get("vector_ui_layer"),
        buttons=buttons,
    )


def new_project() -> Manifest:
    menu = Menu(uid="menu-main", name="Main Menu")
    return Manifest(first_play=menu.uid, menus=[menu])
