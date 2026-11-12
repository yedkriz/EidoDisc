from __future__ import annotations

import json
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid

from PySide6.QtCore import QMimeData, QSignalBlocker, QUrl, Qt
from PySide6.QtGui import QAction, QColor, QDrag, QIcon, QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from canvas import ButtonGraphicsItem, CompositionScene, CompositionView
from compiler import DirectoryExportJob, MultiplexJob
from ffi_validator import FfiValidator, ManifestValidationError
from inspector import ButtonInspector
from models import Button, ButtonAction, ButtonAssets, Manifest, Menu, Title, new_project


ROLE_KIND = Qt.ItemDataRole.UserRole
ROLE_UID = Qt.ItemDataRole.UserRole + 1
MAX_U32 = (1 << 32) - 1


class AssetListWidget(QListWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setDragDropMode(QListWidget.DragDropMode.DragOnly)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)

    def startDrag(self, supported_actions) -> None:
        item = self.currentItem()
        path = item.data(Qt.ItemDataRole.UserRole) if item else None
        if not path:
            return
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(path)])
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.DropAction.CopyAction)


class AuthoringWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MKV-DISC Authoring")
        self.resize(1500, 960)
        self.manifest = new_project()
        self.project_path: Path | None = None
        self._active_menu_uid = self.manifest.first_play
        self._validator: FfiValidator | None = None
        self._job: MultiplexJob | None = None
        self._directory_job: DirectoryExportJob | None = None
        self._grid_size = 0
        self._asset_paths: set[str] = set()

        self.scene = CompositionScene(self)
        self.view = CompositionView(self.scene, self)
        self.setCentralWidget(self.view)
        self._build_docks()
        self._build_actions()
        self._build_toolbar()
        self._connect_signals()
        self._apply_theme()
        self._refresh_all()

    def _build_docks(self) -> None:
        self.hierarchy = QTreeWidget()
        self.hierarchy.setHeaderHidden(True)
        self.variables = QTableWidget(0, 2)
        self.variables.setHorizontalHeaderLabels(["Variable", "Value"])
        self.variables.horizontalHeader().setStretchLastSection(True)
        left_tabs = QTabWidget()
        left_tabs.addTab(self.hierarchy, "Disc")
        left_tabs.addTab(self.variables, "State")
        left_dock = QDockWidget("Project Hierarchy", self)
        left_dock.setObjectName("projectHierarchyDock")
        left_dock.setWidget(left_tabs)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, left_dock)

        self.inspector = ButtonInspector()
        inspector_dock = QDockWidget("Properties", self)
        inspector_dock.setObjectName("propertiesDock")
        inspector_dock.setWidget(self.inspector)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, inspector_dock)

        self.asset_list = AssetListWidget()
        self.asset_list.setViewMode(QListWidget.ViewMode.IconMode)
        self.asset_list.setIconSize(self.asset_list.iconSize().expandedTo(
            self.asset_list.iconSize().scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio)
        ))
        self.asset_list.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        bottom_tabs = QTabWidget()
        bottom_tabs.addTab(self.asset_list, "Asset Bin")
        bottom_tabs.addTab(self.log, "Compiler & Validation Log")

        self.titles_table = QTableWidget(0, 7)
        self.titles_table.setHorizontalHeaderLabels(
            ["UID", "Name", "Video file", "Video track ID", "Start (ns)", "End (ns)", "Menu return"]
        )
        self.titles_table.horizontalHeader().setStretchLastSection(True)
        self.titles_table.setAlternatingRowColors(True)
        bottom_tabs.addTab(self.titles_table, "Titles")
        self.export_panel = self._build_export_panel()
        bottom_tabs.addTab(self.export_panel, "Export")
        bottom_dock = QDockWidget("Assets, Titles & Build", self)
        bottom_dock.setObjectName("buildDock")
        bottom_dock.setWidget(bottom_tabs)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, bottom_dock)

    def _build_actions(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        self.new_action = QAction("&New Project", self)
        self.open_action = QAction("&Open Manifest…", self)
        self.save_action = QAction("&Save Project", self)
        self.save_as_action = QAction("Save Project &As…", self)
        for action in (self.new_action, self.open_action, self.save_action, self.save_as_action):
            file_menu.addAction(action)
        file_menu.addSeparator()
        self.quit_action = QAction("&Quit", self)
        file_menu.addAction(self.quit_action)
        self.delete_action = QAction("&Delete Selected", self)
        self.delete_action.setShortcut("Delete")
        self.delete_action.triggered.connect(self.delete_selected)
        self.addAction(self.delete_action)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Authoring", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)
        for action in (self.new_action, self.open_action, self.save_action):
            toolbar.addAction(action)
        self.open_project_button = QPushButton("Open Project…")
        toolbar.addWidget(self.open_project_button)
        toolbar.addSeparator()
        self.delete_button = QPushButton("Delete Selected")
        toolbar.addWidget(self.delete_button)
        self.import_media_button = QPushButton("Import Media…")
        self.import_assets_button = QPushButton("Import Assets…")
        self.background_button = QPushButton("Menu Background…")
        self.clear_background_button = QPushButton("Clear Background")
        self.add_menu_button = QPushButton("Add Menu")
        toolbar.addWidget(self.import_media_button)
        toolbar.addWidget(self.import_assets_button)
        toolbar.addWidget(self.background_button)
        toolbar.addWidget(self.clear_background_button)
        toolbar.addWidget(self.add_menu_button)

        toolbar.addWidget(QLabel("Grid"))
        self.grid_combo = QComboBox()
        self.grid_combo.addItem("Off", 0)
        for size in (8, 10, 16):
            self.grid_combo.addItem(f"{size}px", size)
        toolbar.addWidget(self.grid_combo)
        toolbar.addWidget(QLabel("Preview"))
        self.preview_combo = QComboBox()
        self.preview_combo.addItem("Live", "")
        self.preview_combo.addItem("Idle", "idle")
        self.preview_combo.addItem("Focused", "focused")
        self.preview_combo.addItem("Active", "active")
        toolbar.addWidget(self.preview_combo)
        self.safe_area_button = QPushButton("Safe Areas")
        self.safe_area_button.setCheckable(True)
        self.safe_area_button.setChecked(True)
        toolbar.addWidget(self.safe_area_button)

        metadata_toolbar = QToolBar("Manifest Metadata", self)
        metadata_toolbar.setMovable(False)
        self.addToolBar(metadata_toolbar)
        metadata_toolbar.addWidget(QLabel("Project"))
        self.metadata_title = QLineEdit()
        self.metadata_title.setMaximumWidth(260)
        metadata_toolbar.addWidget(self.metadata_title)
        metadata_toolbar.addWidget(QLabel("Language"))
        self.metadata_language = QLineEdit()
        self.metadata_language.setMaximumWidth(90)
        metadata_toolbar.addWidget(self.metadata_language)
        metadata_toolbar.addWidget(QLabel("First play"))
        self.first_play_combo = QComboBox()
        self.first_play_combo.setMinimumWidth(140)
        metadata_toolbar.addWidget(self.first_play_combo)

    def _build_export_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        form = QHBoxLayout()
        form.addWidget(QLabel("Output format"))
        self.export_mode_combo = QComboBox()
        self.export_mode_combo.addItem("Directory package", "directory")
        self.export_mode_combo.addItem("Embedded Matroska (.mkv)", "embedded")
        form.addWidget(self.export_mode_combo)
        form.addWidget(QLabel("Destination"))
        self.export_path = QLineEdit()
        self.export_path.setPlaceholderText("Choose an output folder or .mkv filename")
        form.addWidget(self.export_path, 1)
        self.browse_export_button = QPushButton("Browse…")
        form.addWidget(self.browse_export_button)
        self.build_button = QPushButton("Validate & Export")
        form.addWidget(self.build_button)
        layout.addLayout(form)
        progress_row = QHBoxLayout()
        self.export_progress_label = QLabel("Ready")
        self.export_progress_label.setMinimumWidth(190)
        progress_row.addWidget(self.export_progress_label)
        self.export_progress = QProgressBar()
        self.export_progress.setRange(0, 100)
        self.export_progress.setValue(0)
        progress_row.addWidget(self.export_progress, 1)
        layout.addLayout(progress_row)
        self.export_status = QLabel(
            "Directory exports include the manifest, title files, and referenced assets."
        )
        layout.addWidget(self.export_status)
        return panel

    def _connect_signals(self) -> None:
        self.new_action.triggered.connect(self.new_project)
        self.open_action.triggered.connect(self.open_project)
        self.save_action.triggered.connect(self.save_project)
        self.save_as_action.triggered.connect(self.save_project_as)
        self.quit_action.triggered.connect(self.close)
        self.import_media_button.clicked.connect(self.import_media)
        self.import_assets_button.clicked.connect(self.import_assets)
        self.background_button.clicked.connect(self.choose_menu_background)
        self.clear_background_button.clicked.connect(self.clear_menu_background)
        self.add_menu_button.clicked.connect(self.add_menu)
        self.open_project_button.clicked.connect(self.open_project)
        self.delete_button.clicked.connect(self.delete_selected)
        self.build_button.clicked.connect(self.choose_build_output)
        self.browse_export_button.clicked.connect(self.choose_export_destination)
        self.grid_combo.currentIndexChanged.connect(self._grid_changed)
        self.preview_combo.currentIndexChanged.connect(self._preview_changed)
        self.export_mode_combo.currentIndexChanged.connect(self._export_mode_changed)
        self.safe_area_button.toggled.connect(self.view.set_safe_areas)
        self.hierarchy.currentItemChanged.connect(self._hierarchy_selection_changed)
        self.hierarchy.itemChanged.connect(self._hierarchy_item_changed)
        self.variables.itemChanged.connect(self._state_variable_changed)
        self.titles_table.itemChanged.connect(self._title_cell_changed)
        self.asset_list.itemDoubleClicked.connect(self._asset_double_clicked)
        self.scene.button_selected.connect(self._canvas_selection_changed)
        self.scene.button_activated.connect(self._button_activated)
        self.scene.asset_dropped.connect(self._asset_dropped)
        self.scene.background_video_error.connect(self._background_video_error)
        self.scene.geometry_changed.connect(self._canvas_geometry_changed)
        self.scene.add_button_requested.connect(self._add_button_at)
        self.inspector.changed.connect(self._inspector_changed)
        self.metadata_title.editingFinished.connect(self._metadata_changed)
        self.metadata_language.editingFinished.connect(self._metadata_changed)
        self.first_play_combo.currentIndexChanged.connect(self._first_play_changed)

    def _apply_theme(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QDialog { background: #1e1e24; color: #e4e4eb; }
            QMenuBar, QMenu { background-color: #24242c; color: #f0f0f3; }
            QMenuBar::item:selected, QMenu::item:selected { background: #007acc; color: #ffffff; }
            QDockWidget::title { background: #2a2b36; padding: 6px; }
            QWidget { color: #e4e4eb; background-color: #1e1e24; }
            QScrollArea, QScrollArea QWidget#qt_scrollarea_viewport {
                background-color: #1e1e24; color: #e4e4eb;
            }
            QLineEdit, QSpinBox, QComboBox, QPlainTextEdit, QTreeWidget,
            QTableWidget, QListWidget {
                color: #f0f0f3; background-color: #17171d;
                border: 1px solid #383944; padding: 4px;
                selection-background-color: #007acc;
            }
            QComboBox QAbstractItemView { color: #f0f0f3; background-color: #24242c; }
            QPushButton { color: #f0f0f3; background-color: #30313d; border: 1px solid #484956; padding: 5px 8px; }
            QPushButton:hover { border-color: #007acc; }
            QPushButton:disabled { color: #888; }
            QHeaderView::section { background: #2a2b36; padding: 4px; border: 0; }
            QToolBar { spacing: 5px; background: #23232a; border: 0; padding: 3px; }
            QTabBar::tab { background: #2a2b36; padding: 6px 10px; }
            QTabBar::tab:selected { background: #007acc; }
            """
        )

    def _refresh_all(self) -> None:
        self._refresh_tree()
        self._refresh_variables()
        self._refresh_titles()
        self._refresh_assets()
        self._refresh_canvas()
        self.metadata_title.setText(self.manifest.metadata.title)
        self.metadata_language.setText(self.manifest.metadata.language)
        self._refresh_export_controls()
        self._refresh_first_play()
        self.inspector.set_button(None, self.manifest)

    def _refresh_export_controls(self) -> None:
        blocker = QSignalBlocker(self.export_mode_combo)
        self.export_mode_combo.setCurrentIndex(
            max(0, self.export_mode_combo.findData(self.manifest.source_mode))
        )
        del blocker
        self._export_mode_changed()

    def _export_mode_changed(self, index: int = -1) -> None:
        mode = self.export_mode_combo.currentData()
        if mode not in {"directory", "embedded"}:
            return
        self.manifest.source_mode = mode
        if mode == "directory":
            self.export_path.setPlaceholderText("Select a folder for the directory package")
            self.export_status.setText(
                "Exports mkvdisc.json, referenced images/videos, and title MKVs into a folder."
            )
        else:
            self.export_path.setPlaceholderText("Select a destination .mkv filename")
            self.export_status.setText(
                "Multiplexes title videos, the v0.2 manifest, and referenced assets into one MKV."
            )

    def choose_export_destination(self) -> None:
        if self.export_mode_combo.currentData() == "directory":
            path = QFileDialog.getExistingDirectory(
                self, "Choose directory package destination"
            )
            if path:
                self.export_path.setText(path)
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Choose embedded Matroska output",
            "mkvdisc-output.mkv",
            "Matroska video (*.mkv)",
        )
        if path:
            target = Path(path)
            if target.suffix.casefold() != ".mkv":
                target = target.with_suffix(".mkv")
            self.export_path.setText(str(target))

    def choose_menu_background(self) -> None:
        menu = self._find_menu(self._active_menu_uid)
        if menu is None:
            self._log("Select a menu before choosing its background.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose menu background image or looping video",
            "",
            "Menu backgrounds (*.png *.jpg *.jpeg *.mkv *.mp4 *.mov *.webm *.avi *.m4v)",
        )
        if not path:
            return
        menu.background.name = str(Path(path).resolve())
        menu.background.type = (
            "video"
            if Path(path).suffix.casefold()
            in {".mkv", ".mp4", ".mov", ".webm", ".avi", ".m4v"}
            else "attachment"
        )
        self._asset_paths.add(menu.background.name)
        self._refresh_canvas()
        self._refresh_assets()
        self._log(
            f"Set {menu.name} looping video background to {Path(path).name}."
            if menu.background.type == "video"
            else f"Set {menu.name} image background to {Path(path).name}."
        )

    def delete_selected(self) -> None:
        selected = self.scene.selectedItems()
        if selected and isinstance(selected[0], ButtonGraphicsItem):
            self._delete_button(selected[0].button)
            return
        item = self.hierarchy.currentItem()
        kind = item.data(0, ROLE_KIND) if item else None
        uid = item.data(0, ROLE_UID) if item else None
        if kind == "button":
            button = self._find_button(uid)
            if button:
                self._delete_button(button)
        elif kind == "title":
            title = next((title for title in self.manifest.titles if title.uid == uid), None)
            if title:
                self._delete_title(title)
        elif kind == "menu":
            menu = self._find_menu(uid)
            if menu:
                self._delete_menu(menu)
        elif self.asset_list.hasFocus() and self.asset_list.currentItem():
            path = self.asset_list.currentItem().data(Qt.ItemDataRole.UserRole)
            if path:
                self._delete_asset(path)
        elif self.titles_table.hasFocus():
            row = self.titles_table.currentRow()
            if 0 <= row < len(self.manifest.titles):
                self._delete_title(self.manifest.titles[row])
        else:
            self._log("Select a menu, title, button, or asset to delete.")

    def _delete_button(self, button: Button) -> None:
        menu = next(
            (menu for menu in self.manifest.menus if button in menu.buttons),
            None,
        )
        if menu is None:
            return
        menu.buttons.remove(button)
        for candidate in menu.buttons:
            for direction in ("up", "down", "left", "right"):
                if getattr(candidate.navigation, direction) == button.uid:
                    setattr(candidate.navigation, direction, None)
        self._refresh_all()
        self._log(f"Deleted button {button.label}.")

    def _delete_title(self, title: Title) -> None:
        if len(self.manifest.titles) == 1 and self.manifest.source_mode == "embedded":
            QMessageBox.warning(
                self,
                "Cannot delete title",
                "Embedded output needs at least one title video.",
            )
            return
        if QMessageBox.question(
            self,
            "Delete title",
            f"Delete title '{title.name}'? Buttons targeting it will become Set Variable actions.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.manifest.titles.remove(title)
        for menu in self.manifest.menus:
            for button in menu.buttons:
                if button.action.title_uid == title.uid:
                    button.action = ButtonAction(
                        type="set_variable",
                        set_var_key="selection",
                        set_var_value="none",
                    )
        self._refresh_all()
        self._log(f"Deleted title {title.name}.")

    def _delete_menu(self, menu: Menu) -> None:
        if len(self.manifest.menus) == 1:
            QMessageBox.warning(
                self, "Cannot delete menu", "A project must contain at least one menu."
            )
            return
        fallback = next(candidate for candidate in self.manifest.menus if candidate is not menu)
        if QMessageBox.question(
            self,
            "Delete menu",
            f"Delete menu '{menu.name}'? Menu links and title returns will be redirected to '{fallback.name}'.",
        ) != QMessageBox.StandardButton.Yes:
            return
        self.manifest.menus.remove(menu)
        if self.manifest.first_play == menu.uid:
            self.manifest.first_play = fallback.uid
        for title in self.manifest.titles:
            if title.menu_return == menu.uid:
                title.menu_return = fallback.uid
        for candidate_menu in self.manifest.menus:
            for button in candidate_menu.buttons:
                if button.action.menu_uid == menu.uid:
                    button.action.menu_uid = fallback.uid
        self._active_menu_uid = fallback.uid
        self._refresh_all()
        self._log(f"Deleted menu {menu.name}.")

    def clear_menu_background(self) -> None:
        menu = self._find_menu(self._active_menu_uid)
        if menu is None:
            self._log("Select a menu before clearing its background.")
            return
        menu.background.name = None
        menu.background.type = "attachment"
        self._refresh_canvas()
        self._refresh_assets()
        self._log(f"Cleared {menu.name} background.")

    def _delete_asset(self, asset_path: str) -> None:
        for menu in self.manifest.menus:
            if menu.background.name == asset_path:
                menu.background.name = None
                menu.background.type = "attachment"
            for button in menu.buttons:
                for state in ("idle", "focused", "active"):
                    if getattr(button.assets, state) == asset_path:
                        setattr(button.assets, state, None)
        self._asset_paths.discard(asset_path)
        self._refresh_all()
        self._log(f"Removed asset {Path(asset_path).name} from the project.")

    def _background_video_error(self, message: str) -> None:
        self._log(message)

    def _refresh_tree(self) -> None:
        blocker = QSignalBlocker(self.hierarchy)
        self.hierarchy.clear()
        menus_root = QTreeWidgetItem(["Menu Pages"])
        titles_root = QTreeWidgetItem(["Playable Titles"])
        self.hierarchy.addTopLevelItem(menus_root)
        self.hierarchy.addTopLevelItem(titles_root)
        target_item = None
        for menu in self.manifest.menus:
            menu_item = QTreeWidgetItem([menu.name])
            menu_item.setData(0, ROLE_KIND, "menu")
            menu_item.setData(0, ROLE_UID, menu.uid)
            menu_item.setFlags(menu_item.flags() | Qt.ItemFlag.ItemIsEditable)
            menus_root.addChild(menu_item)
            if menu.uid == self._active_menu_uid:
                target_item = menu_item
            for button in menu.buttons:
                button_item = QTreeWidgetItem([button.label])
                button_item.setData(0, ROLE_KIND, "button")
                button_item.setData(0, ROLE_UID, button.uid)
                menu_item.addChild(button_item)
                if menu.uid == self._active_menu_uid and self._selected_button_uid() == button.uid:
                    target_item = button_item
        for title in self.manifest.titles:
            title_item = QTreeWidgetItem([title.name])
            title_item.setData(0, ROLE_KIND, "title")
            title_item.setData(0, ROLE_UID, title.uid)
            titles_root.addChild(title_item)
        self.hierarchy.expandAll()
        if target_item:
            self.hierarchy.setCurrentItem(target_item)
        del blocker
        self._refresh_first_play()

    def _refresh_first_play(self) -> None:
        blocker = QSignalBlocker(self.first_play_combo)
        self.first_play_combo.clear()
        for menu in self.manifest.menus:
            self.first_play_combo.addItem(menu.name, menu.uid)
        index = self.first_play_combo.findData(self.manifest.first_play)
        self.first_play_combo.setCurrentIndex(max(0, index))
        del blocker

    def _hierarchy_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if item.data(0, ROLE_KIND) == "menu":
            menu = self._find_menu(item.data(0, ROLE_UID))
            if menu:
                menu.name = item.text(0).strip() or menu.name
                self._refresh_first_play()

    def _selected_button_uid(self) -> str | None:
        selected = self.scene.selectedItems()
        if selected and isinstance(selected[0], ButtonGraphicsItem):
            return selected[0].button.uid
        return None

    def _refresh_variables(self) -> None:
        blocker = QSignalBlocker(self.variables)
        pairs = sorted(self.manifest.initial_state_variables.items())
        self.variables.setRowCount(len(pairs) + 1)
        for row, (key, value) in enumerate(pairs):
            self.variables.setItem(row, 0, QTableWidgetItem(key))
            self.variables.setItem(row, 1, QTableWidgetItem(value))
        self.variables.setItem(len(pairs), 0, QTableWidgetItem(""))
        self.variables.setItem(len(pairs), 1, QTableWidgetItem(""))
        del blocker

    def _refresh_titles(self) -> None:
        blocker = QSignalBlocker(self.titles_table)
        self.titles_table.setColumnCount(7)
        self.titles_table.setRowCount(len(self.manifest.titles))
        for row, title in enumerate(self.manifest.titles):
            values = (
                title.uid,
                title.name,
                title.video_track_file or "",
                str(title.video_track_uid or ""),
                str(title.start_ns),
                str(title.end_ns),
                title.menu_return,
            )
            for column, value in enumerate(values):
                self.titles_table.setItem(row, column, QTableWidgetItem(value))
        del blocker

    def _refresh_assets(self) -> None:
        self.asset_list.clear()
        candidates: set[str | None] = set(self._asset_paths)
        for menu in self.manifest.menus:
            candidates.add(menu.background.name)
            for button in menu.buttons:
                candidates.update(
                    (button.assets.idle, button.assets.focused, button.assets.active)
                )
        found: set[str] = set()
        for candidate in candidates:
            if not candidate:
                continue
            path = Path(candidate).expanduser()
            if not path.is_file() or str(path.resolve()) in found:
                continue
            found.add(str(path.resolve()))
            item = QListWidgetItem(path.name)
            item.setToolTip(str(path.resolve()))
            item.setData(Qt.ItemDataRole.UserRole, str(path.resolve()))
            pixmap = QPixmap(str(path)) if path.suffix.casefold() in {".png", ".jpg", ".jpeg"} else QPixmap()
            if not pixmap.isNull():
                item.setIcon(QIcon(pixmap.scaled(64, 64, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)))
            self.asset_list.addItem(item)

    def _refresh_canvas(self) -> None:
        menu = self._find_menu(self._active_menu_uid)
        if menu is None and self.manifest.menus:
            menu = self.manifest.menus[0]
            self._active_menu_uid = menu.uid
        if menu:
            self.scene.load_menu(self.manifest, menu)
            self.view.fitInView(self.scene.sceneRect(), Qt.AspectRatioMode.KeepAspectRatio)
        else:
            self.scene.clear()
        self.inspector.set_button(None, self.manifest)

    def _find_menu(self, uid: str) -> Menu | None:
        return next((menu for menu in self.manifest.menus if menu.uid == uid), None)

    def _find_button(self, uid: str) -> Button | None:
        for menu in self.manifest.menus:
            for button in menu.buttons:
                if button.uid == uid:
                    return button
        return None

    def _hierarchy_selection_changed(self, current, previous) -> None:
        if current is None:
            return
        kind = current.data(0, ROLE_KIND)
        uid = current.data(0, ROLE_UID)
        if kind == "menu":
            self._active_menu_uid = uid
            self._refresh_canvas()
        elif kind == "button":
            menu = next((menu for menu in self.manifest.menus if self._find_button_in(menu, uid)), None)
            if menu:
                self._active_menu_uid = menu.uid
                self._refresh_canvas()
                for item in self.scene.items():
                    if isinstance(item, ButtonGraphicsItem) and item.button.uid == uid:
                        item.setSelected(True)
                        self.view.centerOn(item)
                        break

    @staticmethod
    def _find_button_in(menu: Menu, uid: str) -> Button | None:
        return next((button for button in menu.buttons if button.uid == uid), None)

    def _canvas_selection_changed(self, button) -> None:
        self.inspector.set_button(button, self.manifest)
        if button:
            self._refresh_tree()

    def _button_activated(self, button: Button) -> None:
        action = button.action
        if action.type == "set_variable":
            if not action.set_var_key or action.set_var_value is None:
                self._log(
                    f"Preview cannot apply {button.label}: set-variable key/value is incomplete."
                )
                return
            self.manifest.initial_state_variables[action.set_var_key] = action.set_var_value
            self._refresh_variables()
            self.scene.refresh_state_layers(self.manifest)
            self._log(
                f"Preview state: {action.set_var_key} = {action.set_var_value}"
            )
        elif action.type == "jump_menu":
            target = self._find_menu(action.menu_uid or "")
            if target:
                self._active_menu_uid = target.uid
                self._refresh_tree()
                self._refresh_canvas()
                self._log(f"Preview navigation: opened {target.name}.")
            else:
                self._log(f"Preview navigation target not found: {action.menu_uid}.")
        elif action.type == "play_title":
            self._log(
                f"Preview action: play title {action.title_uid or '(unset)'} "
                "(playback is handled by the player runtime)."
            )

    def _asset_dropped(self, path: str, button: Button | None) -> None:
        asset_path = str(Path(path).resolve())
        self._asset_paths.add(asset_path)
        if button:
            if Path(path).suffix.casefold() not in {".png", ".jpg", ".jpeg"}:
                self._log("Video files can only be used as menu backgrounds.")
                return
            button.assets.idle = asset_path
            self.scene.refresh_state_layers(self.manifest)
            self.inspector.set_button(button, self.manifest)
            self._log(f"Assigned {Path(asset_path).name} as {button.label}'s idle asset.")
        else:
            menu = self._find_menu(self._active_menu_uid)
            if menu:
                menu.background.name = asset_path
                menu.background.type = (
                    "video"
                    if Path(path).suffix.casefold()
                    in {".mkv", ".mp4", ".mov", ".webm", ".avi", ".m4v"}
                    else "attachment"
                )
                self._refresh_canvas()
                self._log(
                    f"Set {menu.name} background to {Path(asset_path).name}."
                )
        self._refresh_assets()

    def _canvas_geometry_changed(self, button) -> None:
        if self.inspector._button is button:
            self.inspector.set_button(button, self.manifest)

    def _inspector_changed(self) -> None:
        button = self.inspector._button
        if button is None:
            return
        self.scene.sync_button(button)
        self.scene.refresh_state_layers(self.manifest)
        self._refresh_tree()
        self._refresh_assets()

    def _state_variable_changed(self, item: QTableWidgetItem) -> None:
        state: dict[str, str] = {}
        for row in range(self.variables.rowCount()):
            key_item = self.variables.item(row, 0)
            value_item = self.variables.item(row, 1)
            key = key_item.text().strip() if key_item else ""
            value = value_item.text() if value_item else ""
            if key:
                state[key] = value
        self.manifest.initial_state_variables = state
        self.scene.refresh_state_layers(self.manifest)
        if item.row() == self.variables.rowCount() - 1 and item.text():
            self._refresh_variables()

    def _title_cell_changed(self, item: QTableWidgetItem) -> None:
        row = item.row()
        if row >= len(self.manifest.titles):
            return
        title = self.manifest.titles[row]
        value = item.text().strip()
        if item.column() == 0:
            title.uid = value or title.uid
        elif item.column() == 1:
            title.name = item.text()
        elif item.column() == 2:
            title.video_track_file = value or None
        elif item.column() == 3:
            try:
                track_id = int(value) if value else None
            except ValueError:
                self._log(f"Title {title.uid}: video track ID must be an integer.")
                self._refresh_titles()
                return
            if track_id is not None and not 0 <= track_id <= MAX_U32:
                self._log(
                    f"Title {title.uid}: video track ID must be between 0 and {MAX_U32}."
                )
                self._refresh_titles()
                return
            title.video_track_uid = track_id
        elif item.column() in (4, 5):
            try:
                number = int(value)
            except ValueError:
                self._log(f"Title {title.uid}: start/end must be an integer nanosecond value.")
                self._refresh_titles()
                return
            if number < 0:
                self._log(f"Title {title.uid}: start/end cannot be negative.")
                self._refresh_titles()
                return
            if item.column() == 4:
                title.start_ns = number
            else:
                title.end_ns = number
        elif item.column() == 6:
            title.menu_return = value
        self._refresh_tree()

    def _asset_double_clicked(self, item: QListWidgetItem) -> None:
        button = self.inspector._button
        if button is None:
            menu = self._find_menu(self._active_menu_uid)
            if menu:
                menu.background.name = item.data(Qt.ItemDataRole.UserRole)
                menu.background.type = (
                    "video"
                    if Path(menu.background.name).suffix.casefold()
                    in {".mkv", ".mp4", ".mov", ".webm", ".avi", ".m4v"}
                    else "attachment"
                )
                self._refresh_canvas()
                self._refresh_assets()
                self._log(f"Set {menu.name} background to {item.text()}.")
            return
        button.assets.idle = item.data(Qt.ItemDataRole.UserRole)
        self.inspector.set_button(button, self.manifest)
        self._inspector_changed()

    def _add_button_at(self, point) -> None:
        menu = self._find_menu(self._active_menu_uid)
        if menu is None:
            self._log("Create a menu before adding a button.")
            return
        x = max(0, min(menu.canvas.width - 320, round(point.x())))
        y = max(0, min(menu.canvas.height - 80, round(point.y())))
        uid = f"button-{uuid.uuid4().hex[:8]}"
        default_title = self.manifest.titles[0].uid if self.manifest.titles else None
        action = ButtonAction(
            type="play_title" if default_title else "set_variable",
            title_uid=default_title,
            set_var_key="selection" if default_title is None else None,
            set_var_value="selected" if default_title is None else None,
        )
        button = Button(
            uid=uid,
            label=f"Button {len(menu.buttons) + 1}",
            rect=(x, y, 320, 80),
            action=action,
            assets=ButtonAssets(),
        )
        menu.buttons.append(button)
        item = self.scene.add_button(button)
        item.setSelected(True)
        self._refresh_tree()
        self._refresh_assets()
        self._log(f"Added {button.label} to {menu.name}.")

    def _metadata_changed(self) -> None:
        self.manifest.metadata.title = self.metadata_title.text().strip() or "Untitled MKV Disc"
        self.manifest.metadata.language = self.metadata_language.text().strip() or "en"

    def _first_play_changed(self, index: int) -> None:
        uid = self.first_play_combo.currentData()
        if uid:
            self.manifest.first_play = uid

    def _grid_changed(self, index: int) -> None:
        self._grid_size = int(self.grid_combo.currentData() or 0)
        self.view.set_grid(self._grid_size, self._grid_size > 0)
        self.scene.snap_size = self._grid_size

    def _preview_changed(self, index: int) -> None:
        self.scene.set_preview_mode(self.preview_combo.currentData() or None)

    def new_project(self) -> None:
        self.manifest = new_project()
        self.project_path = None
        self._asset_paths.clear()
        self._active_menu_uid = self.manifest.first_play
        self._refresh_all()
        self._log("Created a new MKV-DISC v0.2 project in directory mode.")

    def open_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open MKV-DISC manifest", "", "JSON manifests (*.json)"
        )
        if not path:
            return
        try:
            manifest = Manifest.from_json(Path(path).read_text(encoding="utf-8"))
            self._resolve_relative_paths(manifest, Path(path).resolve().parent)
        except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
            QMessageBox.critical(self, "Open failed", str(error))
            self._log(f"Open failed: {error}")
            return
        self.manifest = manifest
        try:
            self._repair_legacy_video_track_ids()
        except ValueError as error:
            self._log(f"Legacy track ID migration deferred: {error}")
        self.project_path = Path(path).resolve()
        self._asset_paths.clear()
        self._active_menu_uid = manifest.first_play
        self._refresh_all()
        self._log(f"Opened manifest: {self.project_path}")

    @staticmethod
    def _resolve_relative_paths(manifest: Manifest, base: Path) -> None:
        for title in manifest.titles:
            if title.video_track_file and not Path(title.video_track_file).is_absolute():
                title.video_track_file = str((base / title.video_track_file).resolve())
        for menu in manifest.menus:
            if menu.background.name and not Path(menu.background.name).is_absolute():
                menu.background.name = str((base / menu.background.name).resolve())
            for button in menu.buttons:
                for state in ("idle", "focused", "active"):
                    path = getattr(button.assets, state)
                    if path and not Path(path).is_absolute():
                        setattr(button.assets, state, str((base / path).resolve()))

    def save_project(self) -> None:
        if self.project_path is None:
            self.save_project_as()
            return
        self._save_to(self.project_path)

    def save_project_as(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save MKV-DISC manifest", "mkvdisc.json", "JSON manifests (*.json)"
        )
        if path:
            target = Path(path)
            if target.suffix.casefold() != ".json":
                target = target.with_suffix(".json")
            self._save_to(target)

    def _save_to(self, path: Path) -> None:
        try:
            self._repair_legacy_video_track_ids()
            path.write_text(self.manifest.to_json(), encoding="utf-8")
        except (OSError, ValueError) as error:
            QMessageBox.critical(self, "Save failed", str(error))
            self._log(f"Save failed: {error}")
            return
        self.project_path = path.resolve()
        self._log(f"Saved v0.2 manifest: {self.project_path}")

    def _repair_legacy_video_track_ids(self) -> None:
        repairs: list[tuple[Title, int]] = []
        for title in self.manifest.titles:
            track_uid = title.video_track_uid
            if track_uid is None or 0 <= track_uid <= MAX_U32:
                continue
            if track_uid < 0:
                raise ValueError(
                    f"Title '{title.uid}' has a negative video track ID ({track_uid})."
                )
            if not title.video_track_file:
                raise ValueError(
                    f"Title '{title.uid}' has an out-of-range video track value "
                    f"({track_uid}) and no source MKV to recover its track ID from. "
                    "Re-import the source video."
                )
            try:
                track_id = self._probe_video_track_id(
                    Path(title.video_track_file).expanduser().resolve()
                )
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
                raise ValueError(
                    f"Could not recover the video track ID for title '{title.uid}': {error}"
                ) from error
            if track_id is None or not 0 <= track_id <= MAX_U32:
                raise ValueError(
                    f"No valid video track ID was found for title '{title.uid}'."
                )
            repairs.append((title, track_id))

        for title, track_id in repairs:
            old_value = title.video_track_uid
            title.video_track_uid = track_id
            self._log(
                f"Repaired legacy video track UID for '{title.uid}': "
                f"{old_value} -> track ID {track_id}."
            )

    def import_media(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Import directory-mode title video", "", "Matroska video (*.mkv)"
        )
        if not paths:
            return
        return_menu = self._active_menu_uid
        for path in paths:
            source = Path(path).resolve()
            video_track_id = None
            try:
                video_track_id = self._probe_video_track_id(source)
            except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
                self._log(f"Could not identify video track ID in {source.name}: {error}")
            uid = self._unique_uid(self._slug(source.stem), {title.uid for title in self.manifest.titles})
            self.manifest.titles.append(
                Title(
                    uid=uid,
                    name=source.stem,
                    video_track_file=str(source),
                    video_track_uid=video_track_id,
                    start_ns=0,
                    end_ns=1_000_000_000,
                    menu_return=return_menu,
                )
            )
        self._refresh_titles()
        self._refresh_tree()
        self._log(
            f"Imported {len(paths)} title video(s). Video track IDs were identified "
            "where available. Default end bound is 1 second; edit each title's "
            "bounds in the Titles tab."
        )

    def _probe_video_track_id(self, path: Path) -> int | None:
        result = subprocess.run(
            [self._resolve_mkvmerge(), "--identify", "--identification-format", "json", str(path)],
            capture_output=True,
            check=False,
            timeout=30,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "mkvmerge identification failed.")
        info = json.loads(result.stdout)
        tracks = info.get("tracks", [])
        for track in tracks:
            if track.get("type") == "video":
                track_id = track.get("id")
                if track_id is None:
                    return None
                parsed_id = int(track_id)
                if not 0 <= parsed_id <= MAX_U32:
                    raise ValueError(
                        f"mkvmerge returned video track ID {parsed_id}, outside the u32 range."
                    )
                return parsed_id
        return None

    def import_assets(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Import menu images and looping videos",
            "",
            "Media (*.png *.jpg *.jpeg *.mkv *.mp4 *.mov *.webm *.avi *.m4v)",
        )
        if not paths:
            return
        new_count = 0
        for path in paths:
            resolved = str(Path(path).resolve())
            if resolved not in self._asset_paths:
                self._asset_paths.add(resolved)
                new_count += 1
        self._refresh_assets()
        self._log(f"Imported {new_count} image/video asset(s) into the asset bin.")

    def add_menu(self) -> None:
        uid = self._unique_uid(
            f"menu-{len(self.manifest.menus) + 1}",
            {menu.uid for menu in self.manifest.menus},
        )
        menu = Menu(uid=uid, name=f"Menu Page {len(self.manifest.menus) + 1}")
        self.manifest.menus.append(menu)
        self._active_menu_uid = uid
        self._refresh_tree()
        self._refresh_canvas()
        self._log(f"Added menu page {uid}.")

    def choose_build_output(self) -> None:
        if self._job is not None or self._directory_job is not None:
            return
        mode = self.export_mode_combo.currentData()
        destination = self.export_path.text().strip()
        if not destination:
            self.choose_export_destination()
            destination = self.export_path.text().strip()
        if not destination:
            return
        if mode == "directory":
            self._start_directory_export(Path(destination))
        else:
            output_path = Path(destination)
            if output_path.suffix.casefold() != ".mkv":
                output_path = output_path.with_suffix(".mkv")
            self.export_path.setText(str(output_path))
            self._start_build(output_path)

    def _start_directory_export(self, output_directory: Path) -> None:
        self._begin_export_progress("Validating directory package…")
        try:
            self._repair_legacy_video_track_ids()
            validator = self._get_validator()
            self._log("Preflight: validating v0.2 directory manifest with the Rust core…")
            result = validator.validate(self.manifest.to_directory_json())
            self._log(
                f"Validation passed: {len(self.manifest.menus)} menu(s), "
                f"{len(self.manifest.titles)} title(s), source_mode=directory."
            )
        except (OSError, ValueError, ManifestValidationError, AttributeError) as error:
            self._log(f"Directory export validation failed: {error}")
            self._finish_export_progress(False)
            QMessageBox.warning(self, "Directory export failed", str(error))
            return

        self._directory_job = DirectoryExportJob(
            self.manifest,
            str(output_directory),
            validator,
            self,
        )
        self._directory_job.output.connect(self._log)
        self._directory_job.phase.connect(self._export_phase_changed)
        self._directory_job.progress.connect(self._directory_progress_changed)
        self._directory_job.completed.connect(self._directory_export_finished)
        self.build_button.setEnabled(False)
        self.export_mode_combo.setEnabled(False)
        self.browse_export_button.setEnabled(False)
        self._log(f"Starting directory export to {output_directory}…")
        self._directory_job.start()

    def _start_build(self, output_path: Path) -> None:
        self._begin_export_progress("Validating manifest…")
        try:
            self._repair_legacy_video_track_ids()
            validator = self._get_validator()
            self._log("Preflight: validating v0.2 manifest with the Rust core…")
            result = validator.validate(self.manifest.to_compiler_json())
            self._log(
                f"Validation passed: {len(self.manifest.menus)} menu(s), "
                f"{len(self.manifest.titles)} title(s), source_mode=embedded."
            )
        except (OSError, ValueError, ManifestValidationError, AttributeError) as error:
            self._log(f"Validation failed: {error}")
            self._finish_export_progress(False)
            QMessageBox.warning(self, "Validation failed", str(error))
            return

        self.manifest.source_mode = "embedded"
        mkvmerge = self._resolve_mkvmerge()
        self._job = MultiplexJob(
            self.manifest,
            str(output_path),
            mkvmerge,
            validator,
            self,
        )
        self._job.output.connect(self._log)
        self._job.phase.connect(self._export_phase_changed)
        self._job.progress.connect(self._embedded_progress_changed)
        self._job.finished.connect(self._build_finished)
        self.build_button.setEnabled(False)
        self.export_mode_combo.setEnabled(False)
        self.browse_export_button.setEnabled(False)
        self._log(
            f"Starting asynchronous mkvmerge. {self._build_input_description()} "
            "The manifest and referenced image attachments are included."
        )
        self._job.start()

    def _get_validator(self) -> FfiValidator:
        if self._validator is None:
            self._validator = FfiValidator()
        return self._validator

    @staticmethod
    def _resolve_mkvmerge() -> str:
        candidates = [
            shutil.which("mkvmerge"),
            r"C:\Program Files\MKVToolNix\mkvmerge.exe",
            r"C:\Program Files (x86)\MKVToolNix\mkvmerge.exe",
            str(Path(__file__).resolve().parent.parent / "mkvmerge.exe"),
        ]
        for candidate in candidates:
            if candidate and Path(candidate).is_file():
                return candidate
        return "mkvmerge.exe"

    def _carrier_name(self) -> str:
        for title in self.manifest.titles:
            if title.video_track_file and Path(title.video_track_file).is_file():
                return Path(title.video_track_file).name
        return "missing title video"

    def _build_input_description(self) -> str:
        if self.manifest.source_mode == "directory":
            return (
                f"Directory-mode titles remain external; '{self._carrier_name()}' "
                "carries the disc."
            )
        return "Embedded-mode title sources are multiplexed into the output."

    def _build_finished(self, success: bool, message: str) -> None:
        self._log(message)
        self._finish_export_progress(success)
        self.build_button.setEnabled(True)
        self.export_mode_combo.setEnabled(True)
        self.browse_export_button.setEnabled(True)
        self._job = None
        if success:
            self.statusBar().showMessage(message, 10000)
        else:
            QMessageBox.critical(self, "Build failed", message)

    def _directory_export_finished(self, success: bool, message: str) -> None:
        self._log(message)
        self._finish_export_progress(success)
        self.build_button.setEnabled(True)
        self.export_mode_combo.setEnabled(True)
        self.browse_export_button.setEnabled(True)
        if self._directory_job is not None:
            self._directory_job.wait()
        self._directory_job = None
        if success:
            self.statusBar().showMessage(message, 10000)
        else:
            QMessageBox.critical(self, "Directory export failed", message)

    def _begin_export_progress(self, phase: str) -> None:
        self.export_progress.setRange(0, 0)
        self.export_progress_label.setText(phase)

    def _export_phase_changed(self, phase: str) -> None:
        self.export_progress_label.setText(phase)
        if phase in {"Multiplexing", "Copying directory package"}:
            self.export_progress.setRange(0, 100)
            self.export_progress.setValue(0)
        else:
            self.export_progress.setRange(0, 0)

    def _embedded_progress_changed(self, percent: int) -> None:
        self.export_progress.setRange(0, 100)
        self.export_progress.setValue(min(95, int(percent * 0.95)))
        self.export_progress_label.setText(f"Multiplexing: {percent}%")

    def _directory_progress_changed(self, percent: int) -> None:
        self.export_progress.setRange(0, 100)
        self.export_progress.setValue(percent)
        self.export_progress_label.setText(f"Copying files: {percent}%")

    def _finish_export_progress(self, success: bool) -> None:
        self.export_progress.setRange(0, 100)
        self.export_progress.setValue(100 if success else 0)
        self.export_progress_label.setText(
            "Export complete" if success else "Export failed"
        )

    def _log(self, message: str) -> None:
        self.log.appendPlainText(message)

    def closeEvent(self, event) -> None:
        if self._job is not None or self._directory_job is not None:
            self._log("Wait for the current export to finish before closing the studio.")
            event.ignore()
            return
        event.accept()

    @staticmethod
    def _slug(value: str) -> str:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower()
        return slug or "title"

    @staticmethod
    def _unique_uid(candidate: str, existing: set[str]) -> str:
        uid = candidate
        counter = 2
        while uid in existing:
            uid = f"{candidate}-{counter}"
            counter += 1
        return uid


def main() -> int:
    application = QApplication(sys.argv)
    palette = application.palette()
    palette.setColor(QPalette.ColorRole.Window, QColor("#1e1e24"))
    palette.setColor(QPalette.ColorRole.WindowText, QColor("#f0f0f3"))
    palette.setColor(QPalette.ColorRole.Base, QColor("#17171d"))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor("#24242c"))
    palette.setColor(QPalette.ColorRole.Text, QColor("#f0f0f3"))
    palette.setColor(QPalette.ColorRole.Button, QColor("#30313d"))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor("#f0f0f3"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#007acc"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    application.setPalette(palette)
    window = AuthoringWindow()
    window.show()
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
