from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from models import Button, Manifest


class ButtonInspector(QScrollArea):
    changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self._button: Button | None = None
        self._manifest: Manifest | None = None
        self._updating = False
        body = QWidget()
        self.setWidget(body)
        self._form = QFormLayout(body)
        self._uid = QLineEdit()
        self._label = QLineEdit()
        self._geometry = [QSpinBox() for _ in range(4)]
        for spin in self._geometry:
            spin.setRange(0, 100000)
        self._asset_fields: dict[str, QLineEdit] = {}
        self._navigation: dict[str, QComboBox] = {}
        self._action_type = QComboBox()
        self._action_type.addItem("Play Title", "play_title")
        self._action_type.addItem("Jump Menu", "jump_menu")
        self._action_type.addItem("Set State Variable", "set_variable")
        self._target = QComboBox()
        self._set_key = QLineEdit()
        self._set_value = QLineEdit()
        self._condition_key = QLineEdit()
        self._condition_value = QLineEdit()

        self._form.addRow("Button UID", self._uid)
        self._form.addRow("Display label", self._label)
        for label, spin in zip(("X", "Y", "Width", "Height"), self._geometry):
            self._form.addRow(label, spin)
        for state in ("idle", "focused", "active"):
            field = QLineEdit()
            browse = QPushButton("Browse…")
            row = QWidget()
            layout = QHBoxLayout(row)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.addWidget(field)
            layout.addWidget(browse)
            browse.clicked.connect(lambda checked=False, s=state: self._browse_asset(s))
            field.editingFinished.connect(self._apply_changes)
            self._asset_fields[state] = field
            self._form.addRow(f"{state.title()} asset", row)
        self._form.addRow("Action", self._action_type)
        self._form.addRow("Action target", self._target)
        self._form.addRow("Set variable key", self._set_key)
        self._form.addRow("Set variable value", self._set_value)
        self._form.addRow("Active condition key", self._condition_key)
        self._form.addRow("Active condition value", self._condition_value)
        for direction in ("up", "down", "left", "right"):
            combo = QComboBox()
            combo.addItem("(none)", "")
            self._navigation[direction] = combo
            self._form.addRow(f"Navigate {direction}", combo)

        self._uid.editingFinished.connect(self._apply_changes)
        self._label.editingFinished.connect(self._apply_changes)
        for spin in self._geometry:
            spin.valueChanged.connect(self._apply_changes)
        self._action_type.currentIndexChanged.connect(self._action_type_changed)
        self._target.currentIndexChanged.connect(self._apply_changes)
        for widget in (self._set_key, self._set_value, self._condition_key, self._condition_value):
            widget.editingFinished.connect(self._apply_changes)
        for combo in self._navigation.values():
            combo.currentIndexChanged.connect(self._apply_changes)
        self.set_button(None, None)

    def set_button(self, button: Button | None, manifest: Manifest | None) -> None:
        self._button = button
        self._manifest = manifest
        self.setEnabled(button is not None)
        self._updating = True
        self._target.clear()
        self._target.addItem("(none)", "")
        self._uid.setText(button.uid if button else "")
        self._label.setText(button.label if button else "")
        rect = button.rect if button else (0, 0, 0, 0)
        for spin, value in zip(self._geometry, rect):
            spin.setValue(value)
        for state, field in self._asset_fields.items():
            field.setText(getattr(button.assets, state) or "" if button else "")
        if manifest:
            action = button.action if button else None
            action_type = action.type if action else "play_title"
            index = self._action_type.findData(action_type)
            self._action_type.setCurrentIndex(max(0, index))
            selected = (
                action.title_uid if action_type == "play_title" else
                action.menu_uid if action_type == "jump_menu" else None
            ) if action else None
            self._populate_targets(action_type, selected)
            for direction, combo in self._navigation.items():
                combo.clear()
                combo.addItem("(none)", "")
                for menu in manifest.menus:
                    for candidate in menu.buttons:
                        if candidate is not button:
                            combo.addItem(candidate.label, candidate.uid)
                value = getattr(button.navigation, direction) if button else None
                combo.setCurrentIndex(max(0, combo.findData(value)))
            self._set_key.setText(action.set_var_key or "" if action else "")
            self._set_value.setText(action.set_var_value or "" if action else "")
            self._condition_key.setText(action.active_condition_key or "" if action else "")
            self._condition_value.setText(action.active_condition_value or "" if action else "")
        self._updating = False

    def _populate_targets(self, action_type: str, selected: str | None) -> None:
        if self._manifest is None:
            return
        blocker = QSignalBlocker(self._target)
        self._target.clear()
        self._target.addItem("(none)", "")
        if action_type == "play_title":
            choices = [(title.uid, title.name) for title in self._manifest.titles]
        elif action_type == "jump_menu":
            choices = [(menu.uid, menu.name) for menu in self._manifest.menus]
        else:
            choices = []
        for uid, name in choices:
            self._target.addItem(name, uid)
        self._target.setCurrentIndex(max(0, self._target.findData(selected)))
        del blocker

    def _action_type_changed(self, *args) -> None:
        if self._updating:
            return
        action_type = self._action_type.currentData()
        self._populate_targets(action_type, None)
        self._apply_changes()

    def _browse_asset(self, state: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, f"Choose {state} image", "", "Images (*.png *.jpg *.jpeg)"
        )
        if path:
            self._asset_fields[state].setText(path)
            self._apply_changes()

    def _apply_changes(self, *args) -> None:
        if self._updating or self._button is None:
            return
        button = self._button
        button.uid = self._uid.text().strip() or button.uid
        button.label = self._label.text()
        button.rect = tuple(spin.value() for spin in self._geometry)
        for state, field in self._asset_fields.items():
            value = field.text().strip()
            setattr(button.assets, state, value or None)
        action_type = self._action_type.currentData()
        button.action.type = action_type
        button.action.title_uid = (
            self._target.currentData() if action_type == "play_title" else None
        )
        button.action.menu_uid = (
            self._target.currentData() if action_type == "jump_menu" else None
        )
        button.action.set_var_key = (
            self._set_key.text().strip() or None
            if action_type == "set_variable" else None
        )
        button.action.set_var_value = (
            self._set_value.text() or None
            if action_type == "set_variable" else None
        )
        button.action.active_condition_key = self._condition_key.text().strip() or None
        button.action.active_condition_value = self._condition_value.text() or None
        for direction, combo in self._navigation.items():
            setattr(button.navigation, direction, combo.currentData() or None)
        self.changed.emit()
