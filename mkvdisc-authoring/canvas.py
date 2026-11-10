from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QLineF, QPointF, QRectF, QSizeF, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QGraphicsVideoItem
from PySide6.QtWidgets import (
    QGraphicsItem,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsView,
)

from models import Button, Manifest, Menu


class ButtonGraphicsItem(QGraphicsRectItem):
    HANDLE_SIZE = 10.0
    RESIZE_MARGIN = 9.0

    def __init__(
        self,
        button: Button,
        manifest: Manifest,
        on_geometry_changed,
        on_activated,
        parent: QGraphicsItem | None = None,
    ) -> None:
        x, y, width, height = button.rect
        super().__init__(QRectF(0, 0, width, height), parent)
        self.button = button
        self.manifest = manifest
        self._on_geometry_changed = on_geometry_changed
        self._on_activated = on_activated
        self._focused = False
        self._preview_mode: str | None = None
        self._resize_edges: tuple[bool, bool, bool, bool] | None = None
        self._press_scene = QPointF()
        self._press_rect = QRectF()
        self._activate_on_release = False
        self.setPos(x, y)
        self.setFlags(
            QGraphicsItem.GraphicsItemFlag.ItemIsMovable
            | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
            | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self._layers: list[QGraphicsPixmapItem] = []
        self._refresh_layers()
        self._refresh_style()

    def set_preview_mode(self, mode: str | None) -> None:
        self._preview_mode = mode
        self._refresh_layers()

    def set_state(self, manifest: Manifest) -> None:
        self.manifest = manifest
        self._refresh_layers()

    def hoverEnterEvent(self, event) -> None:
        self._focused = True
        self._refresh_layers()
        super().hoverEnterEvent(event)

    def hoverLeaveEvent(self, event) -> None:
        self._focused = False
        self._refresh_layers()
        super().hoverLeaveEvent(event)

    def itemChange(self, change, value):
        if (
            change == QGraphicsItem.GraphicsItemChange.ItemPositionChange
            and self.scene()
            and self.scene().snap_size
        ):
            snap = self.scene().snap_size
            return QPointF(
                round(value.x() / snap) * snap,
                round(value.y() / snap) * snap,
            )
        if change in (
            QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged,
            QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged,
        ):
            self._refresh_style()
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self._notify_geometry()
        return super().itemChange(change, value)

    def mousePressEvent(self, event) -> None:
        self._press_scene = event.scenePos()
        if event.button() == Qt.MouseButton.LeftButton and self.isSelected():
            x = event.pos().x()
            y = event.pos().y()
            rect = self.rect()
            left = x <= self.RESIZE_MARGIN
            right = x >= rect.width() - self.RESIZE_MARGIN
            top = y <= self.RESIZE_MARGIN
            bottom = y >= rect.height() - self.RESIZE_MARGIN
            if left or right or top or bottom:
                self._resize_edges = (left, top, right, bottom)
                self._press_scene = event.scenePos()
                self._press_rect = self.rect()
                self._activate_on_release = False
                event.accept()
                return
        self._activate_on_release = event.button() == Qt.MouseButton.LeftButton
        self._resize_edges = None
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._resize_edges is not None:
            left, top, right, bottom = self._resize_edges
            delta = event.scenePos() - self._press_scene
            rect = QRectF(self._press_rect)
            minimum = 12.0
            if left:
                rect.setLeft(min(rect.right() - minimum, rect.left() + delta.x()))
            if right:
                rect.setRight(max(rect.left() + minimum, rect.right() + delta.x()))
            if top:
                rect.setTop(min(rect.bottom() - minimum, rect.top() + delta.y()))
            if bottom:
                rect.setBottom(max(rect.top() + minimum, rect.bottom() + delta.y()))
            self.setRect(rect)
            self._refresh_layers()
            self._notify_geometry()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        activate = (
            self._activate_on_release
            and QLineF(self._press_scene, event.scenePos()).length() <= 4.0
        )
        self._resize_edges = None
        self._activate_on_release = False
        super().mouseReleaseEvent(event)
        if activate:
            self._on_activated(self.button)

    def paint(self, painter, option, widget=None) -> None:
        super().paint(painter, option, widget)
        if self.isSelected():
            painter.setBrush(QColor("#007acc"))
            painter.setPen(QPen(QColor("#f2f2f2"), 1))
            half = self.HANDLE_SIZE / 2
            for x in (0, self.rect().center().x(), self.rect().right()):
                for y in (0, self.rect().center().y(), self.rect().bottom()):
                    if x == self.rect().center().x() and y == self.rect().center().y():
                        continue
                    painter.drawRect(QRectF(x - half, y - half, self.HANDLE_SIZE, self.HANDLE_SIZE))

    def _refresh_style(self) -> None:
        color = QColor("#007acc") if self.isSelected() else QColor("#9b9ba8")
        pen = QPen(color, 2 if self.isSelected() else 1)
        pen.setStyle(Qt.PenStyle.DashLine)
        self.setPen(pen)
        self.setBrush(QColor(0, 122, 204, 30))
        self.update()

    def _refresh_layers(self) -> None:
        scene = self.scene()
        if scene:
            for layer in self._layers:
                scene.removeItem(layer)
        self._layers.clear()
        width = max(1, round(self.rect().width()))
        height = max(1, round(self.rect().height()))
        base_state = self._preview_mode
        force_active = base_state == "active"
        if base_state is None:
            base_state = "focused" if self._focused else "idle"
        elif force_active:
            base_state = "idle"
        base_path = getattr(self.button.assets, base_state, None)
        active_path = self.button.assets.active

        paths: list[str] = []
        if base_path:
            paths.append(base_path)
        if active_path and (force_active or self._active_condition_matches()):
            paths.append(active_path)
        for path in paths:
            pixmap = QPixmap(path)
            if pixmap.isNull():
                continue
            pixmap = pixmap.scaled(
                width,
                height,
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            layer = QGraphicsPixmapItem(pixmap)
            layer.setPos(self.pos())
            layer.setZValue(self.zValue() - 1 + len(self._layers) * 0.001)
            layer.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
            if scene:
                scene.addItem(layer)
            self._layers.append(layer)
        self.update()

    def _active_condition_matches(self) -> bool:
        key = self.button.action.active_condition_key
        value = self.button.action.active_condition_value
        return bool(
            key is not None
            and value is not None
            and self.manifest.initial_state_variables.get(key) == value
        )

    def _notify_geometry(self) -> None:
        x, y = round(self.pos().x()), round(self.pos().y())
        width, height = round(self.rect().width()), round(self.rect().height())
        self.button.rect = (x, y, width, height)
        self._on_geometry_changed(self.button)
        for layer in self._layers:
            layer.setPos(self.pos())


class CompositionScene(QGraphicsScene):
    button_selected = Signal(object)
    button_activated = Signal(object)
    geometry_changed = Signal(object)
    asset_dropped = Signal(str, object)
    background_video_error = Signal(str)
    add_button_requested = Signal(QPointF)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSceneRect(0, 0, 1920, 1080)
        self._manifest: Manifest | None = None
        self._menu: Menu | None = None
        self._items: dict[str, ButtonGraphicsItem] = {}
        self.snap_size = 0
        self._video_player = QMediaPlayer(self)
        self._audio_output = QAudioOutput(self)
        self._audio_output.setMuted(True)
        self._video_player.setAudioOutput(self._audio_output)
        self._video_player.setLoops(QMediaPlayer.Loops.Infinite)
        self._video_player.errorOccurred.connect(
            lambda error, message: self.background_video_error.emit(
                f"Background video preview error ({error.name}): {message}"
            )
        )
        self._video_item: QGraphicsVideoItem | None = None
        self.selectionChanged.connect(self._selection_changed)

    def load_menu(self, manifest: Manifest, menu: Menu) -> None:
        self._video_player.stop()
        self._video_player.setVideoOutput(None)
        self.clear()
        self._manifest = manifest
        self._menu = menu
        self._video_item = None
        self._items.clear()
        self.setSceneRect(0, 0, menu.canvas.width, menu.canvas.height)
        if menu.background.name and Path(menu.background.name).is_file():
            background_path = Path(menu.background.name)
            if menu.background.type == "video":
                self._video_item = QGraphicsVideoItem()
                self._video_item.setSize(QSizeF(menu.canvas.width, menu.canvas.height))
                self._video_item.setAspectRatioMode(Qt.AspectRatioMode.IgnoreAspectRatio)
                self._video_item.setZValue(-99)
                self._video_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
                self.addItem(self._video_item)
                self._video_player.setVideoOutput(self._video_item)
                self._video_player.setSource(QUrl.fromLocalFile(str(background_path.resolve())))
                self._video_player.play()
            else:
                background = QPixmap(menu.background.name)
                if not background.isNull():
                    background_item = self.addPixmap(
                        background.scaled(
                            menu.canvas.width,
                            menu.canvas.height,
                            Qt.AspectRatioMode.IgnoreAspectRatio,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    background_item.setZValue(-100)
                    background_item.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        for button in menu.buttons:
            self.add_button(button)

    def add_button(self, button: Button) -> ButtonGraphicsItem:
        if self._manifest is None:
            raise RuntimeError("Load a menu before adding buttons.")
        item = ButtonGraphicsItem(
            button,
            self._manifest,
            self.geometry_changed.emit,
            self.button_activated.emit,
        )
        self.addItem(item)
        item.set_state(self._manifest)
        self._items[button.uid] = item
        return item

    def sync_button(self, button: Button) -> None:
        item = next(
            (candidate for candidate in self._items.values() if candidate.button is button),
            None,
        )
        if item is None:
            return
        for uid, candidate in tuple(self._items.items()):
            if candidate is item and uid != button.uid:
                del self._items[uid]
                self._items[button.uid] = item
                break
        x, y, width, height = button.rect
        item.setPos(x, y)
        item.setRect(0, 0, width, height)
        item._refresh_layers()
        item.update()

    def set_preview_mode(self, mode: str | None) -> None:
        for item in self._items.values():
            item.set_preview_mode(mode)

    def refresh_state_layers(self, manifest: Manifest) -> None:
        for item in self._items.values():
            item.set_state(manifest)

    def mouseDoubleClickEvent(self, event) -> None:
        item = self.itemAt(event.scenePos(), self.views()[0].transform())
        if not isinstance(item, ButtonGraphicsItem):
            self.add_button_requested.emit(event.scenePos())
        super().mouseDoubleClickEvent(event)

    def _selection_changed(self) -> None:
        selected = self.selectedItems()
        self.button_selected.emit(
            selected[0].button if selected and isinstance(selected[0], ButtonGraphicsItem) else None
        )


class CompositionView(QGraphicsView):
    def __init__(self, scene: CompositionScene, parent=None) -> None:
        super().__init__(scene, parent)
        self.setBackgroundBrush(QColor("#141419"))
        self.setRenderHints(
            self.renderHints()
            | QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self._grid_size = 0
        self._grid_enabled = False
        self._safe_areas = True
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def set_grid(self, size: int, enabled: bool) -> None:
        self._grid_size = size
        self._grid_enabled = enabled and size > 0
        self.viewport().update()

    def set_safe_areas(self, enabled: bool) -> None:
        self._safe_areas = enabled
        self.viewport().update()

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event) -> None:
        paths = [
            url.toLocalFile()
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]
        if not paths:
            super().dropEvent(event)
            return
        path = paths[0]
        suffix = Path(path).suffix.casefold()
        if suffix not in {".png", ".jpg", ".jpeg", ".mkv", ".mp4", ".mov", ".webm", ".avi", ".m4v"}:
            event.ignore()
            return
        scene = self.scene()
        if not isinstance(scene, CompositionScene):
            event.ignore()
            return
        point = self.mapToScene(event.position().toPoint())
        button_item = next(
            (
                item
                for item in scene.items(point)
                if isinstance(item, ButtonGraphicsItem)
            ),
            None,
        )
        if button_item and suffix not in {".png", ".jpg", ".jpeg"}:
            event.ignore()
            return
        scene.asset_dropped.emit(path, button_item.button if button_item else None)
        event.acceptProposedAction()

    def drawBackground(self, painter, rect: QRectF) -> None:
        painter.fillRect(rect, QColor("#141419"))
        if self._grid_enabled and self._grid_size:
            pen = QPen(QColor(255, 255, 255, 20), 0)
            painter.setPen(pen)
            left = int(rect.left()) - (int(rect.left()) % self._grid_size)
            top = int(rect.top()) - (int(rect.top()) % self._grid_size)
            for x in range(left, int(rect.right()) + self._grid_size, self._grid_size):
                painter.drawLine(x, int(rect.top()), x, int(rect.bottom()))
            for y in range(top, int(rect.bottom()) + self._grid_size, self._grid_size):
                painter.drawLine(int(rect.left()), y, int(rect.right()), y)
        if self._safe_areas:
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1, Qt.PenStyle.DashLine))
            scene = self.sceneRect()
            painter.drawRect(scene.adjusted(scene.width() * 0.05, scene.height() * 0.05, -scene.width() * 0.05, -scene.height() * 0.05))
            painter.setPen(QPen(QColor(255, 215, 0, 100), 1, Qt.PenStyle.DashLine))
            painter.drawRect(scene.adjusted(scene.width() * 0.1, scene.height() * 0.1, -scene.width() * 0.1, -scene.height() * 0.1))
