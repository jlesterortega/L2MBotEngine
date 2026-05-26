import sys
import os
import json
import logging
import threading
import ctypes
from pathlib import Path
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QComboBox, QPushButton, QSpinBox, QCheckBox, QTextEdit,
    QFrame, QGridLayout, QScrollArea, QSizePolicy, QToolTip
)
from PyQt6.QtGui import QTextCursor, QColor

from audio_listener import AudioListener, get_audio_devices, get_audio_processes, HPMonitorThread
from input_simulator import press_key, click_at, press_combo

CONFIG_FILENAME = "l2m_config.json"
logger = logging.getLogger("L2MBot")

def get_config_path() -> Path:
    return Path(__file__).parent / CONFIG_FILENAME

class CoordinatePickerOverlay(QWidget):
    coordinates_picked = pyqtSignal(int, int)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | 
            Qt.WindowType.WindowStaysOnTopHint | 
            Qt.WindowType.SubWindow
        )
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setWindowOpacity(0.01)  
        self.setCursor(Qt.CursorShape.CrossCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.globalPosition()
            self.coordinates_picked.emit(int(pos.x()), int(pos.y()))
            self.close()


class MacroExecutionWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, steps_data, focus_callback, auto_record, record_duration, max_loops, stop_condition_check):
        super().__init__()
        self.steps_data = steps_data
        self.focus_callback = focus_callback
        self.auto_record = auto_record
        self.record_duration = record_duration # Make sure this is assigned
        self.max_loops = max_loops
        self.stop_condition_check = stop_condition_check

    def run(self):
        try:
            self.log_signal.emit("🎯 Target event triggered. Coordinating window focus...")
            if self.focus_callback:
                self.focus_callback()
            
            post_focus_delay = self.steps_data['post_init_delay']
            if post_focus_delay > 0:
                self.log_signal.emit(f"⏳ Holding actions for {post_focus_delay}ms pre-sequence delay...")
                QThread.msleep(post_focus_delay)

            if self.auto_record:
                self.log_signal.emit(f"🎥 Triggering Auto-Record clip (Ctrl+Shift+V) for {self.record_duration}s...")
                press_combo("ctrl+shift", "v")
                
                def stop_recording_timer():
                    QThread.msleep(self.record_duration * 1000)
                    self.log_signal.emit("🎥 Stopping Auto-Record clip (Ctrl+Shift+V)...")
                    press_combo("ctrl+shift", "v")
                
                threading.Thread(target=stop_recording_timer, daemon=True).start()
                QThread.msleep(100)

            loop_count = 0
            while loop_count < self.max_loops:
                if self.stop_condition_check():
                    self.log_signal.emit("✓ Combat threat resolved. Breaking out of loops early.")
                    break

                loop_count += 1
                self.log_signal.emit(f"🔄 Processing Hit-Back Skill Matrix — Loop ({loop_count}/{self.max_loops})")

                for i in range(3):
                    press_combo("ctrl", "space")
                    if i < 2:  # 200ms delay between the 3 presses
                        QThread.msleep(200)
                # --- END OF MODIFIED ATTACK SEQUENCE ---

                for i, step in enumerate(self.steps_data['steps']):
                    if self.stop_condition_check():
                        break
                    if not step['enabled'] or step['key'] == "none":
                        continue
                    press_key(step['key'].lower(), times=1)
                    if step['delay'] > 0:
                        QThread.msleep(step['delay'])

                QThread.msleep(50)

            self.log_signal.emit("⚔️ Closing Action: Pressing 'F' (Auto-Hunt Re-engagement)")
            press_key("f", times=1)
            self.log_signal.emit("✓ Action macro loop sequence complete.")
        except Exception as e:
            self.log_signal.emit(f"⚠️ Error executing macro sequence loop: {e}")
        finally:
            self.finished_signal.emit()


class L2MBotUI(QMainWindow):
    parent_signal = pyqtSignal()
    hp_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("L2M Bot — Lineage2M Guard Engine")
        self.setMinimumSize(540, 940)
        
        self.is_listening = False
        self.listener = None
        self.hp_monitor_worker = None
        self.macro_worker = None
        self.macro_lock = threading.Lock()
        self._is_currently_under_attack = False

        self.focus_click_x = 0
        self.focus_click_y = 0
        self.hp_check_x = 0
        self.hp_check_y = 0
        self._flash_state = False

        self.init_ui()
        self.parent_signal.connect(self.execute_trigger_sequence)
        self.hp_signal.connect(self.execute_hp_escape_sequence)
        self.load_configuration()
        
        QTimer.singleShot(100, self.refresh_targets)
        self.append_log("🤖 L2M Bot Engine Interface Loaded successfully.")

    def init_ui(self):
        self.setStyleSheet("""
            QMainWindow { background-color: #0d0d12; }
            QWidget { font-family: 'Segoe UI', 'Arial'; color: #e2e2e9; font-size: 13px; }
            QFrame#Card { background-color: #161622; border: 1px solid #252538; border-radius: 8px; }
            QLabel#HeaderTitle { font-size: 22px; font-weight: bold; color: #9a7fff; }
            QLabel#SectionTitle { font-size: 11px; font-weight: bold; color: #6e6e82; letter-spacing: 1px; }
            QComboBox { background-color: #222232; border: 1px solid #32324d; border-radius: 4px; padding: 4px 8px; color: #e2e2e9; }
            QComboBox::drop-down { border: 0px; }
            QSpinBox { background-color: #222232; border: 1px solid #32324d; border-radius: 4px; padding: 4px; padding-right: 20px; color: #34d399; font-weight: bold; }
            QSpinBox::up-button { subcontrol-origin: border; subcontrol-position: top right; width: 18px; background-color: #2a2a3d; border-left: 1px solid #32324d; border-bottom: 1px solid #1a1a26; border-top-right-radius: 4px; }
            QSpinBox::up-button:hover { background-color: #7c5cfc; }
            QSpinBox::up-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-bottom: 5px solid #e2e2e9; width: 0; height: 0; }
            QSpinBox::down-button { subcontrol-origin: border; subcontrol-position: bottom right; width: 18px; background-color: #2a2a3d; border-left: 1px solid #32324d; border-top: 1px solid #1a1a26; border-bottom-right-radius: 4px; }
            QSpinBox::down-button:hover { background-color: #7c5cfc; }
            QSpinBox::down-arrow { image: none; border-left: 4px solid transparent; border-right: 4px solid transparent; border-top: 5px solid #e2e2e9; width: 0; height: 0; }
            QCheckBox { spacing: 8px; }
            QCheckBox::indicator { width: 18px; height: 18px; border-radius: 4px; border: 1px solid #32324d; background-color: #222232; }
            QCheckBox::indicator:checked { background-color: #7c5cfc; border: 1px solid #9a7fff; }
            QTextEdit { background-color: #09090d; border: 1px solid #1f1f2e; border-radius: 6px; font-family: 'Cascadia Code', 'Consolas'; color: #34d399; font-size: 12px; }
            QPushButton#PrimaryAction { background-color: #7c5cfc; color: #ffffff; font-weight: bold; font-size: 14px; border-radius: 6px; padding: 12px; border: none; }
            QPushButton#PrimaryAction:hover { background-color: #9a7fff; }
            QPushButton#SecondaryAction { background-color: #242436; color: #e2e2e9; border: 1px solid #383854; border-radius: 4px; padding: 5px 12px; }
            QPushButton#SecondaryAction:hover { background-color: #2e2e45; border-color: #7c5cfc; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(14)

        header_layout = QHBoxLayout()
        title_lbl = QLabel("L2M Bot Engine")
        title_lbl.setObjectName("HeaderTitle")
        header_layout.addWidget(title_lbl)
        
        self.status_alert_banner = QLabel("")
        self.status_alert_banner.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_alert_banner.setStyleSheet("font-size: 14px; font-weight: bold; color: #ef4444; padding: 4px 12px;")
        header_layout.addWidget(self.status_alert_banner, 1)
        
        self.btn_test_trigger = QPushButton("🎯 TEST TRIGGER")
        self.btn_test_trigger.setObjectName("SecondaryAction")
        self.btn_test_trigger.clicked.connect(self.execute_trigger_sequence)
        header_layout.addWidget(self.btn_test_trigger)
        main_layout.addLayout(header_layout)

        self.flash_timer = QTimer()
        self.flash_timer.timeout.connect(self._handle_alert_flash_cycle)

        # Audio Stream Configuration Card
        src_card = QFrame()
        src_card.setObjectName("Card")
        src_layout = QVBoxLayout(src_card)
        src_layout.setContentsMargins(12, 12, 12, 12)
        
        lbl_sec1 = QLabel("AUDIO CAPTURE STREAM ENGINE")
        lbl_sec1.setObjectName("SectionTitle")
        src_layout.addWidget(lbl_sec1)

        mode_layout = QHBoxLayout()
        self.cb_audio_source = QComboBox()
        self.cb_audio_source.addItems(["System Audio (Global Loopback)", "App Only Capture (Process Specific)"])
        self.cb_audio_source.currentIndexChanged.connect(self.on_audio_source_changed)
        mode_layout.addWidget(self.cb_audio_source, 2)

        self.btn_refresh = QPushButton("🔄 Refresh Targets")
        self.btn_refresh.setObjectName("SecondaryAction")
        self.btn_refresh.clicked.connect(self.refresh_targets)
        mode_layout.addWidget(self.btn_refresh, 1)
        src_layout.addLayout(mode_layout)

        self.target_dropdowns_layout = QGridLayout()
        src_layout.addLayout(self.target_dropdowns_layout)
        
        self.lbl_device = QLabel("Active Sound Endpoint:")
        self.combo_devices = QComboBox()
        self.lbl_process = QLabel("Target App Instance:")
        self.combo_processes = QComboBox()
        
        self.target_dropdowns_layout.addWidget(self.lbl_device, 0, 0)
        self.target_dropdowns_layout.addWidget(self.combo_devices, 0, 1)
        self.target_dropdowns_layout.addWidget(self.lbl_process, 1, 0)
        self.target_dropdowns_layout.addWidget(self.combo_processes, 1, 1)

        # Window Click Targeting
        self.lbl_coord_title = QLabel("Focus Window Click Position:")
        self.lbl_coord_title.setStyleSheet("font-weight: bold;")
        self.lbl_coord_info = QLabel(" ⓘ ")
        self.lbl_coord_info.setStyleSheet("color: #9a7fff; font-weight: bold; font-size: 14px;")
        self.lbl_coord_info.setToolTip("Forces a mouse click on these coordinates inside the game client to regain window focus.")
        self.lbl_coord_values = QLabel("Not Set (Uses window center defaults)")
        self.lbl_coord_values.setStyleSheet("color: #6e6e82; font-family: 'Cascadia Code'; font-size: 12px;")

        self.btn_pick_coords = QPushButton("🎯 Pick Position")
        self.btn_pick_coords.setObjectName("SecondaryAction")
        self.btn_pick_coords.clicked.connect(self.start_coordinate_picking_sequence)
        self.btn_clear_coords = QPushButton("✕")
        self.btn_clear_coords.setObjectName("SecondaryAction")
        self.btn_clear_coords.setStyleSheet("color: #f87171; max-width: 30px;")
        self.btn_clear_coords.clicked.connect(self.clear_custom_coordinates)

        coord_actions_container = QWidget()
        coord_actions_layout = QHBoxLayout(coord_actions_container)
        coord_actions_layout.setContentsMargins(0, 0, 0, 0)
        coord_actions_layout.addWidget(self.lbl_coord_values, 2)
        coord_actions_layout.addWidget(self.btn_pick_coords, 1)
        coord_actions_layout.addWidget(self.btn_clear_coords, 0)

        coord_title_container = QWidget()
        coord_title_layout = QHBoxLayout(coord_title_container)
        coord_title_layout.setContentsMargins(0, 0, 0, 0)
        coord_title_layout.addWidget(self.lbl_coord_title)
        coord_title_layout.addWidget(self.lbl_coord_info)
        coord_title_layout.addStretch()

        self.target_dropdowns_layout.addWidget(coord_title_container, 2, 0)
        self.target_dropdowns_layout.addWidget(coord_actions_container, 2, 1)

        # HP bar tracking line
        self.lbl_hp_title = QLabel("HP Threshold Target Position:")
        self.lbl_hp_title.setStyleSheet("font-weight: bold;")
        self.lbl_hp_info = QLabel(" ⓘ ")
        self.lbl_hp_info.setStyleSheet("color: #ef4444; font-weight: bold; font-size: 14px;")
        self.lbl_hp_info.setToolTip("Click directly on your health bar percentage mark. Fires emergency escape instantly if color drops.")
        self.lbl_hp_values = QLabel("HP Scanner Disabled (Unset)")
        self.lbl_hp_values.setStyleSheet("color: #6e6e82; font-family: 'Cascadia Code'; font-size: 12px;")

        self.btn_pick_hp = QPushButton("🩸 Pick Position")
        self.btn_pick_hp.setObjectName("SecondaryAction")
        self.btn_pick_hp.clicked.connect(self.start_hp_picking_sequence)
        self.btn_clear_hp = QPushButton("✕")
        self.btn_clear_hp.setObjectName("SecondaryAction")
        self.btn_clear_hp.setStyleSheet("color: #f87171; max-width: 30px;")
        self.btn_clear_hp.clicked.connect(self.clear_hp_coordinates)

        hp_actions_container = QWidget()
        hp_actions_layout = QHBoxLayout(hp_actions_container)
        hp_actions_layout.setContentsMargins(0, 0, 0, 0)
        hp_actions_layout.addWidget(self.lbl_hp_values, 2)
        hp_actions_layout.addWidget(self.btn_pick_hp, 1)
        hp_actions_layout.addWidget(self.btn_clear_hp, 0)

        hp_title_container = QWidget()
        hp_title_layout = QHBoxLayout(hp_title_container)
        hp_title_layout.setContentsMargins(0, 0, 0, 0)
        hp_title_layout.addWidget(self.lbl_hp_title)
        hp_title_layout.addWidget(self.lbl_hp_info)
        hp_title_layout.addStretch()

        self.target_dropdowns_layout.addWidget(hp_title_container, 3, 0)
        self.target_dropdowns_layout.addWidget(hp_actions_container, 3, 1)

        # ── MODIFIED: AUTO HP MONITOR LAYOUT WITH RANGED TELEPORT LISTS ──
        self.lbl_hp_key_title = QLabel("Auto HP monitor:")
        self.lbl_hp_key_title.setStyleSheet("font-weight: bold; color: #ef4444;")
        
        hp_settings_container = QWidget()
        hp_settings_layout = QHBoxLayout(hp_settings_container)
        hp_settings_layout.setContentsMargins(0, 0, 0, 0)
        
        self.chk_hp_enabled = QCheckBox("Enable Module")
        self.chk_hp_enabled.setChecked(False)
        
        self.lbl_hp_key_drop = QLabel("Teleport Key:")
        self.combo_hp_key = QComboBox()
        
        # Trimmed vocabulary payload list down to 0-9 matching requested limits
        available_hp_keys = ["none", "0", "1", "2", "3", "4", "5", "6", "7", "8", "9"]
        self.combo_hp_key.addItems(available_hp_keys)
        
        hp_settings_layout.addWidget(self.chk_hp_enabled)
        hp_settings_layout.addSpacing(10)
        hp_settings_layout.addWidget(self.lbl_hp_key_drop)
        hp_settings_layout.addWidget(self.combo_hp_key)
        hp_settings_layout.addStretch()
        
        self.target_dropdowns_layout.addWidget(self.lbl_hp_key_title, 4, 0)
        self.target_dropdowns_layout.addWidget(hp_settings_container, 4, 1)
        
        main_layout.addWidget(src_card)

        # Hit Back Steps Card Setup
        macro_card = QFrame()
        macro_card.setObjectName("Card")
        macro_layout = QVBoxLayout(macro_card)
        macro_layout.setContentsMargins(12, 12, 12, 12)
        
        lbl_sec2 = QLabel("HIT BACK SEQUENCE STEP MATRIX SETUP")
        lbl_sec2.setObjectName("SectionTitle")
        macro_layout.addWidget(lbl_sec2)

        r0 = QHBoxLayout()
        lbl_fx1 = QLabel("⚡ Post-Focus Pre-Action Delay:")
        lbl_delay1 = QLabel("Delay (ms):")
        self.spin_init_delay = QSpinBox()
        self.spin_init_delay.setRange(0, 5000)
        self.spin_init_delay.setSingleStep(50)
        r0.addWidget(lbl_fx1)
        r0.addStretch()
        r0.addWidget(lbl_delay1)
        r0.addWidget(self.spin_init_delay)
        macro_layout.addLayout(r0)

        r_loop = QHBoxLayout()
        lbl_loop_title = QLabel("🔄 Maximum Action Sequence Loops:")
        lbl_loop_desc = QLabel("Max Cycles:")
        self.spin_max_loops = QSpinBox()
        self.spin_max_loops.setRange(1, 100)
        self.spin_max_loops.setValue(1)
        r_loop.addWidget(lbl_loop_title)
        r_loop.addStretch()
        r_loop.addWidget(lbl_loop_desc)
        r_loop.addWidget(self.spin_max_loops)
        macro_layout.addLayout(r_loop)

        r1 = QHBoxLayout()
        lbl_step1_title = QLabel("Step 1 (Fixed Opening Base):")
        lbl_step1_val = QLabel("Ctrl + Space")
        lbl_step1_val.setStyleSheet("color: #7c5cfc; font-weight: bold;")
        r1.addWidget(lbl_step1_title)
        r1.addWidget(lbl_step1_val)
        r1.addStretch()
        macro_layout.addLayout(r1)

        self.macro_steps = []
        standard_keys = ["none", "1", "2", "3", "4", "5", "Q", "E", "R", "T", "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10", "F11", "F12"]
        for idx in range(5):
            r_step = QHBoxLayout()
            chk_en = QCheckBox(f"Step {idx+2}")
            chk_en.setChecked(True)
            
            combo_k = QComboBox()
            combo_k.addItems(standard_keys)
            
            lbl_d = QLabel("Delay (ms):")
            spin_d = QSpinBox()
            spin_d.setRange(0, 9999)
            spin_d.setSingleStep(100)
            
            r_step.addWidget(chk_en, 1)
            r_step.addWidget(combo_k, 2)
            r_step.addStretch()
            r_step.addWidget(lbl_d)
            r_step.addWidget(spin_d, 2)
            
            macro_layout.addLayout(r_step)
            self.macro_steps.append({"enabled": chk_en, "key": combo_k, "delay": spin_d})

        r_end = QHBoxLayout()
        lbl_fx2 = QLabel("🔒 Final Sequence Ending Anchor:")
        lbl_val2 = QLabel("F Key (Auto-Hunt Lock)")
        lbl_val2.setStyleSheet("color: #34d399; font-weight: bold;")
        r_end.addWidget(lbl_fx2)
        r_end.addWidget(lbl_val2)
        r_end.addStretch()
        macro_layout.addLayout(r_end)
        main_layout.addWidget(macro_card)

        extra_layout = QHBoxLayout()
        self.chk_auto_record = QCheckBox("Auto-Record Clip (Ctrl+Shift+V)")
        self.chk_power_saver = QCheckBox("Power Saver Optimization Mode")

        lbl_rec_dur = QLabel("Duration (sec):")
        self.spin_rec_duration = QSpinBox()
        self.spin_rec_duration.setRange(5, 300)
        self.spin_rec_duration.setValue(20) # Default 20 seconds

        extra_layout = QHBoxLayout()
        extra_layout.addWidget(self.chk_auto_record)
        extra_layout.addWidget(lbl_rec_dur)
        extra_layout.addWidget(self.spin_rec_duration)
        extra_layout.addWidget(self.chk_power_saver) # Now this will work
        main_layout.addLayout(extra_layout)

        self.btn_listen_toggle = QPushButton("▶ START LISTENING")
        self.btn_listen_toggle.setObjectName("PrimaryAction")
        self.btn_listen_toggle.clicked.connect(self.toggle_engine_listening_state)
        main_layout.addWidget(self.btn_listen_toggle)

        lbl_sec3 = QLabel("SYSTEM BROADCAST DIAGNOSTICS CONTROL LOGS")
        lbl_sec3.setObjectName("SectionTitle")
        main_layout.addWidget(lbl_sec3)

        self.log_terminal = QTextEdit()
        self.log_terminal.setReadOnly(True)
        main_layout.addWidget(self.log_terminal)

    def on_audio_source_changed(self, index):
        if index == 0:
            self.combo_devices.setEnabled(True)
            self.combo_processes.setEnabled(False)
        else:
            self.combo_devices.setEnabled(False)
            self.combo_processes.setEnabled(True)

    def start_coordinate_picking_sequence(self):
        self.hide()
        QThread.msleep(250)
        self.picker_overlay = CoordinatePickerOverlay()
        self.picker_overlay.coordinates_picked.connect(self.on_coordinates_captured_callback)
        self.picker_overlay.show()

    def on_coordinates_captured_callback(self, x, y):
        self.focus_click_x = x
        self.focus_click_y = y
        self.lbl_coord_values.setText(f"X: {x} , Y: {y}")
        self.lbl_coord_values.setStyleSheet("color: #34d399; font-family: 'Cascadia Code'; font-size: 12px; font-weight: bold;")
        self.append_log(f"🎯 Focus Click position saved: ({x}, {y})")
        self.show()
        self.save_configuration()

    def clear_custom_coordinates(self):
        self.focus_click_x = 0
        self.focus_click_y = 0
        self.lbl_coord_values.setText("Not Set (Uses window center defaults)")
        self.lbl_coord_values.setStyleSheet("color: #6e6e82; font-family: 'Segoe UI'; font-size: 13px;")
        self.append_log("✕ Custom target coordinates cleared.")
        self.show()
        self.save_configuration()

    def start_hp_picking_sequence(self):
        self.hide()
        QThread.msleep(250)
        self.hp_overlay = CoordinatePickerOverlay()
        self.hp_overlay.coordinates_picked.connect(self.on_hp_coordinates_captured)
        self.hp_overlay.show()

    def on_hp_coordinates_captured(self, x, y):
        self.hp_check_x = x
        self.hp_check_y = y
        self.lbl_hp_values.setText(f"X: {x} , Y: {y}")
        self.lbl_hp_values.setStyleSheet("color: #ef4444; font-family: 'Cascadia Code'; font-size: 12px; font-weight: bold;")
        self.append_log(f"🩸 HP Fallback matrix position targeted: ({x}, {y})")
        self.show()
        self.save_configuration()

    def clear_hp_coordinates(self):
        self.hp_check_x = 0
        self.hp_check_y = 0
        self.lbl_hp_values.setText("HP Scanner Disabled (Unset)")
        self.lbl_hp_values.setStyleSheet("color: #6e6e82; font-family: 'Segoe UI'; font-size: 13px;")
        self.append_log("✕ HP Fallback detection scanner unlinked.")
        self.show()
        self.save_configuration()

    def refresh_targets(self):
        devices = get_audio_devices()
        self.combo_devices.clear()
        for d in devices:
            self.combo_devices.addItem(d["name"], d)

        processes = get_audio_processes()
        self.combo_processes.clear()
        for p in processes:
            self.combo_processes.addItem(p["display"], p)

        self.append_log(f"🔄 Scanned assets: Found {len(devices)} endpoints, client processes updated.")

    def append_log(self, text: str):
        from datetime import datetime
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_terminal.append(f"[{timestamp}] {text}")
        self.log_terminal.moveCursor(QTextCursor.MoveOperation.End)

    def _handle_alert_flash_cycle(self):
        if self._flash_state:
            self.status_alert_banner.setText("⚠️ CHARACTER UNDER ATTACK!")
            self.status_alert_banner.setStyleSheet("font-size: 14px; font-weight: bold; color: #ffffff;")
        else:
            self.status_alert_banner.setText("⚠️ CHARACTER UNDER ATTACK!")
            self.status_alert_banner.setStyleSheet("font-size: 14px; font-weight: bold; color: #ef4444;")
        self._flash_state = not self._flash_state

    def _on_macro_worker_finished(self):
        self.flash_timer.stop()
        self.status_alert_banner.setText("")
        self.macro_lock.release()

    def focus_target_window(self):
        current_proc = self.combo_processes.currentData()
        if not current_proc:
            return
            
        target_pid = current_proc.get("pid", 0)
        if target_pid == 0:
            return

        try:
            import win32gui, win32process, win32con
            def cb(hwnd, extra):
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == target_pid and win32gui.IsWindowVisible(hwnd):
                    win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                    win32gui.SetForegroundWindow(hwnd)
                    QThread.msleep(80)
                    
                    if self.focus_click_x > 0 and self.focus_click_y > 0:
                        click_at(self.focus_click_x, self.focus_click_y)
                        self.append_log(f"🎯 Dispatched native macro focus click at: ({self.focus_click_x}, {self.focus_click_y})")
                    else:
                        rect = win32gui.GetWindowRect(hwnd)
                        left, top, right, bottom = rect
                        title_bar_height = ctypes.windll.user32.GetSystemMetrics(win32con.SM_CYCAPTION)
                        center_x = left + ((right - left) // 2)
                        center_y = top + title_bar_height + 50  
                        click_at(center_x, center_y)
                    return False
                return True
            win32gui.EnumWindows(cb, None)
        except Exception as e:
            logger.error(f"Failed to focus window: {e}")

    def _check_if_combat_cleared(self):
        return not self._is_currently_under_attack

    def gather_macro_payload(self) -> dict:
        payload = {
            "post_init_delay": self.spin_init_delay.value(),
            "steps": []
        }
        for step in self.macro_steps:
            payload["steps"].append({
                "enabled": step["enabled"].isChecked(),
                "key": step["key"].currentText(),
                "delay": step["delay"].value()
            })
        return payload

    def execute_hp_escape_sequence(self):
        if not self.macro_lock.acquire(blocking=False):
            return
        try:
            self.append_log("🚨 EMERGENCY LOW-HP TRIGGERED! Running standalone action rules...")
            self.focus_target_window()
            QThread.msleep(100)
            
            escape_key = self.combo_hp_key.currentText()
            if escape_key != "none":
                self.append_log(f"⚔️ Dispatching dynamic Teleport key string: [ {escape_key} ]")
                press_key(escape_key.lower(), times=1)
            else:
                self.append_log("⚠️ Escape key set to 'none'. Action skipped.")
            QThread.msleep(100)
        except Exception as e:
            self.append_log(f"❌ Error during dedicated HP emergency breakout: {e}")
        finally:
            self.macro_lock.release()

    def execute_trigger_sequence(self):
        self._is_currently_under_attack = True

        if self.macro_worker and self.macro_worker.isRunning():
            return

        if not self.macro_lock.acquire(blocking=False):
            return

        try:
            self._flash_state = True
            self._handle_alert_flash_cycle()
            self.flash_timer.start(250)

            payload = self.gather_macro_payload()
            auto_rec = self.chk_auto_record.isChecked()
            max_loops = self.spin_max_loops.value()
            
            self.macro_worker = MacroExecutionWorker(
                payload, 
                self.focus_target_window, 
                auto_rec, 
                self.spin_rec_duration.value(), # Pass the value here
                max_loops, 
                self._check_if_combat_cleared
            )
            self.macro_worker.log_signal.connect(self.append_log)
            self.macro_worker.finished_signal.connect(self._on_macro_worker_finished)
            
            self.macro_worker.start()
        except Exception as e:
            self.append_log(f"❌ Failed to initialize macro thread loop: {e}")
            self._on_macro_worker_finished()

    def _on_audio_silence_timeout(self):
        self._is_currently_under_attack = False

    def toggle_engine_listening_state(self):
        if self.is_listening: self.stop_engine()
        else: self.start_engine()

    def start_engine(self):
        triggers = ["your character is under attack"]
        if not self.chk_power_saver.isChecked():
            triggers.append("an enemy is near")

        self._combat_watchdog_timer = QTimer()
        self._combat_watchdog_timer.setSingleShot(True)
        self._combat_watchdog_timer.timeout.connect(self._on_audio_silence_timeout)

        def _pushed_trigger_callback():
            self._combat_watchdog_timer.start(4000)
            self.parent_signal.emit()

        source_mode = "system" if self.cb_audio_source.currentIndex() == 0 else "app"
        
        if source_mode == "app":
            proc_data = self.combo_processes.currentData()
            if not proc_data:
                self.append_log("❌ Error: No running app instances chosen.")
                return
            self.append_log(f"Starting proctap framework on account client: {proc_data['display']}")
            self.listener = AudioListener(
                on_trigger=lambda phrase: _pushed_trigger_callback(),
                on_status=self.append_log,
                triggers=triggers,
                process_pid=proc_data["pid"],
                process_name=proc_data["name"]
            )
        else:
            dev_data = self.combo_devices.currentData()
            if not dev_data or not isinstance(dev_data, dict):
                dev_idx, dev_rate, dev_channels = None, 16000, 1
            else:
                dev_idx = dev_data.get("index", None)
                dev_rate = dev_data.get("rate", 16000)
                dev_channels = dev_data.get("channels", 1)
                if dev_channels < 1: dev_channels = 1

            self.append_log(f"Starting tracking framework on hardware loopback: {dev_data.get('name', 'Default Line')}")
            self.listener = AudioListener(
                on_trigger=lambda phrase: _pushed_trigger_callback(),
                on_status=self.append_log,
                triggers=triggers,
                device_index=dev_idx,
                device_rate=dev_rate,
                device_channels=dev_channels
            )

        try:
            self.listener.start()
            
            if self.chk_hp_enabled.isChecked() and self.hp_check_x > 0 and self.hp_check_y > 0:
                self.hp_monitor_worker = HPMonitorThread(
                    self.hp_check_x, self.hp_check_y,
                    lambda phrase: self.hp_signal.emit(),
                    self.append_log
                )
                self.hp_monitor_worker.start()

            self.is_listening = True
            self.btn_listen_toggle.setText("⏹ STOP LISTENING")
            self.btn_listen_toggle.setStyleSheet("background-color: #f87171; color: white; font-weight: bold; font-size: 14px; border-radius: 6px; padding: 12px;")
            self.set_controls_lock(False)
        except Exception as e:
            self.append_log(f"❌ Critical error initiating stream: {e}")
            self.stop_engine()

    def stop_engine(self):
        if hasattr(self, '_combat_watchdog_timer'): self._combat_watchdog_timer.stop()
        if self.hp_monitor_worker:
            self.hp_monitor_worker.stop()
            self.hp_monitor_worker = None
        if self.listener:
            self.listener.stop()
            self.listener = None
        self.is_listening = False
        self._is_currently_under_attack = False
        self.btn_listen_toggle.setText("▶ START LISTENING")
        self.btn_listen_toggle.setStyleSheet("")
        self.set_controls_lock(True)
        self.append_log("⏹ Surveillance engine idle. System disarmed.")

    def set_controls_lock(self, state: bool):
        self.cb_audio_source.setEnabled(state)
        self.combo_devices.setEnabled(state if self.cb_audio_source.currentIndex() == 0 else False)
        self.combo_processes.setEnabled(state if self.cb_audio_source.currentIndex() == 1 else False)
        self.btn_refresh.setEnabled(state)
        self.chk_power_saver.setEnabled(state)
        self.btn_pick_coords.setEnabled(state)
        self.btn_clear_coords.setEnabled(state)
        self.btn_pick_hp.setEnabled(state)
        self.btn_clear_hp.setEnabled(state)
        self.chk_hp_enabled.setEnabled(state)
        self.combo_hp_key.setEnabled(state)
        self.spin_max_loops.setEnabled(state)

    def save_configuration(self):
        config = {
            "post_init_delay": self.spin_init_delay.value(),
            "max_loops": self.spin_max_loops.value(),
            "auto_record": self.chk_auto_record.isChecked(),
            "record_duration": self.spin_rec_duration.value(),
            "power_saver": self.chk_power_saver.isChecked(),
            "audio_source_idx": self.cb_audio_source.currentIndex(),
            "focus_click_x": self.focus_click_x,
            "focus_click_y": self.focus_click_y,
            "hp_check_x": self.hp_check_x,
            "hp_check_y": self.hp_check_y,
            "hp_enabled": self.chk_hp_enabled.isChecked(),
            "hp_key": self.combo_hp_key.currentText(),
            "steps": []
        }
        for step in self.macro_steps:
            config["steps"].append({
                "enabled": step["enabled"].isChecked(),
                "key": step["key"].currentText(),
                "delay": step["delay"].value()
            })
        try:
            get_config_path().write_text(json.dumps(config, indent=2), encoding="utf-8")
        except Exception as e:
            logger.error(f"Failed to record state configuration: {e}")

    def load_configuration(self):
        path = get_config_path()
        if not path.exists(): return
        try:
            config = json.loads(path.read_text(encoding="utf-8"))
            self.spin_init_delay.setValue(config.get("post_init_delay", 200))
            self.spin_max_loops.setValue(config.get("max_loops", 1))
            self.chk_auto_record.setChecked(config.get("auto_record", False))
            self.spin_rec_duration.setValue(config.get("record_duration", 20))
            self.chk_power_saver.setChecked(config.get("power_saver", False))
            self.cb_audio_source.setCurrentIndex(config.get("audio_source_idx", 0))
            
            self.focus_click_x = config.get("focus_click_x", 0)
            self.focus_click_y = config.get("focus_click_y", 0)
            if self.focus_click_x > 0 and self.focus_click_y > 0:
                self.lbl_coord_values.setText(f"X: {self.focus_click_x} , Y: {self.focus_click_y}")
                self.lbl_coord_values.setStyleSheet("color: #34d399; font-family: 'Cascadia Code'; font-size: 12px; font-weight: bold;")

            self.hp_check_x = config.get("hp_check_x", 0)
            self.hp_check_y = config.get("hp_check_y", 0)
            if self.hp_check_x > 0 and self.hp_check_y > 0:
                self.lbl_hp_values.setText(f"X: {self.hp_check_x} , Y: {self.hp_check_y}")
                self.lbl_hp_values.setStyleSheet("color: #ef4444; font-family: 'Cascadia Code'; font-size: 12px; font-weight: bold;")

            self.chk_hp_enabled.setChecked(config.get("hp_enabled", False))
            self.combo_hp_key.setCurrentText(config.get("hp_key", "none"))

            saved_steps = config.get("steps", [])
            for idx, step_data in enumerate(saved_steps):
                if idx < len(self.macro_steps):
                    self.macro_steps[idx]["enabled"].setChecked(step_data.get("enabled", True))
                    self.macro_steps[idx]["key"].setCurrentText(step_data.get("key", "none"))
                    self.macro_steps[idx]["delay"].setValue(step_data.get("delay", 100))
            self.on_audio_source_changed(self.cb_audio_source.currentIndex())
        except Exception as e:
            logger.error(f"Failed loading configuration profile: {e}")

    def closeEvent(self, event):
        self.stop_engine()
        self.save_configuration()
        event.accept()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)
    gui = L2MBotUI()
    gui.show()
    sys.exit(app.exec())