from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from PySide6.QtCore import QObject, QProcess, QThread, Signal

from models import Manifest


class MultiplexJob(QObject):
    output = Signal(str)
    finished = Signal(bool, str)

    def __init__(
        self,
        manifest: Manifest,
        output_path: str,
        mkvmerge_path: str,
        validator: object,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manifest = manifest
        self._output_path = Path(output_path).resolve()
        self._mkvmerge_path = mkvmerge_path
        self._validator = validator
        self._process = QProcess(self)
        self._process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self._process.readyReadStandardOutput.connect(self._read_stdout)
        self._process.readyReadStandardError.connect(self._read_stderr)
        self._process.finished.connect(self._process_finished)
        self._process.errorOccurred.connect(self._process_error)
        self._temporary_directory: tempfile.TemporaryDirectory[str] | None = None
        self._build_path: Path | None = None
        self._stdout_pending = ""
        self._stderr_pending = ""
        self._terminal_error: str | None = None
        self._finalizing = False

    def start(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            raise RuntimeError("The multiplex process is already running.")
        try:
            self._prepare()
            self._process.start(self._mkvmerge_path, self._arguments())
        except (OSError, ValueError, RuntimeError) as error:
            self._cleanup()
            self.finished.emit(False, str(error))

    def _prepare(self) -> None:
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_json = self._manifest.to_compiler_json()
        self._validator.validate(manifest_json)
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="mkvdisc-authoring-"
        )
        temp_dir = Path(self._temporary_directory.name)
        manifest_path = temp_dir / "mkvdisc.json"
        manifest_path.write_text(manifest_json, encoding="utf-8")
        self._manifest_path = manifest_path
        self._attachments = self._collect_attachments(temp_dir)
        self._source_files = self._input_files()
        if self._output_path in self._source_files:
            raise ValueError("The output file cannot overwrite a title video source.")
        if self._output_path.exists():
            raise FileExistsError(
                f"Output file already exists: {self._output_path}. Choose a new filename."
            )
        suffix = self._output_path.suffix or ".mkv"
        self._build_path = self._output_path.with_name(
            f".{self._output_path.stem}.{uuid.uuid4().hex}.building{suffix}"
        )

    def _input_files(self) -> list[Path]:
        paths: list[Path] = []
        seen: set[Path] = set()
        for title in self._manifest.titles:
            if not title.video_track_file:
                if self._manifest.source_mode == "embedded":
                    raise ValueError(
                        f"Embedded title '{title.uid}' needs its source MKV path "
                        "in the Titles tab so it can be multiplexed."
                    )
                continue
            path = Path(title.video_track_file).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"Title video does not exist: {path}")
            if path not in seen:
                seen.add(path)
                paths.append(path)
            if self._manifest.source_mode == "directory":
                break
        if paths:
            return paths
        raise FileNotFoundError(
            f"{self._manifest.source_mode.title()} mode needs at least one existing "
            "title video as a Matroska input."
        )

    def _collect_attachments(self, destination: Path) -> list[Path]:
        paths: dict[str, Path] = {}
        for menu in self._manifest.menus:
            if menu.background.name:
                paths[menu.background.name] = Path(menu.background.name)
            for button in menu.buttons:
                for asset_path in (
                    button.assets.idle,
                    button.assets.focused,
                    button.assets.active,
                ):
                    if asset_path:
                        paths[asset_path] = Path(asset_path)

        copied: list[Path] = []
        used_names: dict[str, str] = {}
        for reference, path in paths.items():
            resolved = path.expanduser().resolve()
            if not resolved.is_file():
                raise FileNotFoundError(
                    f"Referenced graphic asset does not exist: {reference}"
                )
            filename = resolved.name
            prior_reference = used_names.get(filename.casefold())
            if prior_reference and Path(prior_reference).resolve() != resolved:
                raise ValueError(
                    f"Two different assets share the attachment name '{filename}'. "
                    "Rename one of them before building."
                )
            used_names[filename.casefold()] = str(resolved)
            target = destination / filename
            if not target.exists():
                shutil.copy2(resolved, target)
                copied.append(target)
        return copied

    def _arguments(self) -> list[str]:
        args = ["--output", str(self._build_path)]
        args.extend(str(path) for path in self._source_files)
        for attachment in self._attachments:
            args.extend(["--attachment-name", attachment.name])
            if attachment.suffix.casefold() == ".png":
                args.extend(["--attachment-mime-type", "image/png"])
            elif attachment.suffix.casefold() in {".jpg", ".jpeg"}:
                args.extend(["--attachment-mime-type", "image/jpeg"])
            args.extend(["--attach-file", str(attachment)])
        return args

    def _read_stdout(self) -> None:
        self._stdout_pending += bytes(
            self._process.readAllStandardOutput()
        ).decode("utf-8", errors="replace")
        self._emit_complete_lines("stdout")

    def _read_stderr(self) -> None:
        self._stderr_pending += bytes(
            self._process.readAllStandardError()
        ).decode("utf-8", errors="replace")
        self._emit_complete_lines("stderr")

    def _emit_complete_lines(self, channel: str) -> None:
        attribute = "_stdout_pending" if channel == "stdout" else "_stderr_pending"
        pending = getattr(self, attribute)
        lines = pending.splitlines(keepends=True)
        remainder = ""
        for line in lines:
            if line.endswith(("\n", "\r")):
                self.output.emit(line.rstrip("\r\n"))
            else:
                remainder = line
        setattr(self, attribute, remainder)

    def _flush_pending(self) -> None:
        for attribute in ("_stdout_pending", "_stderr_pending"):
            pending = getattr(self, attribute)
            if pending:
                self.output.emit(pending.rstrip("\r\n"))
                setattr(self, attribute, "")

    def _process_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self._read_stdout()
        self._read_stderr()
        self._flush_pending()
        success = (
            self._terminal_error is None
            and exit_status == QProcess.ExitStatus.NormalExit
            and exit_code == 0
        )
        if self._finalizing:
            message = (
                f"Build complete: {self._output_path}"
                if success
                else self._terminal_error
                or f"mkvpropedit exited with code {exit_code} ({exit_status.name})."
            )
            if success:
                try:
                    self._publish_output()
                except OSError as error:
                    success = False
                    message = str(error)
            if not success:
                cleanup_error = self._remove_build_file()
                if cleanup_error:
                    message = f"{message} {cleanup_error}"
            self._cleanup()
            self.finished.emit(success, message)
            return
        if success and self._manifest.source_mode == "embedded":
            try:
                self._prepare_embedded_manifest()
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                message = str(error)
                cleanup_error = self._remove_build_file()
                if cleanup_error:
                    message = f"{message} {cleanup_error}"
                self._cleanup()
                self.finished.emit(False, message)
                return
            self.output.emit("Adding the validated manifest and title track mapping.")
            self._finalizing = True
            self._process.start(
                self._mkvpropedit_path(),
                [
                    str(self._build_path),
                    "--attachment-name",
                    "mkvdisc.json",
                    "--attachment-mime-type",
                    "application/json",
                    "--add-attachment",
                    str(self._manifest_path),
                ],
            )
            return
        message = (
            f"Build complete: {self._output_path}"
            if success
            else self._terminal_error
            or f"mkvmerge exited with code {exit_code} ({exit_status.name})."
        )
        if success:
            try:
                self._publish_output()
            except OSError as error:
                success = False
                message = str(error)
        if not success:
            cleanup_error = self._remove_build_file()
            if cleanup_error:
                message = f"{message} {cleanup_error}"
        self._cleanup()
        self.finished.emit(success, message)

    def _publish_output(self) -> None:
        if self._build_path is None:
            raise RuntimeError("The temporary build output was not initialized.")
        if self._output_path.exists():
            raise FileExistsError(
                f"Output file appeared during the build; refusing to overwrite: "
                f"{self._output_path}"
            )
        self._build_path.rename(self._output_path)

    def _remove_build_file(self) -> str | None:
        if self._build_path is None or not self._build_path.exists():
            return None
        try:
            self._build_path.unlink()
        except OSError as error:
            return f"Could not remove incomplete build file '{self._build_path}': {error}"
        return None

    def _prepare_embedded_manifest(self) -> None:
        manifest_data = json.loads(self._manifest.to_compiler_json())
        source_tracks: dict[Path, list[dict[str, object]]] = {}
        for source_path in self._source_files:
            source_tracks[source_path] = self._identify_tracks(source_path)
        output_tracks = self._identify_tracks(self._build_path)

        output_video_ids_by_uid: dict[int, list[int]] = {}
        for track in output_tracks:
            if track.get("type") != "video":
                continue
            properties = track.get("properties")
            if not isinstance(properties, dict):
                continue
            track_uid = properties.get("uid")
            track_id = track.get("id")
            if isinstance(track_uid, int) and isinstance(track_id, int):
                output_video_ids_by_uid.setdefault(track_uid, []).append(track_id)

        for title_data, title in zip(manifest_data["titles"], self._manifest.titles):
            source_path = Path(title.video_track_file or "").expanduser().resolve()
            tracks = source_tracks.get(source_path)
            if tracks is None:
                raise ValueError(
                    f"Embedded title '{title.uid}' has no multiplexed source video."
                )
            video_tracks = [track for track in tracks if track.get("type") == "video"]
            if title.video_track_uid is None:
                source_track = next(
                    (
                        track
                        for track in video_tracks
                        if isinstance(track.get("properties"), dict)
                        and track["properties"].get("default_track") is True
                    ),
                    video_tracks[0] if video_tracks else None,
                )
            else:
                source_track = next(
                    (
                        track
                        for track in video_tracks
                        if track.get("id") == title.video_track_uid
                    ),
                    None,
                )
            if source_track is None:
                raise ValueError(
                    f"Title '{title.uid}' references a video track that does not "
                    "exist in its source MKV."
                )
            properties = source_track.get("properties")
            source_uid = properties.get("uid") if isinstance(properties, dict) else None
            if not isinstance(source_uid, int):
                raise ValueError(
                    f"Could not identify the Matroska video track for title '{title.uid}'."
                )
            output_ids = output_video_ids_by_uid.get(source_uid, [])
            if len(output_ids) != 1:
                raise ValueError(
                    f"Could not uniquely map title '{title.uid}' to its video track "
                    "in the multiplexed MKV."
                )
            title_data["video_track_uid"] = output_ids[0]

        manifest_json = json.dumps(manifest_data, indent=2, ensure_ascii=False)
        self._validator.validate(manifest_json)
        self._manifest_path.write_text(manifest_json, encoding="utf-8")

    def _identify_tracks(self, path: Path) -> list[dict[str, object]]:
        result = subprocess.run(
            [self._mkvmerge_path, "-J", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip()
            raise RuntimeError(
                f"Could not identify tracks in '{path}': "
                f"{detail or f'mkvmerge exited with code {result.returncode}.'}"
            )
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as error:
            raise ValueError(
                f"mkvmerge returned invalid track information for '{path}': {error}"
            ) from error
        tracks = data.get("tracks")
        if not isinstance(tracks, list):
            raise ValueError(f"mkvmerge returned no track list for '{path}'.")
        return tracks

    def _mkvpropedit_path(self) -> str:
        sibling = Path(self._mkvmerge_path).with_name("mkvpropedit.exe")
        if sibling.is_file():
            return str(sibling)
        return "mkvpropedit"

    def _process_error(self, error: QProcess.ProcessError) -> None:
        executable = (
            self._mkvpropedit_path() if self._finalizing else self._mkvmerge_path
        )
        if error == QProcess.ProcessError.FailedToStart:
            self._terminal_error = (
                f"Could not start '{executable}': "
                f"{self._process.errorString()}"
            )
            cleanup_error = self._remove_build_file()
            if cleanup_error:
                self._terminal_error = f"{self._terminal_error} {cleanup_error}"
            self._cleanup()
            self.finished.emit(False, self._terminal_error)
        elif error == QProcess.ProcessError.Crashed:
            self._terminal_error = (
                f"'{executable}' crashed: {self._process.errorString()}"
            )

    def _cleanup(self) -> None:
        if self._temporary_directory is not None:
            self._temporary_directory.cleanup()
            self._temporary_directory = None


class DirectoryExportJob(QThread):
    output = Signal(str)
    completed = Signal(bool, str)

    def __init__(
        self,
        manifest: Manifest,
        output_directory: str,
        validator: object,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manifest = manifest
        self._output_directory = Path(output_directory).expanduser().resolve()
        self._validator = validator

    def run(self) -> None:
        try:
            self._export()
        except (OSError, ValueError, RuntimeError) as error:
            self.completed.emit(False, str(error))
        else:
            self.completed.emit(True, f"Directory export complete: {self._output_directory}")

    def _export(self) -> None:
        if self._manifest.source_mode != "directory":
            raise ValueError("Directory export requires source_mode 'directory'.")
        self._output_directory.mkdir(parents=True, exist_ok=True)
        manifest_json = self._manifest.to_directory_json()
        self._validator.validate(manifest_json)

        files: dict[str, tuple[str, Path]] = {}
        for title in self._manifest.titles:
            if not title.video_track_file:
                raise ValueError(f"Title '{title.uid}' has no video file.")
            self._add_file(files, title.video_track_file, f"title '{title.uid}'")
        for menu in self._manifest.menus:
            if menu.background.name:
                self._add_file(files, menu.background.name, f"background '{menu.name}'")
            for button in menu.buttons:
                for state, path in (
                    ("idle", button.assets.idle),
                    ("focused", button.assets.focused),
                    ("active", button.assets.active),
                ):
                    if path:
                        self._add_file(files, path, f"button '{button.uid}' {state} asset")

        output_manifest = self._output_directory / "mkvdisc.json"
        if output_manifest.exists():
            raise FileExistsError(f"Refusing to overwrite existing file: {output_manifest}")
        for name in files:
            destination = self._output_directory / name
            if destination.exists():
                raise FileExistsError(
                    f"Refusing to overwrite existing export file: {destination}"
                )

        with tempfile.TemporaryDirectory(
            prefix=".mkvdisc-export-", dir=self._output_directory
        ) as staging:
            stage = Path(staging)
            for index, (name, source) in enumerate(files.values(), start=1):
                destination = stage / name
                self.output.emit(f"Copying {source.name} ({index}/{len(files)})")
                shutil.copy2(source, destination)
            (stage / "mkvdisc.json").write_text(manifest_json, encoding="utf-8")
            for name, _ in files.values():
                (stage / name).replace(self._output_directory / name)
            (stage / "mkvdisc.json").replace(output_manifest)

    @staticmethod
    def _add_file(
        files: dict[str, tuple[str, Path]], reference: str, owner: str
    ) -> None:
        source = Path(reference).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"{owner} does not exist: {source}")
        name = source.name
        key = name.casefold()
        existing = files.get(key)
        if existing and existing[1] != source:
            raise ValueError(
                f"Different source files share the export name '{name}': "
                f"{existing[1]} and {source}"
            )
        files[key] = (name, source)
