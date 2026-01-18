import sys
import asyncio
import time
import os
import shutil
from pathlib import Path
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QTextEdit, QPushButton, QComboBox, 
                             QLabel, QSlider, QFileDialog, QFrame)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
import edge_tts
import pygame

# --- CROSS-PLATFORM PATH RESOLVER (Fixed Path Bug) ---
def get_temp_path(filename):
    """Creates a persistent temp path in a hidden home folder (Cross-platform)"""
    home_dir = Path.home() / ".edge_tts_downloader"
    home_dir.mkdir(exist_ok=True)
    return str(home_dir / filename)

TEMP_FILE = get_temp_path("preview_cache.mp3")

# --- TTS GENERATION WORKER ---
class TTSWorker(QThread):
    finished = pyqtSignal(str)
    error = pyqtSignal(str)

    def __init__(self, text, voice, rate, pitch):
        super().__init__()
        self.text = text
        self.voice = voice
        self.rate = f"{rate:+d}%"
        self.pitch = f"{pitch:+d}Hz"

    def run(self):
        try:
            async def generate():
                communicate = edge_tts.Communicate(f" . . {self.text}", self.voice, rate=self.rate, pitch=self.pitch)
                await communicate.save(TEMP_FILE)
            asyncio.run(generate())
            self.finished.emit(TEMP_FILE)
        except Exception as e:
            self.error.emit(str(e))

# --- MAIN APPLICATION ---
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Edge TTS Downloader")
        self.setMinimumSize(1000, 800)
        
        self.is_playing = False
        self.duration_ms = 0
        self.current_pos_ms = 0 
        
        pygame.mixer.init()

        self.setStyleSheet("""
            QMainWindow { background-color: #0d1117; }
            QLabel { color: #8b949e; font-weight: 600; font-size: 12px; text-transform: uppercase; }
            QTextEdit { background-color: #161b22; color: #e6edf3; border: 1px solid #30363d; border-radius: 12px; padding: 15px; font-size: 15px; }
            QComboBox { background-color: #21262d; color: white; border: 1px solid #30363d; border-radius: 8px; padding: 10px; }
            QPushButton#primaryBtn { background-color: #238636; color: white; border-radius: 10px; font-weight: bold; padding: 15px; font-size: 14px; }
            QPushButton#primaryBtn:hover { background-color: #2ea043; }
            QPushButton#controlBtn { background-color: #21262d; color: #c9d1d9; border: 1px solid #30363d; border-radius: 8px; padding: 12px; font-weight: bold; }
            QFrame#card { background-color: #161b22; border: 1px solid #30363d; border-radius: 15px; }
            QSlider::groove:horizontal { height: 6px; background: #30363d; border-radius: 3px; }
            QSlider::handle:horizontal { background: #58a6ff; width: 18px; height: 18px; margin: -6px 0; border-radius: 9px; }
        """)

        self.central_widget = QWidget()
        self.setCentralWidget(self.central_widget)
        self.layout = QVBoxLayout(self.central_widget)
        self.layout.setContentsMargins(40, 40, 40, 40)
        self.layout.setSpacing(20)

        # 1. Voice Row
        config_layout = QHBoxLayout()
        self.region_combo = QComboBox()
        self.voice_combo = QComboBox()
        config_layout.addWidget(self.region_combo, 1)
        config_layout.addWidget(self.voice_combo, 2)
        self.layout.addLayout(config_layout)

        # 2. Text Input
        self.layout.addWidget(QLabel("Script Content"))
        self.text_input = QTextEdit()
        self.text_input.setPlaceholderText("Enter text here...")
        self.layout.addWidget(self.text_input)

        # 3. Parameters
        self.param_card = QFrame(); self.param_card.setObjectName("card")
        p_layout = QHBoxLayout(self.param_card)
        self.speed_slider = self.create_slider(p_layout, "Speed", -50, 100, 0, "%")
        self.pitch_slider = self.create_slider(p_layout, "Pitch", -25, 25, 0, "Hz")
        self.vol_slider = self.create_slider(p_layout, "Volume", 0, 100, 100, "%")
        self.layout.addWidget(self.param_card)

        # 4. Generate
        self.gen_btn = QPushButton("✨ GENERATE SPEECH")
        self.gen_btn.setObjectName("primaryBtn")
        self.gen_btn.clicked.connect(self.start_tts)
        self.layout.addWidget(self.gen_btn)

        # 5. Player (Initially Hidden)
        self.player_card = QFrame(); self.player_card.setObjectName("card"); self.player_card.setVisible(False)
        play_layout = QVBoxLayout(self.player_card)
        
        seek_row = QHBoxLayout()
        self.cur_time = QLabel("00:00"); self.cur_time.setStyleSheet("color: #58a6ff; font-family: monospace;")
        self.seek_bar = QSlider(Qt.Orientation.Horizontal)
        self.tot_time = QLabel("00:00"); self.tot_time.setStyleSheet("font-family: monospace;")
        seek_row.addWidget(self.cur_time); seek_row.addWidget(self.seek_bar); seek_row.addWidget(self.tot_time)
        play_layout.addLayout(seek_row)

        btn_row = QHBoxLayout()
        self.play_btn = QPushButton("⏸ PAUSE"); self.play_btn.setObjectName("controlBtn")
        self.play_btn.clicked.connect(self.toggle_play)
        self.save_btn = QPushButton("💾 DOWNLOAD MP3"); self.save_btn.setObjectName("controlBtn")
        self.save_btn.clicked.connect(self.save_as)
        btn_row.addWidget(self.play_btn); btn_row.addStretch(); btn_row.addWidget(self.save_btn)
        play_layout.addLayout(btn_row)
        self.layout.addWidget(self.player_card)

        # Logic Setup
        self.clock_timer = QTimer()
        self.clock_timer.setInterval(50) 
        self.clock_timer.timeout.connect(self.engine_tick)
        
        self.seek_bar.sliderPressed.connect(self.on_seek_start)
        self.seek_bar.sliderReleased.connect(self.on_seek_end)
        self.seek_bar.valueChanged.connect(self.update_labels_only)
        
        self.vol_slider.valueChanged.connect(lambda v: pygame.mixer.music.set_volume(v/100.0))
        self.load_voices()

    def create_slider(self, layout, name, min_v, max_v, def_v, unit):
        box = QVBoxLayout(); lbl = QLabel(f"{name}: {def_v}{unit}")
        slider = QSlider(Qt.Orientation.Horizontal); slider.setRange(min_v, max_v); slider.setValue(def_v)
        slider.valueChanged.connect(lambda v: lbl.setText(f"{name}: {v}{unit}"))
        box.addWidget(lbl); box.addWidget(slider); layout.addLayout(box)
        return slider

    def load_voices(self):
        async def fetch(): v = await edge_tts.VoicesManager.create(); return v.voices
        self.voices = asyncio.run(fetch())
        locales = sorted(list(set([v['Locale'] for v in self.voices])))
        self.region_combo.addItems(locales); self.region_combo.setCurrentText("en-US")
        self.region_combo.currentTextChanged.connect(self.update_voices); self.update_voices("en-US")

    def update_voices(self, locale):
        self.voice_combo.clear()
        self.voice_combo.addItems([v['ShortName'] for v in self.voices if v['Locale'] == locale])

    def start_tts(self):
        txt = self.text_input.toPlainText().strip()
        if not txt: return
        
        # --- FIX: GHOST TIMER & AUDIO RESET ---
        pygame.mixer.music.stop()
        self.clock_timer.stop()
        self.is_playing = False
        
        self.gen_btn.setEnabled(False)
        self.gen_btn.setText("⏳ PROCESSING...")
        self.worker = TTSWorker(txt, self.voice_combo.currentText(), self.speed_slider.value(), self.pitch_slider.value())
        self.worker.finished.connect(self.on_gen_done); self.worker.start()

    def on_gen_done(self, file_path):
        self.gen_btn.setEnabled(True)
        self.gen_btn.setText("✨ GENERATE SPEECH")
        self.player_card.setVisible(True)
        
        sound = pygame.mixer.Sound(file_path)
        self.duration_ms = int(sound.get_length() * 1000)
        self.seek_bar.setRange(0, self.duration_ms)
        self.tot_time.setText(time.strftime('%M:%S', time.gmtime(self.duration_ms / 1000)))
        
        self.current_pos_ms = 0
        self.seek_to_position(0, should_play=True)

    def seek_to_position(self, ms, should_play):
        pygame.mixer.music.stop()
        pygame.mixer.music.load(TEMP_FILE)
        pygame.mixer.music.set_volume(self.vol_slider.value() / 100.0)
        pygame.mixer.music.play(start=ms/1000.0)
        
        self.current_pos_ms = ms
        self.is_playing = should_play
        self.play_btn.setText("⏸ PAUSE" if should_play else "▶ PLAY")
        if not should_play: pygame.mixer.music.pause()
        
        self.clock_timer.start()
        self.update_labels_only()

    def engine_tick(self):
        if self.is_playing:
            self.current_pos_ms += 50
            if self.current_pos_ms >= self.duration_ms:
                self.current_pos_ms = self.duration_ms
                self.is_playing = False
                self.play_btn.setText("▶ PLAY")
                pygame.mixer.music.stop()
            
            self.seek_bar.blockSignals(True)
            self.seek_bar.setValue(self.current_pos_ms)
            self.seek_bar.blockSignals(False)
            self.update_labels_only()

    def update_labels_only(self):
        val = self.seek_bar.value() if not self.is_playing else self.current_pos_ms
        self.cur_time.setText(time.strftime('%M:%S', time.gmtime(val / 1000)))

    def toggle_play(self):
        if self.is_playing:
            pygame.mixer.music.pause()
            self.is_playing = False
            self.play_btn.setText("▶ PLAY")
        else:
            if self.current_pos_ms >= self.duration_ms - 100:
                self.seek_to_position(0, should_play=True)
            else:
                pygame.mixer.music.unpause()
                self.is_playing = True
                self.play_btn.setText("⏸ PAUSE")

    def on_seek_start(self):
        self.clock_timer.stop()

    def on_seek_end(self):
        target_ms = self.seek_bar.value()
        was_playing = self.play_btn.text() == "⏸ PAUSE"
        self.seek_to_position(target_ms, should_play=was_playing)

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save Audio", "speech_export.mp3", "MP3 Files (*.mp3)")
        if path: shutil.copy(TEMP_FILE, path)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())