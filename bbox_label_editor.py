"""
바운딩박스 라벨 편집기 (XML 기반)
- 이미지 폴더를 열어 Pascal VOC XML 어노테이션과 매칭
- 바운딩박스 + 라벨을 이미지 위에 오버레이하여 표시
- 클릭으로 박스 선택 후 라벨 수정 가능
- XML 없는 이미지는 '작업 불가' 표시
"""
import os
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

# Qt 플러그인 경로 설정 (imageformats + platforms)
_qt_plugins = Path(sys.executable).parent / "Lib" / "site-packages" / "PyQt5" / "Qt5" / "plugins"
if _qt_plugins.exists():
    os.environ.setdefault("QT_PLUGIN_PATH", str(_qt_plugins))
    os.environ.setdefault("QT_QPA_PLATFORM_PLUGIN_PATH", str(_qt_plugins / "platforms"))

from PyQt5.QtCore import Qt, pyqtSignal, QRectF, QPointF, QByteArray
from PyQt5.QtGui import (
    QPixmap, QPainter, QPen, QColor, QFont, QBrush, QFontMetrics,
    QKeySequence, QImage,
)
from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QFileDialog, QListWidget,
    QListWidgetItem, QSplitter, QGroupBox, QMessageBox, QShortcut,
    QStatusBar,
)

# ── 스타일 ─────────────────────────────────────────────────────
STYLE = """
QMainWindow { background: #1a1a2e; }
QGroupBox {
    color: #e0e0e0; font-weight: bold; font-size: 13px;
    border: 1px solid #333; border-radius: 6px;
    margin-top: 10px; padding-top: 14px;
}
QGroupBox::title { subcontrol-position: top left; padding: 2px 8px; }
QLabel { color: #ccc; font-size: 12px; }
QLineEdit {
    background: #16213e; color: #e0e0e0; border: 1px solid #444;
    border-radius: 4px; padding: 5px 8px; font-size: 12px;
}
QPushButton {
    background: #0f3460; color: #e0e0e0; border: none;
    border-radius: 4px; padding: 6px 14px; font-size: 12px;
}
QPushButton:hover { background: #1a5276; }
QPushButton:disabled { background: #333; color: #666; }
QPushButton#btn_save {
    background: #4ecdc4; color: #1a1a2e; font-weight: bold; font-size: 13px;
}
QPushButton#btn_save:hover { background: #45b7aa; }
QPushButton#btn_apply {
    background: #e94560; color: #fff; font-weight: bold;
}
QPushButton#btn_apply:hover { background: #d63851; }
QListWidget {
    background: #16213e; color: #e0e0e0; border: 1px solid #333;
    border-radius: 4px; font-size: 11px; outline: none;
}
QListWidget::item { padding: 3px 6px; }
QListWidget::item:selected { background: #0f3460; }
QListWidget::item:hover { background: #1a3a5c; }
QStatusBar { color: #aaa; font-size: 11px; }
QSplitter::handle { background: #333; width: 3px; }
"""

# ── 색상 팔레트 ───────────────────────────────────────────────
COLORS = [
    "#FF4444", "#44FF44", "#4488FF", "#FFFF44", "#FF44FF", "#44FFFF",
    "#FF8844", "#8844FF", "#4488FF", "#FF4488", "#88FF44", "#44FF88",
    "#FF6666", "#66FF66", "#6666FF", "#FFAA44", "#AA44FF", "#44FFAA",
]


@dataclass
class BoxItem:
    index: int
    xmin: int
    ymin: int
    xmax: int
    ymax: int
    label: str
    modified: bool = False


@dataclass
class ImageEntry:
    filename: str
    image_path: str
    xml_path: str | None  # None = 작업불가
    boxes: list[BoxItem] = field(default_factory=list)
    tree: ET.ElementTree | None = None  # XML DOM (수정용)
    dirty: bool = False


# ── ImageCanvas ─────────────────────────────────────────────────
class ImageCanvas(QWidget):
    """이미지 + 바운딩박스 오버레이 위젯"""
    boxSelected = pyqtSignal(int)
    boxDeselected = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap: QPixmap | None = None
        self.boxes: list[BoxItem] = []
        self.selected_index: int | None = None
        self.unmatched = False
        self.scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.setMinimumSize(400, 300)
        self.setMouseTracking(True)

    def set_data(self, pixmap: QPixmap | None, boxes: list[BoxItem], unmatched: bool = False):
        self.pixmap = pixmap
        self.boxes = boxes
        self.selected_index = None
        self.unmatched = unmatched
        self._calc_transform()
        self.update()

    def select_box(self, idx: int | None):
        self.selected_index = idx
        self.update()

    def _calc_transform(self):
        if self.pixmap is None or self.pixmap.isNull():
            return
        w, h = self.width(), self.height()
        iw, ih = self.pixmap.width(), self.pixmap.height()
        if iw == 0 or ih == 0:
            return
        self.scale = min(w / iw, h / ih)
        sw, sh = iw * self.scale, ih * self.scale
        self.offset_x = (w - sw) / 2
        self.offset_y = (h - sh) / 2

    def resizeEvent(self, event):
        self._calc_transform()
        super().resizeEvent(event)

    def paintEvent(self, event):
        if self.pixmap is None or self.pixmap.isNull():
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)

        iw, ih = self.pixmap.width(), self.pixmap.height()
        self._calc_transform()
        sw, sh = iw * self.scale, ih * self.scale
        dest = QRectF(self.offset_x, self.offset_y, sw, sh)
        p.drawPixmap(dest, self.pixmap, QRectF(0, 0, iw, ih))

        # 바운딩박스 그리기
        font = QFont("Consolas", 10, QFont.Bold)
        p.setFont(font)
        fm = QFontMetrics(font)

        color_map = {}
        ci = 0
        for i, box in enumerate(self.boxes):
            if box.label not in color_map:
                color_map[box.label] = QColor(COLORS[ci % len(COLORS)])
                ci += 1

        for i, box in enumerate(self.boxes):
            x1 = self.offset_x + box.xmin * self.scale
            y1 = self.offset_y + box.ymin * self.scale
            x2 = self.offset_x + box.xmax * self.scale
            y2 = self.offset_y + box.ymax * self.scale
            bw, bh = x2 - x1, y2 - y1

            is_sel = (i == self.selected_index)
            color = QColor("#FFFFFF") if is_sel else color_map.get(box.label, QColor("#CCCCCC"))
            pen_w = 3 if is_sel else 2

            p.setPen(QPen(color, pen_w))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRectF(x1, y1, bw, bh))

            # 라벨 배경 + 텍스트
            label_text = box.label
            tw = fm.horizontalAdvance(label_text) + 6
            th = fm.height() + 2
            label_y = y1 - th if y1 - th > self.offset_y else y1
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(color))
            p.drawRect(QRectF(x1, label_y, tw, th))
            p.setPen(QColor("#000000") if is_sel else QColor("#FFFFFF"))
            p.drawText(QRectF(x1 + 3, label_y + 1, tw, th), Qt.AlignVCenter, label_text)

            # 박스 인덱스 (우하단)
            idx_text = str(i + 1)
            p.setPen(color)
            p.drawText(QRectF(x2 - 16, y2 - 14, 16, 14), Qt.AlignCenter, idx_text)

        # 작업 불가 배너
        if self.unmatched:
            banner_w = sw * 0.18
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(220, 50, 50, 160))
            p.drawRect(QRectF(self.offset_x, self.offset_y, banner_w, sh))
            p.setPen(QColor("#FFFFFF"))
            banner_font = QFont("Malgun Gothic", 14, QFont.Bold)
            p.setFont(banner_font)
            p.save()
            cx = self.offset_x + banner_w / 2
            cy = self.offset_y + sh / 2
            p.translate(cx, cy)
            p.rotate(-90)
            p.drawText(QRectF(-sh / 2, -20, sh, 40), Qt.AlignCenter, "작 업 불 가")
            p.restore()

        p.end()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton or self.pixmap is None or self.pixmap.isNull():
            return
        # 위젯 좌표 → 이미지 좌표
        mx = (event.x() - self.offset_x) / self.scale
        my = (event.y() - self.offset_y) / self.scale

        # 역순으로 검사 (위에 그려진 박스 우선)
        for i in range(len(self.boxes) - 1, -1, -1):
            b = self.boxes[i]
            if b.xmin <= mx <= b.xmax and b.ymin <= my <= b.ymax:
                self.selected_index = i
                self.update()
                self.boxSelected.emit(i)
                return

        self.selected_index = None
        self.update()
        self.boxDeselected.emit()


# ── LRU 이미지 캐시 ───────────────────────────────────────────
class ImageCache:
    def __init__(self, max_size=50):
        self._cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._max = max_size

    def get(self, path: str) -> QPixmap:
        if path in self._cache:
            self._cache.move_to_end(path)
            return self._cache[path]
        # QPixmap(path)는 한글 경로에서 실패할 수 있으므로 바이트로 로드
        pm = QPixmap()
        try:
            with open(path, "rb") as f:
                data = f.read()
            pm.loadFromData(QByteArray(data))
        except Exception:
            pm = QPixmap(path)  # fallback
        self._cache[path] = pm
        if len(self._cache) > self._max:
            self._cache.popitem(last=False)
        return pm

    def clear(self):
        self._cache.clear()


# ── 메인 윈도우 ────────────────────────────────────────────────
class BBoxLabelEditor(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("바운딩박스 라벨 편집기")
        self.setGeometry(100, 100, 1200, 800)
        self.setStyleSheet(STYLE)

        self.entries: list[ImageEntry] = []
        self.current_index = -1
        self.selected_box: int | None = None
        self.undo_stack: list[tuple] = []  # (entry_idx, box_idx, old_label, new_label)
        self.image_cache = ImageCache()
        self.edit_count = 0

        self._build_ui()
        self._setup_shortcuts()

    # ── UI 빌드 ──────────────────────────────────────────────
    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(8, 8, 8, 4)
        main_layout.setSpacing(6)

        # ── 상단: 폴더 선택 ──
        top_group = QGroupBox("폴더 선택")
        top_lay = QHBoxLayout(top_group)

        top_lay.addWidget(QLabel("이미지 폴더:"))
        self.img_folder_input = QLineEdit()
        self.img_folder_input.setReadOnly(True)
        top_lay.addWidget(self.img_folder_input, 3)
        btn_img = QPushButton("찾아보기")
        btn_img.clicked.connect(self._select_image_folder)
        top_lay.addWidget(btn_img)

        top_lay.addWidget(QLabel("XML 폴더 (선택):"))
        self.xml_folder_input = QLineEdit()
        self.xml_folder_input.setReadOnly(True)
        self.xml_folder_input.setPlaceholderText("이미지 폴더에 XML 없을 때만")
        top_lay.addWidget(self.xml_folder_input, 2)
        btn_xml = QPushButton("찾아보기")
        btn_xml.clicked.connect(self._select_xml_folder)
        top_lay.addWidget(btn_xml)

        btn_load = QPushButton("불러오기")
        btn_load.setObjectName("btn_apply")
        btn_load.clicked.connect(self._load_folder)
        top_lay.addWidget(btn_load)

        main_layout.addWidget(top_group)

        # ── 중앙: 스플리터 (목록 | 캔버스+편집) ──
        splitter = QSplitter(Qt.Horizontal)

        # 왼쪽 패널: 검색 + 목록
        left_widget = QWidget()
        left_lay = QVBoxLayout(left_widget)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(4)

        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("검색 (파일명)")
        self.search_input.textChanged.connect(self._filter_list)
        left_lay.addWidget(self.search_input)

        self.file_list = QListWidget()
        self.file_list.currentRowChanged.connect(self._on_list_selection)
        left_lay.addWidget(self.file_list)

        # 이미지 카운터
        self.counter_label = QLabel("0 / 0")
        self.counter_label.setAlignment(Qt.AlignCenter)
        left_lay.addWidget(self.counter_label)

        splitter.addWidget(left_widget)

        # 오른쪽 패널: 캔버스 + 편집
        right_widget = QWidget()
        right_lay = QVBoxLayout(right_widget)
        right_lay.setContentsMargins(0, 0, 0, 0)
        right_lay.setSpacing(4)

        self.canvas = ImageCanvas()
        self.canvas.boxSelected.connect(self._on_box_selected)
        self.canvas.boxDeselected.connect(self._on_box_deselected)
        right_lay.addWidget(self.canvas, 1)

        # 편집 패널
        edit_group = QGroupBox("라벨 편집")
        edit_lay = QHBoxLayout(edit_group)

        self.box_info_label = QLabel("박스를 클릭하여 선택하세요")
        edit_lay.addWidget(self.box_info_label, 1)

        edit_lay.addWidget(QLabel("새 라벨:"))
        self.label_input = QLineEdit()
        self.label_input.setMaxLength(1)
        self.label_input.setFixedWidth(50)
        self.label_input.setAlignment(Qt.AlignCenter)
        self.label_input.setEnabled(False)
        self.label_input.returnPressed.connect(self._apply_label)
        edit_lay.addWidget(self.label_input)

        self.btn_apply = QPushButton("적용")
        self.btn_apply.setObjectName("btn_apply")
        self.btn_apply.setEnabled(False)
        self.btn_apply.clicked.connect(self._apply_label)
        edit_lay.addWidget(self.btn_apply)

        self.btn_next_box = QPushButton("다음 박스 →")
        self.btn_next_box.setEnabled(False)
        self.btn_next_box.clicked.connect(self._next_box)
        edit_lay.addWidget(self.btn_next_box)

        right_lay.addWidget(edit_group)

        # VIN 문자 스트립
        self.vin_strip_widget = QWidget()
        self.vin_strip_layout = QHBoxLayout(self.vin_strip_widget)
        self.vin_strip_layout.setContentsMargins(4, 2, 4, 2)
        self.vin_strip_layout.setSpacing(2)
        self.vin_strip_buttons: list[QPushButton] = []
        right_lay.addWidget(self.vin_strip_widget)

        splitter.addWidget(right_widget)
        splitter.setSizes([250, 950])
        main_layout.addWidget(splitter, 1)

        # ── 하단 툴바 ──
        bottom_lay = QHBoxLayout()
        self.status_label = QLabel("")
        bottom_lay.addWidget(self.status_label, 1)

        btn_save = QPushButton("💾 저장 (Ctrl+S)")
        btn_save.setObjectName("btn_save")
        btn_save.clicked.connect(self._save_all)
        bottom_lay.addWidget(btn_save)

        btn_undo = QPushButton("↩ 되돌리기 (Ctrl+Z)")
        btn_undo.clicked.connect(self._undo)
        bottom_lay.addWidget(btn_undo)

        main_layout.addLayout(bottom_lay)

    def _setup_shortcuts(self):
        QShortcut(QKeySequence("Ctrl+S"), self, self._save_all)
        QShortcut(QKeySequence("Ctrl+Z"), self, self._undo)
        QShortcut(QKeySequence("A"), self, lambda: self._navigate(-1))
        QShortcut(QKeySequence("Left"), self, lambda: self._navigate(-1))
        QShortcut(QKeySequence("D"), self, lambda: self._navigate(1))
        QShortcut(QKeySequence("Right"), self, lambda: self._navigate(1))
        QShortcut(QKeySequence("Tab"), self, self._next_box)
        QShortcut(QKeySequence("Shift+Tab"), self, self._prev_box)
        QShortcut(QKeySequence("Escape"), self, self._deselect_box)

    # ── 폴더 선택 ────────────────────────────────────────────
    def _select_image_folder(self):
        path = QFileDialog.getExistingDirectory(self, "이미지 폴더 선택")
        if path:
            self.img_folder_input.setText(path)

    def _select_xml_folder(self):
        path = QFileDialog.getExistingDirectory(self, "XML 폴더 선택")
        if path:
            self.xml_folder_input.setText(path)

    # ── 데이터 로드 ──────────────────────────────────────────
    def _load_folder(self):
        img_folder = self.img_folder_input.text().strip()
        if not img_folder or not os.path.isdir(img_folder):
            QMessageBox.warning(self, "오류", "유효한 이미지 폴더를 선택하세요.")
            return

        xml_folder = self.xml_folder_input.text().strip()

        # 이미지 수집 (재귀)
        images = {}  # basename -> full_path
        img_exts = {".jpg", ".jpeg", ".png", ".bmp"}
        for root, _, files in os.walk(img_folder):
            for f in files:
                if Path(f).suffix.lower() in img_exts:
                    images[f] = os.path.join(root, f)

        if not images:
            QMessageBox.warning(self, "오류", "이미지 파일을 찾을 수 없습니다.")
            return

        # XML 수집
        xmls = {}  # basename(without ext) -> full_path
        xml_search_folder = xml_folder if xml_folder and os.path.isdir(xml_folder) else img_folder
        for root, _, files in os.walk(xml_search_folder):
            for f in files:
                if f.lower().endswith(".xml"):
                    base = os.path.splitext(f)[0]
                    xmls[base] = os.path.join(root, f)

        if not xmls and not xml_folder:
            # 혼합 모드에서 XML 없음 → XML 폴더 선택 유도
            reply = QMessageBox.question(
                self, "XML 없음",
                "이미지 폴더에서 XML 파일을 찾을 수 없습니다.\nXML 폴더를 별도로 선택하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._select_xml_folder()
                xml_folder = self.xml_folder_input.text().strip()
                if xml_folder and os.path.isdir(xml_folder):
                    for root, _, files in os.walk(xml_folder):
                        for f in files:
                            if f.lower().endswith(".xml"):
                                base = os.path.splitext(f)[0]
                                xmls[base] = os.path.join(root, f)

        # 매칭
        self.entries = []
        matched = 0
        for img_name, img_path in sorted(images.items()):
            base = os.path.splitext(img_name)[0]
            xml_path = xmls.get(base)
            entry = ImageEntry(filename=img_name, image_path=img_path, xml_path=xml_path)

            if xml_path:
                entry.boxes, entry.tree = self._parse_xml(xml_path)
                matched += 1

            self.entries.append(entry)

        unmatched = len(self.entries) - matched

        # 정렬: 매칭된 것 먼저, 미매칭은 뒤에
        self.entries.sort(key=lambda e: (e.xml_path is None, e.filename))

        self._populate_list()
        self.edit_count = 0
        self.undo_stack.clear()
        self.image_cache.clear()
        self._update_status(matched, unmatched)

        if self.entries:
            self.file_list.setCurrentRow(0)

    def _parse_xml(self, xml_path: str) -> tuple[list[BoxItem], ET.ElementTree]:
        tree = ET.parse(xml_path)
        root = tree.getroot()
        boxes = []
        for i, obj in enumerate(root.findall("object")):
            name_el = obj.find("name")
            bndbox = obj.find("bndbox")
            if name_el is None or bndbox is None:
                continue
            boxes.append(BoxItem(
                index=i,
                xmin=int(float(bndbox.findtext("xmin", "0"))),
                ymin=int(float(bndbox.findtext("ymin", "0"))),
                xmax=int(float(bndbox.findtext("xmax", "0"))),
                ymax=int(float(bndbox.findtext("ymax", "0"))),
                label=name_el.text or "",
            ))
        # x 좌표 순 정렬
        boxes.sort(key=lambda b: b.xmin)
        return boxes, tree

    def _populate_list(self):
        self.file_list.blockSignals(True)
        self.file_list.clear()
        for entry in self.entries:
            if entry.xml_path is None:
                text = f"[작업불가] {entry.filename}"
            elif entry.dirty:
                text = f"* {entry.filename}"
            else:
                text = entry.filename
            item = QListWidgetItem(text)
            if entry.xml_path is None:
                item.setForeground(QColor("#888888"))
            elif entry.dirty:
                item.setForeground(QColor("#4ecdc4"))
            self.file_list.addItem(item)
        self.file_list.blockSignals(False)

    def _filter_list(self, text: str):
        text = text.lower()
        for i in range(self.file_list.count()):
            item = self.file_list.item(i)
            item.setHidden(text not in self.entries[i].filename.lower())

    def _update_list_item(self, idx: int):
        """특정 인덱스의 목록 아이템 텍스트/색상 업데이트"""
        entry = self.entries[idx]
        item = self.file_list.item(idx)
        if not item:
            return
        if entry.xml_path is None:
            item.setText(f"[작업불가] {entry.filename}")
            item.setForeground(QColor("#888888"))
        elif entry.dirty:
            item.setText(f"* {entry.filename}")
            item.setForeground(QColor("#4ecdc4"))
        else:
            item.setText(entry.filename)
            item.setForeground(QColor("#e0e0e0"))

    def _update_status(self, matched=None, unmatched=None):
        if matched is None:
            matched = sum(1 for e in self.entries if e.xml_path is not None)
            unmatched = sum(1 for e in self.entries if e.xml_path is None)
        dirty = sum(1 for e in self.entries if e.dirty)
        self.status_label.setText(
            f"총 {len(self.entries)}개  |  매칭: {matched}  |  미매칭: {unmatched}  |  수정됨: {dirty}"
        )

    # ── 이미지 표시 ──────────────────────────────────────────
    def _on_list_selection(self, row: int):
        if row < 0 or row >= len(self.entries):
            return
        self.current_index = row
        entry = self.entries[row]

        pixmap = self.image_cache.get(entry.image_path)
        is_unmatched = entry.xml_path is None
        self.canvas.set_data(pixmap, entry.boxes, unmatched=is_unmatched)

        # 편집 패널 상태
        has_boxes = bool(entry.boxes) and not is_unmatched
        self.label_input.setEnabled(False)
        self.btn_apply.setEnabled(False)
        self.btn_next_box.setEnabled(has_boxes)
        self.selected_box = None

        if is_unmatched:
            self.box_info_label.setText("어노테이션 데이터 없음 (작업 불가)")
        elif not entry.boxes:
            self.box_info_label.setText("어노테이션 없음 (빈 XML)")
        else:
            self.box_info_label.setText("박스를 클릭하여 선택하세요")

        self._update_vin_strip(entry.boxes if not is_unmatched else [])
        self.counter_label.setText(f"{row + 1} / {len(self.entries)}")

    def _navigate(self, delta: int):
        if not self.entries:
            return
        # label_input에 포커스 있으면 A/D 무시
        if self.label_input.hasFocus():
            return
        new_idx = self.current_index + delta
        if 0 <= new_idx < len(self.entries):
            self.file_list.setCurrentRow(new_idx)

    # ── 박스 선택/편집 ───────────────────────────────────────
    def _on_box_selected(self, box_idx: int):
        if self.current_index < 0:
            return
        entry = self.entries[self.current_index]
        if box_idx >= len(entry.boxes):
            return
        self.selected_box = box_idx
        box = entry.boxes[box_idx]
        self.box_info_label.setText(
            f"박스 #{box_idx + 1}/{len(entry.boxes)}  |  현재 라벨: [{box.label}]  |  "
            f"위치: ({box.xmin},{box.ymin})-({box.xmax},{box.ymax})"
        )
        self.label_input.setEnabled(True)
        self.btn_apply.setEnabled(True)
        self.label_input.setText(box.label)
        self.label_input.selectAll()
        self.label_input.setFocus()
        self._highlight_vin_strip(box_idx)

    def _on_box_deselected(self):
        self._deselect_box()

    def _deselect_box(self):
        self.selected_box = None
        self.canvas.select_box(None)
        self.label_input.setEnabled(False)
        self.label_input.clear()
        self.btn_apply.setEnabled(False)
        if self.current_index >= 0 and self.current_index < len(self.entries):
            entry = self.entries[self.current_index]
            if entry.xml_path is None:
                self.box_info_label.setText("어노테이션 데이터 없음 (작업 불가)")
            else:
                self.box_info_label.setText("박스를 클릭하여 선택하세요")
        self._highlight_vin_strip(None)

    def _next_box(self):
        if self.current_index < 0:
            return
        entry = self.entries[self.current_index]
        if not entry.boxes:
            return
        if self.selected_box is None:
            new_idx = 0
        else:
            new_idx = (self.selected_box + 1) % len(entry.boxes)
        self.canvas.select_box(new_idx)
        self._on_box_selected(new_idx)

    def _prev_box(self):
        if self.current_index < 0:
            return
        entry = self.entries[self.current_index]
        if not entry.boxes:
            return
        if self.selected_box is None:
            new_idx = len(entry.boxes) - 1
        else:
            new_idx = (self.selected_box - 1) % len(entry.boxes)
        self.canvas.select_box(new_idx)
        self._on_box_selected(new_idx)

    def _apply_label(self):
        if self.selected_box is None or self.current_index < 0:
            return
        entry = self.entries[self.current_index]
        box = entry.boxes[self.selected_box]

        new_label = self.label_input.text().strip().upper()
        if not new_label:
            return
        if not re.match(r'^[A-Z0-9*]$', new_label):
            QMessageBox.warning(self, "입력 오류", "라벨은 A-Z, 0-9, * 중 하나여야 합니다.")
            return

        if new_label == box.label:
            self._next_box()
            return

        old_label = box.label
        box.label = new_label
        box.modified = True
        entry.dirty = True

        # XML DOM 업데이트
        if entry.tree:
            objects = entry.tree.getroot().findall("object")
            # boxes는 xmin 정렬이므로 원래 index로 찾기
            if box.index < len(objects):
                name_el = objects[box.index].find("name")
                if name_el is not None:
                    name_el.text = new_label

        self.undo_stack.append((self.current_index, self.selected_box, old_label, new_label))
        self.edit_count += 1

        # UI 업데이트
        self._update_list_item(self.current_index)
        self._update_status()
        self._update_vin_strip(entry.boxes)
        self.canvas.update()

        # 박스 정보 갱신
        self.box_info_label.setText(
            f"박스 #{self.selected_box + 1}/{len(entry.boxes)}  |  현재 라벨: [{new_label}]  |  "
            f"위치: ({box.xmin},{box.ymin})-({box.xmax},{box.ymax})"
        )

        # 자동으로 다음 박스
        self._next_box()

    # ── VIN 문자 스트립 ──────────────────────────────────────
    def _update_vin_strip(self, boxes: list[BoxItem]):
        # 기존 버튼 제거
        for btn in self.vin_strip_buttons:
            self.vin_strip_layout.removeWidget(btn)
            btn.deleteLater()
        self.vin_strip_buttons.clear()

        if not boxes:
            return

        for i, box in enumerate(boxes):
            btn = QPushButton(box.label)
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(
                "QPushButton { background: #16213e; color: #e0e0e0; border: 1px solid #444; "
                "border-radius: 3px; font-size: 13px; font-weight: bold; font-family: Consolas; }"
                "QPushButton:hover { background: #1a5276; border-color: #4ecdc4; }"
            )
            idx = i
            btn.clicked.connect(lambda checked, j=idx: self._on_vin_char_clicked(j))
            self.vin_strip_layout.addWidget(btn)
            self.vin_strip_buttons.append(btn)

        self.vin_strip_layout.addStretch()

    def _highlight_vin_strip(self, idx: int | None):
        for i, btn in enumerate(self.vin_strip_buttons):
            if i == idx:
                btn.setStyleSheet(
                    "QPushButton { background: #e94560; color: #fff; border: 2px solid #fff; "
                    "border-radius: 3px; font-size: 13px; font-weight: bold; font-family: Consolas; }"
                )
            else:
                btn.setStyleSheet(
                    "QPushButton { background: #16213e; color: #e0e0e0; border: 1px solid #444; "
                    "border-radius: 3px; font-size: 13px; font-weight: bold; font-family: Consolas; }"
                    "QPushButton:hover { background: #1a5276; border-color: #4ecdc4; }"
                )

    def _on_vin_char_clicked(self, idx: int):
        self.canvas.select_box(idx)
        self._on_box_selected(idx)

    # ── 되돌리기 ─────────────────────────────────────────────
    def _undo(self):
        if not self.undo_stack:
            return
        entry_idx, box_idx, old_label, new_label = self.undo_stack.pop()
        entry = self.entries[entry_idx]
        box = entry.boxes[box_idx]

        box.label = old_label
        if old_label == self._get_original_label(entry, box.index):
            box.modified = False

        # XML DOM 업데이트
        if entry.tree:
            objects = entry.tree.getroot().findall("object")
            if box.index < len(objects):
                name_el = objects[box.index].find("name")
                if name_el is not None:
                    name_el.text = old_label

        # dirty 체크: 수정된 박스가 하나라도 있는지
        entry.dirty = any(b.modified for b in entry.boxes)

        self._update_list_item(entry_idx)
        self._update_status()

        # 현재 보고 있는 이미지면 캔버스 갱신
        if entry_idx == self.current_index:
            self.canvas.update()
            self._update_vin_strip(entry.boxes)
            if self.selected_box == box_idx:
                self._on_box_selected(box_idx)

    def _get_original_label(self, entry: ImageEntry, obj_index: int) -> str:
        """원본 XML에서 라벨 다시 읽기 (undo 시 원본 비교용)"""
        if not entry.xml_path:
            return ""
        try:
            tree = ET.parse(entry.xml_path)
            objects = tree.getroot().findall("object")
            if obj_index < len(objects):
                name_el = objects[obj_index].find("name")
                return name_el.text if name_el is not None else ""
        except Exception:
            pass
        return ""

    # ── 저장 ─────────────────────────────────────────────────
    def _save_all(self):
        dirty_entries = [e for e in self.entries if e.dirty and e.tree and e.xml_path]
        if not dirty_entries:
            QMessageBox.information(self, "저장", "수정된 내용이 없습니다.")
            return

        saved = 0
        for entry in dirty_entries:
            try:
                # 백업
                bak_path = entry.xml_path + ".bak"
                if not os.path.exists(bak_path):
                    shutil.copy2(entry.xml_path, bak_path)

                # 저장
                entry.tree.write(
                    entry.xml_path,
                    xml_declaration=True,
                    encoding="utf-8",
                )
                entry.dirty = False
                for box in entry.boxes:
                    box.modified = False
                saved += 1
            except Exception as e:
                QMessageBox.warning(self, "저장 오류", f"{entry.filename}: {e}")

        self._populate_list()
        if self.current_index >= 0:
            self.file_list.setCurrentRow(self.current_index)
        self._update_status()
        QMessageBox.information(self, "저장 완료", f"{saved}개 XML 파일이 저장되었습니다.")

    # ── 종료 확인 ────────────────────────────────────────────
    def closeEvent(self, event):
        dirty_count = sum(1 for e in self.entries if e.dirty)
        if dirty_count > 0:
            reply = QMessageBox.question(
                self, "미저장 변경사항",
                f"수정된 파일 {dirty_count}개가 저장되지 않았습니다.\n저장하지 않고 종료하시겠습니까?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.No:
                event.ignore()
                return
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = BBoxLabelEditor()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
