"""Dört nokta prob ölçüm arayüzü — Keithley 2400 + Lucas Labs S302-6 + Signatone SP4.

Sol panel: KickStart I-V Characterizer'a benzer parametre grupları.
Sağ panel: canlı I-V grafiği, sonuçlar, veri tablosu ve olay günlüğü.
"""

from __future__ import annotations

import json
import math
import os
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QLocale, Qt, QSettings
from PySide6.QtGui import QDoubleValidator, QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
    QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QProgressBar, QPushButton, QScrollArea, QSpinBox, QSplitter,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from . import analysis
from .config import MeasurementConfig, SourceCfg, MeasureCfg, SampleCfg
from .export import write_csv
from .instrument import (
    BAUD_RATES, DEFAULT_GPIB_ADDRESS, InstrumentError, Keithley2400,
    SimulatedKeithley2400, connect_gpib, list_serial_ports,
    list_visa_resources,
)
from .worker import MeasurementWorker, build_levels

CURRENT_UNITS = [("nA", 1e-9), ("µA", 1e-6), ("mA", 1e-3), ("A", 1.0)]
VOLTAGE_UNITS = [("µV", 1e-6), ("mV", 1e-3), ("V", 1.0)]
CURRENT_RANGES = [("Oto", None), ("1 µA", 1e-6), ("10 µA", 1e-5), ("100 µA", 1e-4),
                  ("1 mA", 1e-3), ("10 mA", 1e-2), ("100 mA", 1e-1), ("1 A", 1.0)]
VOLTAGE_RANGES = [("Oto", None), ("200 mV", 0.2), ("2 V", 2.0),
                  ("20 V", 20.0), ("200 V", 200.0)]
THICKNESS_UNITS = [("nm", 1e-6), ("µm", 1e-3), ("mm", 1.0)]  # -> mm çarpanı
NPLC_PRESETS = [("Hızlı (0.1 PLC)", 0.1), ("Orta (1 PLC)", 1.0),
                ("Yavaş (10 PLC)", 10.0), ("Özel", None)]


class UnitValueEdit(QWidget):
    """Sayı + birim girişi (ör. 1.0 [mA])."""

    def __init__(self, units, default_value: float, default_unit: str, parent=None):
        super().__init__(parent)
        self.edit = QLineEdit(f"{default_value:g}")
        validator = QDoubleValidator()
        validator.setNotation(QDoubleValidator.Notation.ScientificNotation)
        validator.setLocale(QLocale.c())  # ondalık ayracı her zaman nokta
        self.edit.setValidator(validator)
        self.unit = QComboBox()
        self._set_units(units, default_unit)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.edit, 3)
        lay.addWidget(self.unit, 1)

    def _set_units(self, units, default_unit: str) -> None:
        self.unit.clear()
        for name, mult in units:
            self.unit.addItem(name, mult)
        idx = self.unit.findText(default_unit)
        if idx >= 0:
            self.unit.setCurrentIndex(idx)

    def set_units(self, units, default_unit: str) -> None:
        self._set_units(units, default_unit)

    def value(self) -> float:
        text = self.edit.text().replace(",", ".").strip()
        if not text:
            raise ValueError("Boş sayı alanı var")
        return float(text) * float(self.unit.currentData())

    def set_value(self, si_value: float) -> None:
        mult = float(self.unit.currentData())
        self.edit.setText(f"{si_value / mult:g}")

    def state(self) -> dict:
        return {"text": self.edit.text(), "unit": self.unit.currentText()}

    def restore(self, st: dict) -> None:
        if not st:
            return
        idx = self.unit.findText(st.get("unit", ""))
        if idx >= 0:
            self.unit.setCurrentIndex(idx)
        self.edit.setText(st.get("text", self.edit.text()))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("4PP Ölçüm — Keithley 2400 (Lucas Labs S302-6 / Signatone SP4)")
        self.resize(1280, 800)

        self.instrument = None
        self.worker: Optional[MeasurementWorker] = None
        self.rows: List[Tuple[float, float, float, bool]] = []
        self.last_fit = None
        self.last_sheet = None
        self.idn = ""

        self._build_ui()
        self._load_settings()
        self._on_function_changed()
        self._on_mode_changed()
        self._on_geometry_changed()
        self._on_conn_type_changed()
        self._refresh_ports()

    # ------------------------------------------------------------------ UI

    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Orientation.Horizontal)
        self.setCentralWidget(splitter)

        # --- sol: parametre panelleri ---
        left = QWidget()
        left_lay = QVBoxLayout(left)
        left_lay.setContentsMargins(6, 6, 6, 6)
        left_lay.addWidget(self._connection_group())
        left_lay.addWidget(self._source_group())
        left_lay.addWidget(self._measure_group())
        left_lay.addWidget(self._sample_group())
        left_lay.addWidget(self._save_group())
        left_lay.addStretch(1)

        scroll = QScrollArea()
        scroll.setWidget(left)
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(400)

        left_outer = QWidget()
        lo = QVBoxLayout(left_outer)
        lo.setContentsMargins(0, 0, 0, 0)
        lo.addWidget(scroll, 1)
        lo.addWidget(self._run_bar())

        # --- sağ: grafik + sonuçlar + tablo + log ---
        right_split = QSplitter(Qt.Orientation.Vertical)
        right_split.addWidget(self._plot_widget())
        right_split.addWidget(self._results_widget())
        right_split.addWidget(self._table_and_log())
        right_split.setStretchFactor(0, 5)
        right_split.setStretchFactor(1, 0)
        right_split.setStretchFactor(2, 3)

        splitter.addWidget(left_outer)
        splitter.addWidget(right_split)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    def _connection_group(self) -> QGroupBox:
        g = QGroupBox("Bağlantı")
        form = QFormLayout(g)
        self.conn_form = form

        self.conn_type_combo = QComboBox()
        self.conn_type_combo.addItem("RS-232 (USB-seri çevirici)", "serial")
        self.conn_type_combo.addItem("GPIB (USB-GPIB çevirici / VISA)", "gpib")
        self.conn_type_combo.currentIndexChanged.connect(self._on_conn_type_changed)
        form.addRow("Bağlantı türü:", self.conn_type_combo)

        self.port_combo = QComboBox()
        btn_refresh = QPushButton("Yenile")
        btn_refresh.clicked.connect(self._refresh_ports)
        row = QHBoxLayout()
        row.addWidget(self.port_combo, 1)
        row.addWidget(btn_refresh)
        self.port_row = self._wrap(row)
        form.addRow("Seri port:", self.port_row)

        self.visa_combo = QComboBox()
        self.visa_combo.setEditable(True)  # kaynak bulunamazsa elle yazılabilsin
        self.visa_combo.setToolTip(
            "Keithley 2400 fabrika ayarıyla GPIB0::24::INSTR olarak görünür. "
            "Liste boşsa NI-VISA / Keysight IO Libraries kurulumunu kontrol edin.")
        btn_visa_refresh = QPushButton("Yenile")
        btn_visa_refresh.clicked.connect(self._refresh_visa)
        row = QHBoxLayout()
        row.addWidget(self.visa_combo, 1)
        row.addWidget(btn_visa_refresh)
        self.visa_row = self._wrap(row)
        form.addRow("GPIB kaynağı:", self.visa_row)

        self.baud_combo = QComboBox()
        for b in sorted(BAUD_RATES):
            self.baud_combo.addItem(str(b), b)
        self.baud_combo.setCurrentText("9600")
        self.baud_combo.setToolTip("Keithley 2400 ön panel RS-232 ayarıyla aynı olmalı")
        form.addRow("Baud:", self.baud_combo)

        self.term_combo = QComboBox()
        for t in ("CRLF", "CR", "LF"):
            self.term_combo.addItem(t)
        self.term_combo.setToolTip("Cihazın TERMINATOR ayarı (varsayılan <CR+LF>)")
        form.addRow("Terminatör:", self.term_combo)

        self.flow_check = QCheckBox("XON/XOFF akış kontrolü")
        form.addRow("", self.flow_check)

        self.sim_check = QCheckBox("Simülasyon modu (cihaz olmadan dene)")
        self.sim_check.toggled.connect(self._on_sim_toggled)
        form.addRow("", self.sim_check)

        self.btn_connect = QPushButton("Bağlan")
        self.btn_connect.clicked.connect(self._toggle_connection)
        self.idn_label = QLabel("Bağlı değil")
        self.idn_label.setWordWrap(True)
        form.addRow(self.btn_connect, self.idn_label)
        return g

    def _source_group(self) -> QGroupBox:
        g = QGroupBox("Kaynak ayarları (Source)")
        self.src_form = QFormLayout(g)
        form = self.src_form

        self.func_combo = QComboBox()
        self.func_combo.addItem("Akım kaynağı, gerilim ölç (önerilen)", "current")
        self.func_combo.addItem("Gerilim kaynağı, akım ölç", "voltage")
        self.func_combo.currentIndexChanged.connect(self._on_function_changed)
        form.addRow("Fonksiyon:", self.func_combo)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Lineer tarama", "lin")
        self.mode_combo.addItem("Logaritmik tarama", "log")
        self.mode_combo.addItem("Liste taraması", "list")
        self.mode_combo.addItem("Sabit seviye (tek nokta)", "bias")
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        form.addRow("Mod:", self.mode_combo)

        self.start_edit = UnitValueEdit(CURRENT_UNITS, 0.1, "mA")
        form.addRow("Başlangıç:", self.start_edit)
        self.stop_edit = UnitValueEdit(CURRENT_UNITS, 1.0, "mA")
        form.addRow("Bitiş:", self.stop_edit)

        self.points_spin = QSpinBox()
        self.points_spin.setRange(2, 2500)
        self.points_spin.setValue(11)
        form.addRow("Nokta sayısı:", self.points_spin)

        self.list_edit = QLineEdit()
        self.list_edit.setPlaceholderText("örn: 1e-4, 2e-4, 5e-4, 1e-3  (SI birimi: A veya V)")
        form.addRow("Liste değerleri:", self.list_edit)

        self.bias_edit = UnitValueEdit(CURRENT_UNITS, 1.0, "mA")
        form.addRow("Sabit seviye:", self.bias_edit)
        self.bias_count_spin = QSpinBox()
        self.bias_count_spin.setRange(1, 1000)
        self.bias_count_spin.setValue(5)
        form.addRow("Okuma sayısı:", self.bias_count_spin)

        self.dual_check = QCheckBox("Çift yönlü tarama (ileri + geri)")
        form.addRow("", self.dual_check)

        self.reversal_check = QCheckBox("Akım ters çevirme (+I/−I, termal EMF eliminasyonu)")
        self.reversal_check.setChecked(True)
        self.reversal_check.setToolTip(
            "Her noktada +I ve −I ile ölçüm alınır, V = (V⁺ − V⁻)/2. "
            "Termal EMF ve ofset hatalarını giderir; ölçüm süresi 2 katına çıkar.")
        form.addRow("", self.reversal_check)

        self.src_range_combo = QComboBox()
        form.addRow("Kaynak aralığı:", self.src_range_combo)

        self.compliance_edit = UnitValueEdit(VOLTAGE_UNITS, 21.0, "V")
        self.compliance_label = QLabel("Compliance (V limiti):")
        self.compliance_edit.setToolTip(
            "Akım kaynağında cihazın uygulayacağı en yüksek gerilim. "
            "Numuneyi korumak için düşük tutun.")
        form.addRow(self.compliance_label, self.compliance_edit)

        self.delay_auto_check = QCheckBox("Oto")
        self.delay_spin = QDoubleSpinBox()
        self.delay_spin.setRange(0.0, 9999.0)
        self.delay_spin.setDecimals(3)
        self.delay_spin.setValue(0.1)
        self.delay_spin.setSuffix(" s")
        self.delay_auto_check.toggled.connect(self.delay_spin.setDisabled)
        row = QHBoxLayout()
        row.addWidget(self.delay_spin, 1)
        row.addWidget(self.delay_auto_check)
        form.addRow("Kaynak gecikmesi:", self._wrap(row))
        return g

    def _measure_group(self) -> QGroupBox:
        g = QGroupBox("Ölçüm ayarları (Measure)")
        form = QFormLayout(g)

        self.nplc_combo = QComboBox()
        for name, val in NPLC_PRESETS:
            self.nplc_combo.addItem(name, val)
        self.nplc_combo.setCurrentIndex(1)
        self.nplc_combo.currentIndexChanged.connect(self._on_nplc_changed)
        self.nplc_spin = QDoubleSpinBox()
        self.nplc_spin.setRange(0.01, 10.0)
        self.nplc_spin.setDecimals(2)
        self.nplc_spin.setValue(1.0)
        self.nplc_spin.setEnabled(False)
        row = QHBoxLayout()
        row.addWidget(self.nplc_combo, 2)
        row.addWidget(self.nplc_spin, 1)
        form.addRow("Entegrasyon (NPLC):", self._wrap(row))

        self.meas_range_combo = QComboBox()
        form.addRow("Ölçüm aralığı:", self.meas_range_combo)

        self.azero_check = QCheckBox("Auto zero açık")
        self.azero_check.setChecked(True)
        self.azero_check.setToolTip("Sıfır/kazanç sapmalarını her okumada düzeltir; ölçümü yavaşlatır.")
        form.addRow("", self.azero_check)

        self.filter_check = QCheckBox("Tekrar filtresi (ortalama)")
        self.filter_count_spin = QSpinBox()
        self.filter_count_spin.setRange(2, 100)
        self.filter_count_spin.setValue(10)
        self.filter_count_spin.setEnabled(False)
        self.filter_check.toggled.connect(self.filter_count_spin.setEnabled)
        row = QHBoxLayout()
        row.addWidget(self.filter_check, 2)
        row.addWidget(self.filter_count_spin, 1)
        form.addRow("", self._wrap(row))

        self.fourwire_check = QCheckBox("4 telli ölçüm (remote sense) — 4PP için gerekli")
        self.fourwire_check.setChecked(True)
        form.addRow("", self.fourwire_check)

        self.term_sel_combo = QComboBox()
        self.term_sel_combo.addItem("Ön panel", "front")
        self.term_sel_combo.addItem("Arka panel", "rear")
        form.addRow("Terminaller:", self.term_sel_combo)
        return g

    def _sample_group(self) -> QGroupBox:
        g = QGroupBox("Numune ve hesap")
        form = QFormLayout(g)
        self.sample_form = form

        self.sample_name_edit = QLineEdit()
        self.sample_name_edit.setPlaceholderText("örn: ITO-cam-05")
        form.addRow("Numune adı:", self.sample_name_edit)

        self.geometry_combo = QComboBox()
        self.geometry_combo.addItem("Sonsuz ince tabaka (C = 4.5324)", "infinite")
        self.geometry_combo.addItem("Dairesel numune (çap gir)", "circle")
        self.geometry_combo.addItem("Manuel düzeltme faktörü", "manual")
        self.geometry_combo.currentIndexChanged.connect(self._on_geometry_changed)
        form.addRow("Geometri:", self.geometry_combo)

        self.diameter_spin = QDoubleSpinBox()
        self.diameter_spin.setRange(0.1, 1000.0)
        self.diameter_spin.setValue(100.0)
        self.diameter_spin.setSuffix(" mm")
        form.addRow("Numune çapı:", self.diameter_spin)

        self.manual_factor_spin = QDoubleSpinBox()
        self.manual_factor_spin.setRange(0.001, 100.0)
        self.manual_factor_spin.setDecimals(4)
        self.manual_factor_spin.setValue(4.5324)
        self.manual_factor_spin.setToolTip(
            "Dikdörtgen/küçük numuneler için Smits (1958) tablolarından bakılan faktör")
        form.addRow("Düzeltme faktörü:", self.manual_factor_spin)

        self.spacing_spin = QDoubleSpinBox()
        self.spacing_spin.setRange(0.01, 25.0)
        self.spacing_spin.setDecimals(4)
        self.spacing_spin.setValue(1.016)
        self.spacing_spin.setSuffix(" mm")
        self.spacing_spin.setToolTip(
            "Signatone SP4 uç aralığı — kafanızın modeline bakın "
            "(40 mil = 1.016 mm, 62.5 mil = 1.5875 mm)")
        form.addRow("Uç aralığı (s):", self.spacing_spin)

        self.thickness_edit = UnitValueEdit(THICKNESS_UNITS, 0.0, "nm")
        self.thickness_edit.setToolTip("0 = bilinmiyor; girilirse özdirenç hesaplanır (ρ = Rs·t·K)")
        form.addRow("Film kalınlığı (t):", self.thickness_edit)

        self.thick_corr_check = QCheckBox("Kalınlık düzeltmesi uygula (t/s büyükse)")
        self.thick_corr_check.setChecked(True)
        form.addRow("", self.thick_corr_check)
        return g

    def _save_group(self) -> QGroupBox:
        g = QGroupBox("Veri kaydı")
        form = QFormLayout(g)

        self.autosave_check = QCheckBox("Ölçüm bitince CSV'yi otomatik kaydet")
        form.addRow("", self.autosave_check)

        self.folder_edit = QLineEdit(os.path.expanduser("~"))
        btn_browse = QPushButton("Seç…")
        btn_browse.clicked.connect(self._choose_folder)
        row = QHBoxLayout()
        row.addWidget(self.folder_edit, 1)
        row.addWidget(btn_browse)
        form.addRow("Klasör:", self._wrap(row))

        self.btn_save = QPushButton("CSV olarak kaydet")
        self.btn_save.clicked.connect(self._save_csv_dialog)
        self.btn_save.setEnabled(False)
        form.addRow("", self.btn_save)
        return g

    def _run_bar(self) -> QWidget:
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(6, 0, 6, 6)
        self.btn_start = QPushButton("▶ Ölçümü Başlat")
        self.btn_start.setStyleSheet("font-weight: bold; padding: 8px;")
        self.btn_start.clicked.connect(self._start_measurement)
        self.btn_stop = QPushButton("■ Durdur")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._stop_measurement)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        lay.addWidget(self.btn_start, 2)
        lay.addWidget(self.btn_stop, 1)
        lay.addWidget(self.progress, 2)
        return w

    def _plot_widget(self) -> QWidget:
        pg.setConfigOptions(antialias=True, background="w", foreground="k")
        self.plot = pg.PlotWidget()
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.scatter = self.plot.plot([], [], pen=None, symbol="o", symbolSize=7,
                                      symbolBrush=(30, 100, 200), name="Ölçüm")
        self.fit_line = self.plot.plot([], [], pen=pg.mkPen((200, 40, 40), width=2),
                                       name="Lineer fit")
        self.plot.addLegend(offset=(10, 10))
        return self.plot

    def _results_widget(self) -> QWidget:
        w = QGroupBox("Sonuçlar")
        lay = QHBoxLayout(w)
        font = QFont()
        font.setPointSize(11)
        font.setBold(True)
        self.res_r = QLabel("R: —")
        self.res_r2 = QLabel("R²: —")
        self.res_rs = QLabel("Rs: —")
        self.res_rho = QLabel("ρ: —")
        self.res_factor = QLabel("C: —")
        for lbl in (self.res_r, self.res_r2, self.res_rs, self.res_rho, self.res_factor):
            lbl.setFont(font)
            lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            lay.addWidget(lbl)
        return w

    def _table_and_log(self) -> QWidget:
        w = QSplitter(Qt.Orientation.Horizontal)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["Nokta", "Kaynak", "V (V)", "I (A)", "R = V/I (Ω)"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(2000)
        w.addWidget(self.table)
        w.addWidget(self.log)
        return w

    @staticmethod
    def _wrap(layout) -> QWidget:
        w = QWidget()
        w.setLayout(layout)
        return w

    # ------------------------------------------------------------- durum

    def _log(self, msg: str) -> None:
        self.log.appendPlainText(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")

    def _refresh_ports(self) -> None:
        current = self.port_combo.currentData()
        self.port_combo.clear()
        for dev, desc in list_serial_ports():
            label = f"{dev} — {desc}" if desc else dev
            self.port_combo.addItem(label, dev)
        if self.port_combo.count() == 0:
            self.port_combo.addItem("(port bulunamadı)", None)
        if current:
            idx = self.port_combo.findData(current)
            if idx >= 0:
                self.port_combo.setCurrentIndex(idx)

    def _refresh_visa(self) -> None:
        current = self.visa_combo.currentText()
        self.visa_combo.clear()
        resources = list_visa_resources()
        for r in resources:
            self.visa_combo.addItem(r)
        default = f"GPIB0::{DEFAULT_GPIB_ADDRESS}::INSTR"
        if not resources:
            self.visa_combo.addItem(default)
            self._log("VISA kaynağı bulunamadı — sürücü/VISA kurulumunu kontrol edin. "
                      "Adres elle de yazılabilir.")
        if current:
            idx = self.visa_combo.findText(current)
            self.visa_combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _on_conn_type_changed(self) -> None:
        is_serial = self.conn_type_combo.currentData() == "serial"
        form = self.conn_form
        form.setRowVisible(self.port_row, is_serial)
        form.setRowVisible(self.baud_combo, is_serial)
        form.setRowVisible(self.term_combo, is_serial)
        form.setRowVisible(self.flow_check, is_serial)
        form.setRowVisible(self.visa_row, not is_serial)
        if not is_serial and self.visa_combo.count() == 0:
            self._refresh_visa()

    def _on_sim_toggled(self, on: bool) -> None:
        for w in (self.conn_type_combo, self.port_combo, self.baud_combo,
                  self.term_combo, self.flow_check, self.visa_combo):
            w.setEnabled(not on)

    def _on_function_changed(self) -> None:
        is_current = self.func_combo.currentData() == "current"
        units = CURRENT_UNITS if is_current else VOLTAGE_UNITS
        default = "mA" if is_current else "V"
        for w in (self.start_edit, self.stop_edit, self.bias_edit):
            w.set_units(units, default)
        # kaynak aralıkları
        ranges = CURRENT_RANGES if is_current else VOLTAGE_RANGES
        self.src_range_combo.clear()
        for name, val in ranges:
            self.src_range_combo.addItem(name, val)
        # ölçüm aralıkları (kaynağın tersi)
        meas_ranges = VOLTAGE_RANGES if is_current else CURRENT_RANGES
        self.meas_range_combo.clear()
        for name, val in meas_ranges:
            self.meas_range_combo.addItem(name, val)
        # compliance birimi
        if is_current:
            self.compliance_label.setText("Compliance (V limiti):")
            self.compliance_edit.set_units(VOLTAGE_UNITS, "V")
            self.compliance_edit.edit.setText("21")
        else:
            self.compliance_label.setText("Compliance (A limiti):")
            self.compliance_edit.set_units(CURRENT_UNITS, "mA")
            self.compliance_edit.edit.setText("100")
        self.reversal_check.setEnabled(is_current)
        # eksen adları
        if is_current:
            self.plot.setLabel("bottom", "I (kaynak)", units="A")
            self.plot.setLabel("left", "V (ölçülen)", units="V")
        else:
            self.plot.setLabel("bottom", "V (kaynak)", units="V")
            self.plot.setLabel("left", "I (ölçülen)", units="A")

    def _on_mode_changed(self) -> None:
        mode = self.mode_combo.currentData()
        form = self.src_form
        form.setRowVisible(self.start_edit, mode in ("lin", "log"))
        form.setRowVisible(self.stop_edit, mode in ("lin", "log"))
        form.setRowVisible(self.points_spin, mode in ("lin", "log"))
        form.setRowVisible(self.list_edit, mode == "list")
        form.setRowVisible(self.bias_edit, mode == "bias")
        form.setRowVisible(self.bias_count_spin, mode == "bias")
        self.dual_check.setVisible(mode in ("lin", "log", "list"))

    def _on_geometry_changed(self) -> None:
        geo = self.geometry_combo.currentData()
        self.sample_form.setRowVisible(self.diameter_spin, geo == "circle")
        self.sample_form.setRowVisible(self.manual_factor_spin, geo == "manual")

    def _on_nplc_changed(self) -> None:
        val = self.nplc_combo.currentData()
        self.nplc_spin.setEnabled(val is None)
        if val is not None:
            self.nplc_spin.setValue(val)

    def _choose_folder(self) -> None:
        d = QFileDialog.getExistingDirectory(self, "Kayıt klasörü", self.folder_edit.text())
        if d:
            self.folder_edit.setText(d)

    # -------------------------------------------------------- bağlantı

    def _toggle_connection(self) -> None:
        if self.instrument is not None:
            self._disconnect()
            return
        try:
            if self.sim_check.isChecked():
                self.instrument = SimulatedKeithley2400()
            elif self.conn_type_combo.currentData() == "gpib":
                resource = self.visa_combo.currentText().strip()
                if not resource:
                    raise InstrumentError("GPIB kaynağı seçilmedi/yazılmadı "
                                          "(örn: GPIB0::24::INSTR).")
                self.instrument = connect_gpib(resource)
            else:
                port = self.port_combo.currentData()
                if not port:
                    raise InstrumentError("Seri port seçilmedi. USB-RS232 çeviricinin "
                                          "takılı ve sürücüsünün kurulu olduğundan emin olun.")
                self.instrument = Keithley2400(
                    port=port,
                    baud=int(self.baud_combo.currentData()),
                    terminator=self.term_combo.currentText(),
                    xonxoff=self.flow_check.isChecked(),
                )
            self.idn = self.instrument.open_session()
            self.idn_label.setText(self.idn)
            self.btn_connect.setText("Bağlantıyı Kes")
            self._log(f"Bağlandı: {self.idn}")
        except InstrumentError as e:
            if self.instrument is not None:
                self.instrument.close()
                self.instrument = None
            QMessageBox.critical(self, "Bağlantı hatası", str(e))
            self._log(f"Bağlantı hatası: {e}")

    def _disconnect(self) -> None:
        if self.worker and self.worker.isRunning():
            QMessageBox.warning(self, "Uyarı", "Önce ölçümü durdurun.")
            return
        if self.instrument:
            self.instrument.close()
        self.instrument = None
        self.idn = ""
        self.idn_label.setText("Bağlı değil")
        self.btn_connect.setText("Bağlan")
        self._log("Bağlantı kesildi")

    # ---------------------------------------------------------- ölçüm

    def _collect_config(self) -> MeasurementConfig:
        s = SourceCfg(
            function=self.func_combo.currentData(),
            mode=self.mode_combo.currentData(),
            start=self.start_edit.value() if self.mode_combo.currentData() in ("lin", "log") else 0.0,
            stop=self.stop_edit.value() if self.mode_combo.currentData() in ("lin", "log") else 0.0,
            points=self.points_spin.value(),
            bias_level=self.bias_edit.value() if self.mode_combo.currentData() == "bias" else 0.0,
            bias_readings=self.bias_count_spin.value(),
            dual=self.dual_check.isChecked() and self.dual_check.isVisible(),
            reversal=self.reversal_check.isChecked() and self.reversal_check.isEnabled(),
            range_auto=self.src_range_combo.currentData() is None,
            range_value=self.src_range_combo.currentData() or 0.0,
            compliance=self.compliance_edit.value(),
            delay_auto=self.delay_auto_check.isChecked(),
            delay=self.delay_spin.value(),
        )
        if s.mode == "list":
            text = self.list_edit.text().replace(";", ",")
            try:
                s.list_values = [float(x.strip()) for x in text.split(",") if x.strip()]
            except ValueError:
                raise ValueError("Liste değerleri okunamadı; virgülle ayrılmış, ondalık "
                                 "için nokta kullanılan sayılar girin (örn: 1e-4, 0.001)")
        m = MeasureCfg(
            nplc=self.nplc_spin.value(),
            range_auto=self.meas_range_combo.currentData() is None,
            range_value=self.meas_range_combo.currentData() or 0.0,
            auto_zero=self.azero_check.isChecked(),
            filter_on=self.filter_check.isChecked(),
            filter_count=self.filter_count_spin.value(),
            four_wire=self.fourwire_check.isChecked(),
            terminals=self.term_sel_combo.currentData(),
        )
        smp = SampleCfg(
            name=self.sample_name_edit.text().strip(),
            geometry=self.geometry_combo.currentData(),
            diameter_mm=self.diameter_spin.value(),
            spacing_mm=self.spacing_spin.value(),
            manual_factor=self.manual_factor_spin.value(),
            thickness_mm=self.thickness_edit.value(),
            thickness_corr=self.thick_corr_check.isChecked(),
        )
        return MeasurementConfig(source=s, measure=m, sample=smp)

    def _start_measurement(self) -> None:
        if self.worker and self.worker.isRunning():
            return
        try:
            cfg = self._collect_config()
            n_levels = len(build_levels(cfg))  # erken doğrulama
        except ValueError as e:
            QMessageBox.warning(self, "Geçersiz parametre", str(e))
            return

        if self.instrument is None:
            if self.sim_check.isChecked():
                self._toggle_connection()
            else:
                QMessageBox.warning(self, "Bağlantı yok",
                                    "Önce cihaza bağlanın veya simülasyon modunu seçin.")
                return
            if self.instrument is None:
                return

        if not self.fourwire_check.isChecked():
            self._log("UYARI: 4 telli ölçüm kapalı — 4PP için sonuçlar kontak "
                      "direncini içerecektir!")

        self.cfg = cfg
        self.rows = []
        self.last_fit = None
        self.last_sheet = None
        self.table.setRowCount(0)
        self.scatter.setData([], [])
        self.fit_line.setData([], [])
        for lbl, txt in ((self.res_r, "R: —"), (self.res_r2, "R²: —"),
                         (self.res_rs, "Rs: —"), (self.res_rho, "ρ: —"),
                         (self.res_factor, "C: —")):
            lbl.setText(txt)
        self.progress.setRange(0, n_levels)
        self.progress.setValue(0)

        self.worker = MeasurementWorker(self.instrument, cfg)
        self.worker.point_measured.connect(self._on_point)
        self.worker.finished_ok.connect(self._on_finished)
        self.worker.failed.connect(self._on_failed)
        self.worker.status.connect(self._log)
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.btn_save.setEnabled(False)
        self.worker.start()

    def _stop_measurement(self) -> None:
        if self.worker:
            self.worker.stop()
            self._log("Durdurma istendi; mevcut nokta bitince duracak...")

    def _on_point(self, idx: int, level: float, v: float, i: float, tripped: bool) -> None:
        self.rows.append((level, v, i, tripped))
        row = self.table.rowCount()
        self.table.insertRow(row)
        r = v / i if i != 0 else float("nan")
        for col, val in enumerate([str(idx + 1), f"{level:.4E}", f"{v:.6E}",
                                   f"{i:.6E}", f"{r:.6E}"]):
            item = QTableWidgetItem(val)
            if tripped:
                item.setForeground(Qt.GlobalColor.red)
            self.table.setItem(row, col, item)
        self.table.scrollToBottom()
        self.progress.setValue(len(self.rows))
        if tripped:
            self._log(f"UYARI: {idx + 1}. noktada compliance sınırına ulaşıldı!")
        # canlı grafik
        if self.cfg.source.function == "current":
            xs = [r_[2] for r_ in self.rows]  # ölçülen I
            ys = [r_[1] for r_ in self.rows]  # ölçülen V
        else:
            xs = [r_[1] for r_ in self.rows]
            ys = [r_[2] for r_ in self.rows]
        self.scatter.setData(xs, ys)

    def _on_finished(self, rows: list) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.worker = None
        if not rows:
            self._log("Hiç veri alınamadı.")
            return
        self.btn_save.setEnabled(True)
        self._log(f"Ölçüm tamamlandı: {len(rows)} nokta")
        self._analyze()
        if self.autosave_check.isChecked():
            name = self.sample_name_edit.text().strip() or "olcum"
            fname = f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            path = os.path.join(self.folder_edit.text(), fname)
            self._save_csv(path)

    def _on_failed(self, msg: str) -> None:
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.worker = None
        self.btn_save.setEnabled(bool(self.rows))
        self._log(f"HATA: {msg}")
        QMessageBox.critical(self, "Ölçüm hatası", msg)

    # ----------------------------------------------------------- analiz

    def _analyze(self) -> None:
        v = np.array([r[1] for r in self.rows])
        i = np.array([r[2] for r in self.rows])
        cfg = self.cfg
        try:
            if cfg.source.mode == "bias" or len(self.rows) < 2:
                i_mean, v_mean = float(np.mean(i)), float(np.mean(v))
                if i_mean == 0:
                    raise ValueError("Ortalama akım sıfır; direnç hesaplanamıyor")
                fit = analysis.FitResult(resistance=v_mean / i_mean, offset=0.0,
                                         r_squared=float("nan"), n_points=len(self.rows))
                if len(self.rows) > 1:
                    self._log(f"V ortalaması: {v_mean:.6E} V, "
                              f"std: {float(np.std(v, ddof=1)):.2E} V")
            else:
                fit = analysis.linear_fit(i, v)
                xs = np.linspace(float(np.min(i)), float(np.max(i)), 50)
                if cfg.source.function == "current":
                    self.fit_line.setData(xs, fit.resistance * xs + fit.offset)
                else:
                    self.fit_line.setData(fit.resistance * xs + fit.offset, xs)
            smp = cfg.sample
            sheet = analysis.compute_sheet(
                fit.resistance, smp.geometry, smp.diameter_mm, smp.spacing_mm,
                smp.manual_factor, smp.thickness_mm, smp.thickness_corr)
        except ValueError as e:
            self._log(f"Hesap hatası: {e}")
            QMessageBox.warning(self, "Hesap hatası", str(e))
            return

        self.last_fit, self.last_sheet = fit, sheet
        self.res_r.setText(f"R: {fit.resistance:.6G} Ω")
        self.res_r2.setText("R²: —" if math.isnan(fit.r_squared)
                            else f"R²: {fit.r_squared:.6f}")
        self.res_rs.setText(f"Rs: {sheet.sheet_resistance:.6G} Ω/sq")
        if sheet.resistivity_ohm_cm is not None:
            self.res_rho.setText(f"ρ: {sheet.resistivity_ohm_cm:.4G} Ω·cm")
        else:
            self.res_rho.setText("ρ: — (kalınlık girilmedi)")
        self.res_factor.setText(f"C: {sheet.correction_factor:.4f}")
        self._log(f"R = {fit.resistance:.6G} Ω  →  Rs = {sheet.sheet_resistance:.6G} Ω/sq "
                  f"(C = {sheet.correction_factor:.4f})")
        if not math.isnan(fit.r_squared) and fit.r_squared < 0.999:
            self._log("UYARI: R² < 0.999 — kontak sorunu, compliance ya da "
                      "ohmik olmayan davranış olabilir.")

    # ------------------------------------------------------------- kayıt

    def _save_csv_dialog(self) -> None:
        name = self.sample_name_edit.text().strip() or "olcum"
        default = os.path.join(self.folder_edit.text(),
                               f"{name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv")
        path, _ = QFileDialog.getSaveFileName(self, "CSV kaydet", default,
                                              "CSV dosyası (*.csv)")
        if path:
            self._save_csv(path)

    def _save_csv(self, path: str) -> None:
        try:
            write_csv(path, self.rows, self.cfg, self.last_fit, self.last_sheet, self.idn)
            self._log(f"Kaydedildi: {path}")
        except OSError as e:
            QMessageBox.critical(self, "Kayıt hatası", str(e))

    # ------------------------------------------------------- ayar kalıcılığı

    _PERSIST = "ui_state_v1"

    def _settings(self) -> QSettings:
        return QSettings("4pp_measurement", "FourPointProbe")

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(5000)
        if self.instrument:
            self.instrument.close()
        state = {
            "conn_type": self.conn_type_combo.currentData(),
            "visa_resource": self.visa_combo.currentText(),
            "baud": self.baud_combo.currentText(),
            "term": self.term_combo.currentText(),
            "flow": self.flow_check.isChecked(),
            "sim": self.sim_check.isChecked(),
            "function": self.func_combo.currentData(),
            "mode": self.mode_combo.currentData(),
            "start": self.start_edit.state(),
            "stop": self.stop_edit.state(),
            "points": self.points_spin.value(),
            "list": self.list_edit.text(),
            "bias": self.bias_edit.state(),
            "bias_count": self.bias_count_spin.value(),
            "dual": self.dual_check.isChecked(),
            "reversal": self.reversal_check.isChecked(),
            "src_range": self.src_range_combo.currentText(),
            "compliance": self.compliance_edit.state(),
            "delay_auto": self.delay_auto_check.isChecked(),
            "delay": self.delay_spin.value(),
            "nplc_preset": self.nplc_combo.currentIndex(),
            "nplc": self.nplc_spin.value(),
            "meas_range": self.meas_range_combo.currentText(),
            "azero": self.azero_check.isChecked(),
            "filter": self.filter_check.isChecked(),
            "filter_count": self.filter_count_spin.value(),
            "fourwire": self.fourwire_check.isChecked(),
            "terminals": self.term_sel_combo.currentData(),
            "sample": self.sample_name_edit.text(),
            "geometry": self.geometry_combo.currentData(),
            "diameter": self.diameter_spin.value(),
            "manual_factor": self.manual_factor_spin.value(),
            "spacing": self.spacing_spin.value(),
            "thickness": self.thickness_edit.state(),
            "thick_corr": self.thick_corr_check.isChecked(),
            "autosave": self.autosave_check.isChecked(),
            "folder": self.folder_edit.text(),
        }
        self._settings().setValue(self._PERSIST, json.dumps(state))
        super().closeEvent(event)

    def _load_settings(self) -> None:
        raw = self._settings().value(self._PERSIST)
        if not raw:
            return
        try:
            st = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return

        def combo_by_data(combo: QComboBox, data) -> None:
            idx = combo.findData(data)
            if idx >= 0:
                combo.setCurrentIndex(idx)

        def combo_by_text(combo: QComboBox, text) -> None:
            idx = combo.findText(str(text))
            if idx >= 0:
                combo.setCurrentIndex(idx)

        combo_by_data(self.conn_type_combo, st.get("conn_type", "serial"))
        if st.get("visa_resource"):
            self.visa_combo.addItem(st["visa_resource"])
            self.visa_combo.setCurrentText(st["visa_resource"])
        combo_by_text(self.baud_combo, st.get("baud", "9600"))
        combo_by_text(self.term_combo, st.get("term", "CRLF"))
        self.flow_check.setChecked(st.get("flow", False))
        self.sim_check.setChecked(st.get("sim", False))
        combo_by_data(self.func_combo, st.get("function", "current"))
        self._on_function_changed()
        combo_by_data(self.mode_combo, st.get("mode", "lin"))
        self.start_edit.restore(st.get("start", {}))
        self.stop_edit.restore(st.get("stop", {}))
        self.points_spin.setValue(st.get("points", 11))
        self.list_edit.setText(st.get("list", ""))
        self.bias_edit.restore(st.get("bias", {}))
        self.bias_count_spin.setValue(st.get("bias_count", 5))
        self.dual_check.setChecked(st.get("dual", False))
        self.reversal_check.setChecked(st.get("reversal", True))
        combo_by_text(self.src_range_combo, st.get("src_range", "Oto"))
        self.compliance_edit.restore(st.get("compliance", {}))
        self.delay_auto_check.setChecked(st.get("delay_auto", False))
        self.delay_spin.setValue(st.get("delay", 0.1))
        self.nplc_combo.setCurrentIndex(st.get("nplc_preset", 1))
        self.nplc_spin.setValue(st.get("nplc", 1.0))
        combo_by_text(self.meas_range_combo, st.get("meas_range", "Oto"))
        self.azero_check.setChecked(st.get("azero", True))
        self.filter_check.setChecked(st.get("filter", False))
        self.filter_count_spin.setValue(st.get("filter_count", 10))
        self.fourwire_check.setChecked(st.get("fourwire", True))
        combo_by_data(self.term_sel_combo, st.get("terminals", "front"))
        self.sample_name_edit.setText(st.get("sample", ""))
        combo_by_data(self.geometry_combo, st.get("geometry", "infinite"))
        self.diameter_spin.setValue(st.get("diameter", 100.0))
        self.manual_factor_spin.setValue(st.get("manual_factor", 4.5324))
        self.spacing_spin.setValue(st.get("spacing", 1.016))
        self.thickness_edit.restore(st.get("thickness", {}))
        self.thick_corr_check.setChecked(st.get("thick_corr", True))
        self.autosave_check.setChecked(st.get("autosave", False))
        self.folder_edit.setText(st.get("folder", os.path.expanduser("~")))


def main() -> int:
    import sys
    app = QApplication(sys.argv)
    app.setApplicationName("4PP Ölçüm")
    win = MainWindow()
    win.show()
    return app.exec()
