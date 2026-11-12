from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

AUTHORING_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AUTHORING_DIR))

from compiler import DirectoryExportJob, MultiplexJob
from ffi_validator import CValidationResult, FfiValidator, ManifestValidationError
from canvas import CompositionScene
from main import AuthoringWindow
from models import (
    Button,
    ButtonAction,
    ButtonAssets,
    Manifest,
    Menu,
    Title,
    new_project,
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication


class ManifestModelTests(unittest.TestCase):
    def test_v02_round_trip_preserves_reactive_fields_and_directory_paths(self) -> None:
        manifest = new_project()
        manifest.initial_state_variables = {"audio_lang": "en"}
        manifest.menus[0].buttons.append(
            Button(
                uid="audio-en",
                label="English Audio",
                rect=(40, 50, 300, 70),
                assets=ButtonAssets(
                    idle="assets/audio-idle.png",
                    focused="assets/audio-focus.png",
                    active="assets/audio-active.png",
                ),
                action=ButtonAction(
                    type="set_variable",
                    set_var_key="audio_lang",
                    set_var_value="en",
                    active_condition_key="audio_lang",
                    active_condition_value="en",
                ),
            )
        )
        manifest.titles.append(
            Title(
                uid="feature",
                name="Feature",
                video_track_file="media/feature.mkv",
                start_ns=0,
                end_ns=5_000_000_000,
                menu_return="menu-main",
            )
        )

        decoded = Manifest.from_json(manifest.to_json())
        self.assertEqual(decoded.version, "0.2")
        self.assertEqual(decoded.source_mode, "directory")
        self.assertEqual(decoded.titles[0].video_track_file, "media/feature.mkv")
        button = decoded.menus[0].buttons[0]
        self.assertEqual(button.assets.active, "assets/audio-active.png")
        self.assertEqual(button.action.active_condition_value, "en")
        self.assertEqual(decoded.initial_state_variables["audio_lang"], "en")

    def test_compiler_manifest_uses_attachment_basenames(self) -> None:
        manifest = new_project()
        manifest.menus[0].background.name = r"C:\art\main-background.png"
        manifest.menus[0].buttons.append(
            Button(
                uid="play",
                label="Play",
                rect=(0, 0, 100, 50),
                assets=ButtonAssets(idle=r"C:\art\play-idle.png"),
                action=ButtonAction(type="set_variable", set_var_key="selected"),
            )
        )
        payload = manifest.to_compiler_json()
        self.assertIn('"main-background.png"', payload)
        self.assertIn('"play-idle.png"', payload)
        self.assertNotIn(r"C:\art", payload)

    def test_embedded_compiler_manifest_omits_workstation_video_paths(self) -> None:
        manifest = new_project()
        manifest.source_mode = "embedded"
        manifest.titles.append(
            Title(
                uid="feature",
                name="Feature",
                video_track_uid=42,
                video_track_file=r"C:\media\feature.mkv",
                start_ns=0,
                end_ns=5_000_000_000,
                menu_return=manifest.first_play,
            )
        )
        payload = manifest.to_compiler_json()
        self.assertIn('"video_track_uid": 42', payload)
        self.assertIn('"video_track_file": null', payload)
        self.assertNotIn(r"C:\media", payload)

    def test_video_background_serializes_for_runtime_and_directory_export(self) -> None:
        manifest = new_project()
        manifest.menus[0].background.type = "video"
        manifest.menus[0].background.name = r"C:\art\loop.mp4"
        embedded = json.loads(manifest.to_compiler_json())
        directory = json.loads(manifest.to_directory_json())
        self.assertEqual(embedded["menus"][0]["background"]["type"], "video")
        self.assertEqual(directory["menus"][0]["background"]["type"], "video")
        self.assertEqual(directory["menus"][0]["background"]["name"], "loop.mp4")


class MultiplexJobTests(unittest.TestCase):
    def test_mkvmerge_progress_is_reported_separately_from_log_output(self) -> None:
        job = MultiplexJob(new_project(), "output.mkv", "mkvmerge", MagicMock())
        progress = []
        output = []
        job.progress.connect(progress.append)
        job.output.connect(output.append)

        job._handle_process_line("#GUI#progress 47")
        job._handle_process_line("Adding file: feature.mkv")
        job._handle_process_line("#GUI#progress 130")

        self.assertEqual(progress, [47, 100])
        self.assertEqual(output, ["Adding file: feature.mkv"])

    def test_embedded_manifest_maps_source_track_to_output_track_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "feature.mkv"
            source.touch()
            manifest = new_project()
            manifest.source_mode = "embedded"
            manifest.titles.append(
                Title(
                    uid="feature",
                    name="Feature",
                    video_track_uid=0,
                    video_track_file=str(source),
                    start_ns=0,
                    end_ns=5_000_000_000,
                    menu_return=manifest.first_play,
                )
            )
            job = MultiplexJob(manifest, str(root / "output.mkv"), "mkvmerge", MagicMock())
            temporary_directory = tempfile.TemporaryDirectory()
            self.addCleanup(temporary_directory.cleanup)
            job._temporary_directory = temporary_directory
            job._manifest_path = Path(temporary_directory.name) / "mkvdisc.json"
            job._source_files = [source.resolve()]
            job._build_path = root / "output.mkv"

            source_tracks = [
                {"id": 0, "type": "video", "properties": {"uid": 1234}},
                {
                    "id": 1,
                    "type": "audio",
                    "properties": {"uid": 1235, "default_track": False},
                },
                {
                    "id": 2,
                    "type": "audio",
                    "properties": {"uid": 1236, "default_track": True},
                },
                {
                    "id": 3,
                    "type": "subtitles",
                    "properties": {"uid": 1237, "default_track": True},
                },
                {
                    "id": 4,
                    "type": "subtitles",
                    "properties": {"uid": 1238, "default_track": False},
                },
            ]
            output_tracks = [
                {"id": 2, "type": "video", "properties": {"uid": 1234}},
                {"id": 4, "type": "audio", "properties": {"uid": 1235}},
                {"id": 5, "type": "audio", "properties": {"uid": 1236}},
                {"id": 6, "type": "subtitles", "properties": {"uid": 1237}},
                {"id": 7, "type": "subtitles", "properties": {"uid": 1238}},
            ]
            job._identify_tracks = MagicMock(
                side_effect=lambda path: (
                    source_tracks if path == source.resolve() else output_tracks
                )
            )

            job._prepare_embedded_manifest()

            exported = json.loads(job._manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(exported["titles"][0]["video_track_uid"], 2)
            self.assertEqual(exported["titles"][0]["audio_track_uids"], [5, 4])
            self.assertEqual(exported["titles"][0]["subtitle_track_uids"], [6, 7])
            self.assertEqual(exported["titles"][0]["default_audio_track_uid"], 5)
            self.assertEqual(exported["titles"][0]["default_subtitle_track_uid"], 6)
            self.assertIsNone(exported["titles"][0]["video_track_file"])
            job._validator.validate.assert_called_once()


class FfiValidatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        dll = os.environ.get("MKVDISC_DLL")
        if not dll:
            dll = str(AUTHORING_DIR.parent / "target" / "release" / "mkvdisc.dll")
        if not Path(dll).is_file():
            raise unittest.SkipTest(f"Rust validator DLL not found: {dll}")
        cls.validator = FfiValidator(dll)

    def test_ffi_result_layout_matches_rust_c_bool_and_pointer(self) -> None:
        self.assertEqual(ctypes.sizeof(CValidationResult), 16)

    def test_valid_v02_manifest(self) -> None:
        result = self.validator.validate(new_project().to_json())
        self.assertTrue(result.valid)
        self.assertIsNone(result.error_message)

    def test_valid_v02_embedded_manifest(self) -> None:
        manifest = new_project()
        manifest.source_mode = "embedded"
        manifest.titles.append(
            Title(
                uid="feature",
                name="Feature",
                video_track_uid=42,
                start_ns=0,
                end_ns=5_000_000_000,
                menu_return=manifest.first_play,
            )
        )
        result = self.validator.validate(manifest.to_json())
        self.assertTrue(result.valid)

    def test_validation_error_is_returned_as_diagnostic(self) -> None:
        with self.assertRaises(ManifestValidationError) as raised:
            self.validator.validate('{"format":"NOT-MKV-DISC"}')
        self.assertTrue(str(raised.exception))


class CanvasLayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_idle_focused_and_active_layers_follow_preview_and_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            paths = {}
            for name, color in (
                ("idle.png", QColor("red")),
                ("focused.png", QColor("green")),
                ("active.png", QColor("blue")),
            ):
                path = Path(temp) / name
                image = QImage(16, 16, QImage.Format.Format_ARGB32)
                image.fill(color)
                self.assertTrue(image.save(str(path)))
                paths[name] = str(path)

            manifest = new_project()
            manifest.initial_state_variables = {"audio_lang": "en"}
            button = Button(
                uid="audio-en",
                label="English Audio",
                rect=(20, 20, 80, 30),
                assets=ButtonAssets(
                    idle=paths["idle.png"],
                    focused=paths["focused.png"],
                    active=paths["active.png"],
                ),
                action=ButtonAction(
                    type="set_variable",
                    set_var_key="audio_lang",
                    set_var_value="en",
                    active_condition_key="audio_lang",
                    active_condition_value="en",
                ),
            )
            manifest.menus[0].buttons.append(button)
            scene = CompositionScene()
            scene.load_menu(manifest, manifest.menus[0])
            item = next(
                candidate
                for candidate in scene.items()
                if getattr(candidate, "button", None) is button
            )
            self.assertEqual(len(item._layers), 2)

            manifest.initial_state_variables["audio_lang"] = "fr"
            scene.refresh_state_layers(manifest)
            self.assertEqual(len(item._layers), 1)

            scene.set_preview_mode("focused")
            self.assertEqual(len(item._layers), 1)
            scene.set_preview_mode("active")
            self.assertEqual(len(item._layers), 2)


class AuthoringWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])

    def test_open_delete_and_export_controls_are_available(self) -> None:
        window = AuthoringWindow()
        window.show()
        self.application.processEvents()
        self.assertTrue(window.open_project_button.isVisible())
        self.assertTrue(window.delete_button.isVisible())
        self.assertFalse(window.export_progress.isHidden())
        self.assertEqual(window.titles_table.columnCount(), 7)
        self.assertEqual(window.titles_table.horizontalHeaderItem(3).text(), "Video track ID")
        self.assertEqual(window.export_mode_combo.currentData(), "directory")

        window.export_mode_combo.setCurrentIndex(
            window.export_mode_combo.findData("embedded")
        )
        self.assertEqual(window.manifest.source_mode, "embedded")
        self.assertIn(".mkv", window.export_path.placeholderText())

        button = Button(
            uid="delete-me",
            label="Delete me",
            rect=(0, 0, 80, 40),
            action=ButtonAction(type="set_variable", set_var_key="x"),
        )
        window.manifest.menus[0].buttons.append(button)
        window._delete_button(button)
        self.assertFalse(window.manifest.menus[0].buttons)
        window.close()

    def test_legacy_64_bit_track_uid_is_replaced_with_mkvmerge_track_id(self) -> None:
        window = AuthoringWindow()
        source = Path(tempfile.gettempdir()) / "legacy-title.mkv"
        window.manifest.titles = [
            Title(
                uid="legacy-title",
                name="Legacy title",
                video_track_file=str(source),
                video_track_uid=17681778840454397691,
                start_ns=0,
                end_ns=1_000_000_000,
                menu_return=window.manifest.first_play,
            )
        ]

        with patch.object(window, "_probe_video_track_id", return_value=3):
            window._repair_legacy_video_track_ids()

        self.assertEqual(window.manifest.titles[0].video_track_uid, 3)
        exported = json.loads(window.manifest.to_compiler_json())
        self.assertEqual(exported["titles"][0]["video_track_uid"], 3)
        window.close()

    def test_legacy_track_uid_without_source_reports_recovery_error(self) -> None:
        window = AuthoringWindow()
        window.manifest.titles = [
            Title(
                uid="legacy-title",
                name="Legacy title",
                video_track_uid=17681778840454397691,
                start_ns=0,
                end_ns=1_000_000_000,
                menu_return=window.manifest.first_play,
            )
        ]
        with self.assertRaisesRegex(ValueError, "Re-import the source video"):
            window._repair_legacy_video_track_ids()
        window.close()


class DirectoryExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.application = QApplication.instance() or QApplication([])
        dll = os.environ.get("MKVDISC_DLL")
        if not dll:
            dll = str(AUTHORING_DIR.parent / "target" / "release" / "mkvdisc.dll")
        if not Path(dll).is_file():
            raise unittest.SkipTest(f"Rust validator DLL not found: {dll}")
        cls.validator = FfiValidator(dll)

    def test_directory_export_copies_video_assets_and_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "feature.mkv"
            source.write_bytes(b"fixture video")
            image = root / "loop.mp4"
            image.write_bytes(b"fixture loop video")
            output = root / "export"

            manifest = new_project()
            manifest.titles.append(
                Title(
                    uid="feature",
                    name="Feature",
                    video_track_file=str(source),
                    start_ns=0,
                    end_ns=5_000_000_000,
                    menu_return=manifest.first_play,
                )
            )
            manifest.menus[0].background.name = str(image)
            manifest.menus[0].background.type = "video"
            job = DirectoryExportJob(manifest, str(output), self.validator)
            completed = []
            progress = []
            job.progress.connect(progress.append)
            job.completed.connect(lambda success, message: completed.append((success, message)))
            job.start()
            self.assertTrue(job.wait(10_000), "Directory export worker timed out.")
            self.application.processEvents()
            self.assertEqual(len(completed), 1)
            self.assertTrue(completed[0][0], completed[0][1])
            self.assertTrue(progress)
            self.assertEqual(progress[0], 0)
            self.assertTrue(all(a <= b for a, b in zip(progress, progress[1:])))

            exported = json.loads((output / "mkvdisc.json").read_text(encoding="utf-8"))
            self.assertTrue((output / "feature.mkv").is_file())
            self.assertTrue((output / "loop.mp4").is_file())
            self.assertEqual(exported["source_mode"], "directory")
            self.assertEqual(exported["titles"][0]["video_track_file"], "feature.mkv")
            self.assertEqual(exported["menus"][0]["background"]["type"], "video")
            self.assertEqual(exported["menus"][0]["background"]["name"], "loop.mp4")


if __name__ == "__main__":
    unittest.main()
