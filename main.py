import sys
import os
import json
import logging
import subprocess
import tempfile
from pathlib import Path
import cv2
import ctypes
import urllib.request
from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QTime, QSize
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QGridLayout, QPushButton, QLabel, QLineEdit, QRadioButton,
    QButtonGroup, QProgressBar, QTextEdit, QFileDialog,
    QGraphicsDropShadowEffect, QCheckBox, QFrame, QSizePolicy,
    QMenuBar, QMenu, QDialog, QMessageBox, QTabWidget, QComboBox, QScrollArea
)
from PySide6.QtGui import QDragEnterEvent, QDropEvent, QColor, QPixmap, QImage, QFont, QAction, QIcon

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CURRENT_VERSION = "1.0.0" 

# 🟢 នេះជា Link បែប Raw ដែលបង្កើតចេញពី GitHub របស់បង Vathana ដោយស្វ័យប្រវត្ត
VERSION_URL = "https://raw.githubusercontent.com/Vathana-devkh/AK-Digital-Tool-PRO-STUDIO/main/version.json"

# =====================================================================
# helper: ប្រព័ន្ធទាញយក ICON .PNG (គាំទ្រទាំង Local និង .EXE)
# =====================================================================
def get_studio_icon(icon_name, fallback_emoji=""):
    # 🛡️ បើដំណើរការជា .EXE គឺទាញផ្លូវពី _MEIPASS បើ Run ធម្មតាគឺយកតាម current_dir
    if hasattr(sys, '_MEIPASS'):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.dirname(os.path.abspath(__file__))
        
    icon_path = os.path.join(base_path, "icons", f"{icon_name}.png")
    
    if os.path.exists(icon_path):
        return QIcon(icon_path)
        
    logging.warning(f"⚠️ Icon not found at: {icon_path}")
    return QIcon()

# =====================================================================
# 1. ADVANCED ALPHA MASK COMPOSITING PIPELINE ENGINE (MAINTAINED)
# =====================================================================

class GPUDetector:
    @staticmethod
    def detect_hardware_acceleration():
        try:
            res = subprocess.run(['ffmpeg', '-encoders'], capture_output=True, text=True, check=True)
            if 'hevc_nvenc' in res.stdout: return 'hevc_nvenc'
            elif 'hevc_qsv' in res.stdout: return 'hevc_qsv'
            elif 'hevc_amf' in res.stdout: return 'hevc_amf'
        except Exception: pass
        return 'libx265'

class VideoAnalyzer:
    @staticmethod
    def analyze_multiple(file_paths):
        total_duration = 0
        max_bitrate = 500000
        if not file_paths: return {'bitrate': max_bitrate, 'duration': 0}
        
        for path in file_paths:
            if not os.path.exists(path): continue
            try:
                cmd = ['ffprobe', '-v', 'quiet', '-print_format', 'json', '-show_format', '-show_streams', path]
                result = subprocess.run(cmd, capture_output=True, text=True, check=True)
                data = json.loads(result.stdout)
                
                for stream in data.get('streams', []):
                    if stream.get('codec_type') == 'video':
                        br = int(stream.get('bit_rate', 5000000)) // 1000
                        if br > max_bitrate: max_bitrate = br
                        break
                if 'format' in data:
                    total_duration += float(data['format'].get('duration', 0))
            except Exception: pass
            
        return {'bitrate': max_bitrate, 'duration': total_duration}

class FFmpegWorker(QThread):
    progress_signal = Signal(int, str)
    finished_signal = Signal(bool, str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self.process = None
        self.is_killed = False

    def run(self):
        try:
            transition_mov = self.config['transition_mov']  
            mid_list = self.config['mid_list']
            outro = self.config['outro']
            music = self.config['music']
            water = self.config['water']
            duration_sec = self.config['duration_sec']
            resolution_preset = self.config['resolution']
            output_folder = self.config['output_folder']
            mute_original = self.config['mute_original']
            video_title = self.config['video_title']
            
            if "4K" in resolution_preset: w, h = 3840, 2160
            elif "2K" in resolution_preset: w, h = 2560, 1440
            elif "720p" in resolution_preset: w, h = 1280, 720
            else: w, h = 1920, 1080
            
            temp_dir = tempfile.mkdtemp(prefix="ai_compositor_")
            self.progress_signal.emit(5, "⚡ [Core] Initializing Lossless Compositing Engine...")
            
            mid_info = VideoAnalyzer.analyze_multiple(mid_list)
            mid_total_duration = mid_info['duration']
            
            if mid_total_duration <= 0: 
                raise ValueError("Cannot compute mid video duration.")
                
            loop_count = max(1, round(duration_sec / mid_total_duration))
            
            self.progress_signal.emit(15, f"⏳ [1/4] Merging Mid Tracks with CRF 18 (Very Slow)...")
            cmd_mid_combine = ['ffmpeg', '-y']
            filter_input_segments = ""
            
            for index, path in enumerate(mid_list):
                cmd_mid_combine += ['-i', path]
                filter_input_segments += f"[{index}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{index}];"
                filter_input_segments += f"anullsrc=r=44100:cl=stereo[a_silent_{index}];[{index}:a][a_silent_{index}]amix=inputs=2:duration=first[a{index}];"
            
            for index in range(len(mid_list)):
                filter_input_segments += f"[v{index}][a{index}]"
            filter_input_segments += f"concat=n={len(mid_list)}:v=1:a=1[v_out][a_out]"
            
            single_mid_loop_path = os.path.join(temp_dir, "single_mid_combined.mp4")
            cmd_mid_combine += ['-filter_complex', filter_input_segments, '-map', '[v_out]', '-map', '[a_out]', '-c:v', 'libx264', '-preset', 'veryslow', '-crf', '18', '-c:a', 'aac', single_mid_loop_path]
            
            self.run_ffmpeg_cmd(cmd_mid_combine)
            if self.is_killed: return

            loop_v_path = os.path.join(temp_dir, "processed_mid.mp4")
            if mid_total_duration >= duration_sec:
                self.progress_signal.emit(35, f"✂️ [2/4] Executing Intelligent Asset Trim...")
                cmd_trim = ['ffmpeg', '-y', '-ss', '0', '-to', str(duration_sec), '-i', single_mid_loop_path, '-c:v', 'copy', '-c:a', 'copy', loop_v_path]
                self.run_ffmpeg_cmd(cmd_trim)
            else:
                self.progress_signal.emit(35, f"🔄 [2/4] Deploying Turbo Loop Engine ({loop_count} cycles)...")
                cmd_loop = ['ffmpeg', '-y', '-stream_loop', str(loop_count - 1), '-i', single_mid_loop_path, '-c:v', 'copy', '-c:a', 'copy', loop_v_path]
                self.run_ffmpeg_cmd(cmd_loop)
                
            if self.is_killed: return

            self.progress_signal.emit(55, "⏳ [3/4] Staging Master Sequences...")
            base_timeline_v = os.path.join(temp_dir, "base_timeline.mp4")
            
            if outro and os.path.exists(outro):
                cmd_out_join = ['ffmpeg', '-y', '-i', loop_v_path, '-i', outro]
                filter_join = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v0];" \
                              f"[1:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v1];" \
                              f"[v0][0:a][v1][1:a]concat=n=2:v=1:a=1[mo_v][mo_a]"
                cmd_out_join += ['-filter_complex', filter_join, '-map', '[mo_v]', '-map', '[mo_a]', '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-c:a', 'aac', base_timeline_v]
                self.run_ffmpeg_cmd(cmd_out_join)
            else:
                base_timeline_v = loop_v_path

            masked_v_path = os.path.join(temp_dir, "masked_master.mp4")
            if transition_mov and os.path.exists(transition_mov):
                self.progress_signal.emit(70, "🎭 [Masking] Injecting High-Fidelity Transition Overlay...")
                cmd_mask = [
                    'ffmpeg', '-y', 
                    '-i', base_timeline_v, 
                    '-i', transition_mov,
                    '-filter_complex', '[0:v][1:v]overlay=0:0:eof_action=pass[outv]', 
                    '-map', '[outv]', '-map', '0:a', 
                    '-c:v', 'libx264', '-crf', '17', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', masked_v_path
                ]
                self.run_ffmpeg_cmd(cmd_mask)
            else:
                masked_v_path = base_timeline_v

            if self.is_killed: return

            self.progress_signal.emit(85, "⏳ [4/4] Layering Background Scores...")
            os.makedirs(output_folder, exist_ok=True)
            
            safe_title = "".join([c for c in video_title if c.isalpha() or c.isdigit() or c in (' ', '_', '-')]).rstrip()
            safe_title = safe_title.replace(' ', '_') if safe_title else "AI_Studio_Output"
            res_tag = resolution_preset.replace(' ', '_')
            final_output = os.path.normpath(os.path.join(output_folder, f"{safe_title}_{res_tag}.mp4"))
            
            cmd_audio = ['ffmpeg', '-y', '-i', masked_v_path]
            has_bg_audio = (music and os.path.exists(music)) or (water and os.path.exists(water))
            
            if has_bg_audio:
                audio_inputs_count = 0
                mix_filter = ""
                amix_inputs_str = ""
                
                if not mute_original:
                    mix_filter += "[0:a]volume=1.0[a0];"
                    amix_inputs_str += "[a0]"
                    audio_inputs_count += 1
                
                if music and os.path.exists(music):
                    cmd_audio += ['-stream_loop', '-1', '-i', music]
                    mix_filter += f"[{audio_inputs_count + (1 if mute_original else 0)}:a]volume=0.2[m];"
                    amix_inputs_str += "[m]"
                    audio_inputs_count += 1
                    
                if water and os.path.exists(water):
                    cmd_audio += ['-stream_loop', '-1', '-i', water]
                    mix_filter += f"[{audio_inputs_count + (1 if mute_original else 0)}:a]volume=0.1[w];"
                    amix_inputs_str += "[w]"
                    audio_inputs_count += 1
                
                if mute_original and audio_inputs_count == 1:
                    cmd_audio += ['-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', str(duration_sec), final_output]
                else:
                    mix_filter += f"{amix_inputs_str}amix=inputs={audio_inputs_count}:duration=first[aout]"
                    cmd_audio += ['-filter_complex', mix_filter, '-map', '0:v', '-map', '[aout]', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', str(duration_sec), final_output]
            else:
                if mute_original:
                    cmd_audio += ['-c:v', 'copy', '-an', '-t', str(duration_sec), final_output]
                else:
                    cmd_audio += ['-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-t', str(duration_sec), final_output]
                
            self.run_ffmpeg_cmd(cmd_audio)
            
            if not self.is_killed and os.path.exists(final_output) and os.path.getsize(final_output) > 0:
                self.progress_signal.emit(100, "⚡ Ready")
                self.finished_signal.emit(True, final_output)
            else:
                raise FileNotFoundError("FFmpeg compilation failure.")
        except Exception as e:
            self.finished_signal.emit(False, str(e))

    def run_ffmpeg_cmd(self, cmd):
        cmd += ['-progress', 'pipe:1']
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        self.process = subprocess.Popen(cmd, startupinfo=startupinfo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, encoding='utf-8')
        while True:
            line = self.process.stdout.readline()
            if not line and self.process.poll() is not None: break
            if self.is_killed: break
        self.process.wait()

    def stop(self):
        self.is_killed = True
        if self.process:
            self.process.terminate()
            self.process.kill()

# =====================================================================
# 2. FUTURISTIC NEON DRAG & DROP WIDGET COMPONENT (MAINTAINED)
# =====================================================================

class PremiumDropWidget(QWidget):
    def __init__(self, title, is_multiple=False, is_optional=False, is_mov_only=False, parent=None):
        super().__init__(parent)
        self.file_paths = []
        self.is_multiple = is_multiple
        self.is_optional = is_optional
        self.is_mov_only = is_mov_only
        self.setAcceptDrops(True)
        self.setMinimumHeight(140)  
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.main_layout.setContentsMargins(12, 12, 12, 12)
        self.main_layout.setSpacing(6)
        
        self.lbl_thumbnail = QLabel(self)
        self.lbl_thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_thumbnail.setVisible(False)
        self.main_layout.addWidget(self.lbl_thumbnail)
        
        self.lbl_title = QLabel(title, self)
        self.lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        if is_mov_only:
            hint_text = "🌌 DROP ALPHA MASK"
            self.fmt_str = "MOV MODULE"
        else:
            hint_text = "🛰️ DEPLOY VIDEO TRACK" if is_multiple else "📥 DROP CORE SOURCE"
            self.fmt_str = "MP4 / MOV / MKV"
            
        self.lbl_hint = QLabel(hint_text, self)
        self.lbl_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.lbl_format = QLabel(self.fmt_str, self)
        self.lbl_format.setAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.main_layout.addWidget(self.lbl_title)
        self.main_layout.addWidget(self.lbl_hint)
        self.main_layout.addWidget(self.lbl_format)
        
        self.setObjectName("DropBox")
        
    def update_style(self, style_type="normal"):
        main_win = self.window()
        is_dark = getattr(main_win, 'is_dark_theme', True) if main_win else True
        
        if style_type == "normal":
            if is_dark:
                border_color = "rgba(255,255,255,0.08)" if self.is_optional else "#00E5FF" 
                if self.is_mov_only: border_color = "#BF5AF2"  
                bg_color = "rgba(30,41,59,0.3)" if self.is_mov_only else "rgba(22,30,49,0.4)"
                lbl_color = "#E5E7EB"
                hint_color = "#9CA3AF"
                fmt_color = "#BF5AF2" if self.is_mov_only else "#00E5FF"
            else:
                border_color = "#D1D5DB" if self.is_optional else "#007AFF"
                if self.is_mov_only: border_color = "#BF5AF2"
                bg_color = "#F9FAFB" if self.is_mov_only else "#FFFFFF"
                lbl_color = "#1F2937"
                hint_color = "#4B5563"
                fmt_color = "#BF5AF2" if is_dark else "#007AFF"
                
            self.setStyleSheet(f"QWidget#DropBox {{ border: 1px solid {border_color}; border-radius: 14px; background-color: {bg_color}; }}")
            self.lbl_title.setStyleSheet(f"font-size: 11px; font-weight: 700; color: {lbl_color}; background: transparent; font-family: 'Kantumruy Pro';")
            self.lbl_hint.setStyleSheet(f"font-size: 10px; color: {hint_color}; background: transparent; font-family: 'Kantumruy Pro';")
            self.lbl_format.setStyleSheet(f"font-size: 9px; font-weight: bold; color: {fmt_color}; background: transparent; font-family: 'Kantumruy Pro';")
            
        elif style_type == "hover":
            accent = "#30D158"
            self.setStyleSheet(f"QWidget#DropBox {{ border: 1.5px dashed {accent}; border-radius: 14px; background-color: rgba(48,209,88,0.08); }}")
        elif style_type == "success":
            bg = "rgba(5,20,11,0.6)" if is_dark else "#E8F5E9"
            self.setStyleSheet(f"QWidget#DropBox {{ border: 1px solid #30D158; border-radius: 14px; background-color: {bg}; }}")

    def generate_and_show_thumbnail(self, video_path):
        try:
            cap = cv2.VideoCapture(video_path)
            success, frame = cap.read()
            cap.release()
            if success:
                frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                h, w, ch = frame.shape
                bytes_per_line = ch * w
                qt_img = QImage(frame.data, w, h, bytes_per_line, QImage.Format_RGB888)
                pixmap = QPixmap.fromImage(qt_img)
                scaled_pixmap = pixmap.scaled(self.width() - 20, 90, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
                self.lbl_thumbnail.setPixmap(scaled_pixmap)
                self.lbl_thumbnail.setVisible(True)
                self.update_style("success")
        except Exception as e:
            logging.error(f"Cannot extract thumbnail: {e}")

    def mousePressEvent(self, event):
        fmt = "Videos (*.mov)" if self.is_mov_only else "Videos (*.mp4 *.mov *.mkv)"
        if self.is_multiple:
            files, _ = QFileDialog.getOpenFileNames(self, "Load Production Line Clips", "", fmt)
            if files: self.set_files(files)
        else:
            file_path, _ = QFileDialog.getOpenFileName(self, "Load Studio Assets", "", fmt)
            if file_path: self.set_files([file_path])

    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.update_style("hover")

    def leaveEvent(self, event):
        if not self.file_paths: self.update_style("normal")

    def dropEvent(self, event: QDropEvent):
        dropped_files = []
        exts = ('.mov',) if self.is_mov_only else ('.mp4', '.mov', '.mkv')
        for url in event.mimeData().urls():
            path = str(url.toLocalFile())
            if path.lower().endswith(exts):
                dropped_files.append(path)
        
        if dropped_files:
            if self.is_multiple:
                self.set_files(dropped_files)
            else:
                self.set_files([dropped_files[0]])

    def set_files(self, paths):
        self.file_paths = paths
        filename = os.path.basename(paths[0])
        self.generate_and_show_thumbnail(paths[0])
        
        if self.is_multiple:
            self.lbl_hint.setText(f"🧬 CONNECTED: {len(paths)} CLIPS")
            self.lbl_hint.setStyleSheet("font-size: 10px; font-weight: 700; color: #30D158; background: transparent; font-family: 'Kantumruy Pro';")
        else:
            self.lbl_hint.setText(filename if len(filename) < 18 else f"{filename[:15]}...")
            self.lbl_hint.setStyleSheet("font-size: 10px; font-weight: 600; color: #30D158; background: transparent; font-family: 'Kantumruy Pro';")

    def get_single_path(self):
        return self.file_paths[0] if self.file_paths else None

# =====================================================================
# 3. EXTRA INTERFACE COMPONENTS: SETTINGS EDITOR & ABOUT DIALOGS
# =====================================================================

class FfmpegSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Studio FFmpeg Command Editor")
        self.resize(650, 480)
        self.setStyleSheet("background-color: #F4F6F9; color: #1F2937;")
        
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        
        # Default fallback configurations template commands
        self.cmd_video = QTextEdit("-c:v libx264 -preset veryslow -crf 18")
        self.cmd_music = QTextEdit("-c:a aac -b:a 192k -filter_complex amix")
        self.cmd_mask = QTextEdit("-filter_complex overlay=0:0:eof_action=pass")
        
        for txt_box in [self.cmd_video, self.cmd_music, self.cmd_mask]:
            txt_box.setStyleSheet("background-color: #FFFFFF; color: #111827; border: 1px solid #D1D5DB; border-radius: 8px; font-family: Consolas; font-size: 11px;")
            
        tabs.addTab(self.cmd_video, "🎬 Video Engine Command")
        tabs.addTab(self.cmd_music, "🎵 Music Engine Command")
        tabs.addTab(self.cmd_mask, "🎭 Mask Layer Command")
        layout.addWidget(tabs)
        
        h_btn = QHBoxLayout()
        btn_save = QPushButton("Save Settings")
        btn_save.setStyleSheet("background-color: #007AFF; color: white; border-radius: 6px; padding: 8px 16px; font-weight: bold;")
        btn_save.clicked.connect(self.accept)
        h_btn.addStretch()
        h_btn.addWidget(btn_save)
        layout.addLayout(h_btn)

# =====================================================================
# 5. AUTOMATIC UPDATE CHECKER & EXECUTOR (GITHUB-BASED VERSION CONTROL)
# =====================================================================
class AutoUpdater(QThread):
    update_available = Signal(str, str) # បញ្ជូន (latest_version, download_url)
    no_update_found = Signal()          # បញ្ជូនសញ្ញានៅពេលដែលកម្មវិធីជា Version ចុងក្រោយហើយ
    
    def run(self):
        try:
            req = urllib.request.Request(VERSION_URL, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode('utf-8'))
                latest_version = data.get("version", "1.0.0")
                download_url = data.get("download_url", "")
                
                # បើ Version លើ GitHub ថ្មីជាង នឹងផ្ញើ Signal ទៅឱ្យដំឡើង
                if latest_version > CURRENT_VERSION:
                    self.update_available.emit(latest_version, download_url)
                else:
                    self.no_update_found.emit()
        except Exception as e:
            print(f"ពិនិត្យការអាប់ដេតមិនជោគជ័យ: {e}")

def execute_update(download_url):
    try:
        # 🎯 រកផ្លូវពិតប្រាកដរបស់ឯកសារ .exe ដែលកំពុងបើករត់
        current_exe = os.path.abspath(sys.argv[0])
        exe_dir = os.path.dirname(current_exe)
        
        # បើ User រត់ជាកូដ .py ធម្មតា ឱ្យទាញយកជា file main.py វិញ
        if not current_exe.endswith('.exe'):
            req = urllib.request.Request("https://raw.githubusercontent.com/Vathana-devkh/AK-Digital-Tool-PRO-STUDIO/refs/heads/main/main.py", headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=15) as response:
                with open(current_exe, 'wb') as f:
                    f.write(response.read())
            return True

        temp_exe = os.path.join(exe_dir, "AK-Digital-Tool-PRO-STUDIO_new.exe")
        
        # ១. ទាញយកឯកសារ .exe ថ្មីស្រឡាងពី GitHub
        req = urllib.request.Request(download_url, headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=60) as response:
            with open(temp_exe, 'wb') as f:
                f.write(response.read())
                
        # ២. ប្រព័ន្ធ Script .bat ទៅបង្ខំបិទ Process, លុបអាចាស់, ប្តូរឈ្មោះអាថ្មី និង Reboot ឡើងវិញ
        bat_path = os.path.join(exe_dir, "updater.bat")
        exe_name = os.path.basename(current_exe)
        
        with open(bat_path, "w", encoding="cp1252") as f:
            f.write(f"""@echo off
:loop
taskkill /IM "{exe_name}" /F >nul 2>&1
timeout /t 1 /nobreak >nul
del /f /q "{current_exe}"
if exist "{current_exe}" goto loop
move /y "{temp_exe}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
""")
            f.flush()            # 🛡️ បង្ខំឱ្យទម្លាក់ទិន្នន័យចេញពី Buffer
            os.fsync(f.fileno()) # 🛡️ បង្ខំឱ្យ Windows រក្សាទុកឯកសារចូលទៅក្នុង Disk ភ្លាមៗ
        
        # ៣. ពិនិត្យមើលឱ្យច្បាស់ថា File ពិតជាមាននៅលើ Disk មែន ទើបចាប់ផ្ដើមបញ្ជារត់
        import time
        retry = 0
        while not os.path.exists(bat_path) and retry < 5:
            time.sleep(0.5) # បើរកមិនឃើញ ឱ្យរង់ចាំ 0.5 វិនាទីសិន (ទប់ស្កាត់ការរកមិនឃើញ)
            retry += 1
            
        if os.path.exists(bat_path):
            subprocess.Popen([bat_path], shell=True, creationflags=subprocess.CREATE_NO_WINDOW)
            return True
        else:
            logging.error("❌ មិនអាចបង្កើតឯកសារ updater.bat ទៅក្នុង Disk បានឡើយ។")
            return False
            
    except Exception as e:
        logging.error(f"ការអាប់ដេត Binary .EXE បរាជ័យ: {e}")
        return False
# =====================================================================
# 4. MAIN INTERFACE WINDOW MODULE (LIGHT DEFAULT, NAV BAR, FIXED EXPORT)
# =====================================================================

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("AK Digital Tool PRO STUDIO")
        self.resize(1280, 900)
        self.setMinimumSize(1280, 900)  
        
        self.worker = None
        self.is_dark_theme = True  # Default template loaded in Light Engine state mode
        
        # Setup Default FFmpeg Code Configuration Placeholders
        self.custom_ffmpeg_video = "-c:v libx264 -preset veryslow -crf 18"
        self.custom_ffmpeg_music = "-c:a aac -b:a 192k -filter_complex amix"
        self.custom_ffmpeg_mask = "-filter_complex overlay=0:0:eof_action=pass"

        # =========================================================
        # 🟢 ADDED NAVBAR (MENU BAR) SYSTEM INTEGRATION
        # =========================================================
        self.setup_navbar_system()

        self.render_timer = QTimer()
        self.render_timer.timeout.connect(self.update_elapsed_time)
        self.elapsed_time = QTime(0, 0, 0)

        self.main_widget = QWidget()
        self.setCentralWidget(self.main_widget)
        self.main_widget_layout = QVBoxLayout(self.main_widget)
        self.main_widget_layout.setContentsMargins(24, 24, 24, 24)
        self.main_widget_layout.setSpacing(18)

        # =========================================================
        # PREMIUM HEADER CONTAINER
        # =========================================================
        self.header_container = QWidget()
        self.header_container.setObjectName("HeaderContainer")

        header_layout = QHBoxLayout(self.header_container)
        header_layout.setContentsMargins(24, 14, 24, 14)
        header_layout.setSpacing(18)

        self.lbl_logo = QLabel()
        logo_path = os.path.normpath(r"D:\DATA_TOOL\AI Video Loop Generator\logo.png")

        if os.path.exists(logo_path):
            pix = QPixmap(logo_path)
            scaled_pix = pix.scaled(70, 70, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            self.lbl_logo.setPixmap(scaled_pix)

            logo_glow = QGraphicsDropShadowEffect()
            logo_glow.setBlurRadius(25)
            logo_glow.setOffset(0, 0)
            logo_glow.setColor(QColor(0, 229, 255, 100))
            self.lbl_logo.setGraphicsEffect(logo_glow)
        else:
            self.lbl_logo.setText("🎬")
            self.lbl_logo.setStyleSheet("font-size: 40px;")

        self.lbl_main_title = QLabel()
        self.lbl_main_title.setTextFormat(Qt.TextFormat.RichText)

        badge_layout = QHBoxLayout()
        badge_layout.setSpacing(8)

        self.lbl_gpu = QLabel("⚡ GPU READY")
        self.lbl_engine = QLabel("🎞️ FFmpeg Engine")
        badge_layout.addWidget(self.lbl_gpu)
        badge_layout.addWidget(self.lbl_engine)

        left_header = QVBoxLayout()
        left_header.setSpacing(4)
        left_header.addWidget(self.lbl_main_title)
        left_header.addLayout(badge_layout)

        self.btn_theme_toggle = QPushButton()
        self.btn_theme_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme_toggle.setFixedSize(140, 34)
        self.btn_theme_toggle.clicked.connect(self.toggle_theme_style)

        header_layout.addWidget(self.lbl_logo)
        header_layout.addLayout(left_header)
        header_layout.addStretch()
        header_layout.addWidget(self.btn_theme_toggle)

        self.main_widget_layout.addWidget(self.header_container)

        # =========================================================
        # 🎞️ TIMELINE LANES BLOCK
        # =========================================================
        self.top_container = QWidget()
        top_layout = QVBoxLayout(self.top_container)
        top_layout.setContentsMargins(18, 18, 18, 18)
        top_layout.setSpacing(10)
        
        self.lbl_seq_title = QLabel("🛰️ QUANTUM SEQUENCER HOLOGRAPHIC TIMELINE")
        top_layout.addWidget(self.lbl_seq_title)
        
        h_lane_layout = QHBoxLayout()
        h_lane_layout.setSpacing(14)
        
        self.input_transition = PremiumDropWidget("[LANE A] MOV ALPHA MASK OVERLAY", is_multiple=False, is_optional=True, is_mov_only=True)
        self.input_mid = PremiumDropWidget("[LANE B] RECURSIVE MID ENGINE (LOOP TRACKS)", is_multiple=True, is_optional=False)
        self.input_outro = PremiumDropWidget("[LANE C] TERMINAL OUTRO SEGMENT", is_multiple=False, is_optional=True)
        
        h_lane_layout.addWidget(self.input_transition, 1)
        h_lane_layout.addWidget(self.input_mid, 2)  
        h_lane_layout.addWidget(self.input_outro, 1)
        top_layout.addLayout(h_lane_layout)
        self.main_widget_layout.addWidget(self.top_container)

        # =========================================================
        # ⚙️ CENTRAL CONTROL MATRIX (HORIZONTALLY ALIGNED FOR SCAPE SPACE)
        # =========================================================
        h_panels_layout = QHBoxLayout()
        h_panels_layout.setSpacing(12)
        
        self.audio_box = QWidget()
        audio_layout = QVBoxLayout(self.audio_box)
        audio_layout.setContentsMargins(14, 14, 14, 14)
        audio_layout.setSpacing(6)
        
        self.lbl_sec_audio = QLabel("🛜 AUDIO MIX LAYER")
        audio_layout.addWidget(self.lbl_sec_audio)
        
        audio_split_layout = QHBoxLayout()
        audio_split_layout.setSpacing(14)
        
        audio_left_col = QVBoxLayout()
        audio_left_col.setSpacing(15)
        self.btn_mute_toggle = QPushButton("ORIGINAL AUDIO: ON")
        self.btn_mute_toggle.setCheckable(True)
        self.btn_mute_toggle.setFixedHeight(40)
        self.btn_mute_toggle.clicked.connect(self.action_mute_toggle_changed)
        audio_left_col.addWidget(self.btn_mute_toggle)
        audio_left_col.addStretch()
        audio_split_layout.addLayout(audio_left_col, 1)
        
        audio_right_col = QVBoxLayout()
        audio_right_col.setSpacing(2)
        self.lbl_bg_score = QLabel("Background Studio Score:")
        audio_right_col.addWidget(self.lbl_bg_score)
        h_music = QHBoxLayout()
        self.txt_music = QLineEdit()
        self.txt_music.setPlaceholderText("Select score track...")
        self.btn_m = QPushButton("Browse")
        self.btn_m.setIcon(get_studio_icon("browse"))
        self.btn_m.clicked.connect(self.browse_music)
        h_music.addWidget(self.txt_music)
        h_music.addWidget(self.btn_m)
        audio_right_col.addLayout(h_music)
        
        self.lbl_ambient = QLabel("Ambient Sounds:")
        audio_right_col.addWidget(self.lbl_ambient)
        h_water = QHBoxLayout()
        self.txt_water = QLineEdit()
        self.txt_water.setPlaceholderText("Select spatial audio...")
        self.btn_w = QPushButton("Browse")
        self.btn_w.setIcon(get_studio_icon("browse"))
        self.btn_w.clicked.connect(self.browse_water)
        h_water.addWidget(self.txt_water)
        h_water.addWidget(self.btn_w)
        audio_right_col.addLayout(h_water)
        audio_split_layout.addLayout(audio_right_col, 8)
        
        audio_layout.addLayout(audio_split_layout)
        h_panels_layout.addWidget(self.audio_box, 5) 
        
        self.config_matrix_container = QWidget()
        config_matrix_layout = QHBoxLayout(self.config_matrix_container)
        config_matrix_layout.setContentsMargins(0, 0, 0, 0)
        config_matrix_layout.setSpacing(12)
        
        self.time_box = QWidget()
        self.time_box.setObjectName("TimeBox")
        time_layout = QVBoxLayout(self.time_box)
        time_layout.setContentsMargins(18, 18, 18, 18)
        time_layout.setSpacing(12)
        
        self.lbl_sec_time = QLabel("⚡ BOUND PLAYTIME VECTOR")
        time_layout.addWidget(self.lbl_sec_time)
        
        self.loop_group = QButtonGroup(self)
        times_layout = QGridLayout()
        times_layout.setSpacing(8)
        times_list = ["00:59:59", "01:59:59", "02:59:59", "03:59:59"]
        for idx, t in enumerate(times_list):
            rb = QRadioButton(t)
            rb.setMinimumSize(100, 34)
            rb.toggled.connect(self.update_active_panel_styles)
            times_layout.addWidget(rb, idx // 2, idx % 2)
            self.loop_group.addButton(rb)
        time_layout.addLayout(times_layout)
        
        h_custom = QHBoxLayout()
        h_custom.setSpacing(4)
        self.rb_custom = QRadioButton()
        self.rb_custom.toggled.connect(self.update_active_panel_styles)
        self.loop_group.addButton(self.rb_custom)
        self.lbl_custom = QLabel("Custom:")
        self.txt_custom_time = QLineEdit("00:00:00")
        self.txt_custom_time.setAlignment(Qt.AlignmentFlag.AlignCenter)
        h_custom.addWidget(self.rb_custom)
        h_custom.addWidget(self.lbl_custom)
        h_custom.addWidget(self.txt_custom_time)
        time_layout.addLayout(h_custom)
        
        self.res_box = QWidget()
        self.res_box.setObjectName("ResBox")
        res_layout = QVBoxLayout(self.res_box)
        res_layout.setContentsMargins(18, 18, 18, 18)
        res_layout.setSpacing(20)
        
        self.lbl_sec_res = QLabel("📺 RESOLUTION RENDER ARRAY")
        res_layout.addWidget(self.lbl_sec_res)
        
        self.res_group = QButtonGroup(self)
        res_grid = QGridLayout()
        res_grid.setSpacing(8)
        resolutions = ["4K UHD", "2K QHD", "1080p FHD", "720p HD"]
        for idx, res in enumerate(resolutions):
            rb = QRadioButton(res)
            rb.setMinimumSize(100, 34)
            rb.toggled.connect(self.update_active_panel_styles)
            res_grid.addWidget(rb, idx // 2, idx % 2)
            self.res_group.addButton(rb)
        res_layout.addLayout(res_grid)
        res_layout.addStretch() 
        
        config_matrix_layout.addWidget(self.time_box, 1)
        config_matrix_layout.addWidget(self.res_box, 1)
        h_panels_layout.addWidget(self.config_matrix_container, 5) 
        
        self.main_widget_layout.addLayout(h_panels_layout)

        self.loop_group.buttons()[0].setChecked(True)
        self.res_group.buttons()[2].setChecked(True)

        # =========================================================
        # 📁 OUTPUT DIRECTORY PIPELINE CONFIG
        # =========================================================
        self.out_box = QWidget()
        out_layout = QVBoxLayout(self.out_box)
        out_layout.setContentsMargins(18, 18, 18, 18)
        out_layout.setSpacing(10)
        self.lbl_sec_out = QLabel("📁 OUTPUT PIPELINE ROUTING CONFIGURATION")
        out_layout.addWidget(self.lbl_sec_out)
        
        h_title_lane = QHBoxLayout()
        self.lbl_video_title = QLabel("Video Title (ឈ្មោះវីដេអូ):")
        self.lbl_video_title.setMinimumWidth(140)
        self.txt_video_title = QLineEdit()
        self.txt_video_title.setPlaceholderText("Enter output video filename title here...")
        self.txt_video_title.setText("AK_Digital_Hub_Loop")
        h_title_lane.addWidget(self.lbl_video_title)
        h_title_lane.addWidget(self.txt_video_title, 1)
        out_layout.addLayout(h_title_lane)
        
        h_out_engine = QHBoxLayout()
        self.txt_output_path = QLineEdit()
        self.txt_output_path.setText(os.path.normpath(os.path.join(os.path.expanduser("~"), "Videos")))
        
        self.btn_browse_out = QPushButton("Select Output Folder")
        self.btn_browse_out.setMinimumWidth(180)
        self.btn_browse_out.clicked.connect(self.browse_output_destination) # FIXED ACTIVE ROUTER
        
        h_out_engine.addWidget(self.txt_output_path, 1)
        h_out_engine.addWidget(self.btn_browse_out)
        out_layout.addLayout(h_out_engine)
        self.main_widget_layout.addWidget(self.out_box)

        # =========================================================
        # 📡 CONSOLE TERMINAL MONITOR & RENDERING CORES
        # =========================================================
        bottom_panel = QHBoxLayout()
        bottom_panel.setSpacing(16)
        
        self.console_container = QWidget()
        console_layout = QVBoxLayout(self.console_container)
        console_layout.setContentsMargins(18, 18, 18, 18)
        console_layout.setSpacing(10)
        
        h_status_line = QHBoxLayout()
        self.lbl_sec_con = QLabel("📡 LIVE MATRIX TELEMETRY MONITOR")
        h_status_line.addWidget(self.lbl_sec_con)
        h_status_line.addStretch()
        
        self.lbl_timer_display = QLabel("ELAPSED TIME: [00:00:00]")
        h_status_line.addWidget(self.lbl_timer_display)
        console_layout.addLayout(h_status_line)
        
        self.console_log = QTextEdit()
        self.console_log.setReadOnly(True)
        console_layout.addWidget(self.console_log)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(8)
        self.progress_bar.setTextVisible(False)
        console_layout.addWidget(self.progress_bar)
        bottom_panel.addWidget(self.console_container, 4)
        
        btn_panel = QVBoxLayout()
        btn_panel.setSpacing(12)
        
        self.btn_start = QPushButton("START")
        self.btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_start.clicked.connect(self.start_process)
        
        self.btn_glow = QGraphicsDropShadowEffect()
        self.btn_glow.setBlurRadius(20)
        self.btn_glow.setOffset(0, 0)
        self.btn_start.setGraphicsEffect(self.btn_glow)
        
        self.btn_stop = QPushButton("STOP")
        self.btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self.stop_process)
        
        btn_panel.addStretch()
        btn_panel.addWidget(self.btn_start)
        btn_panel.addWidget(self.btn_stop)
        bottom_panel.addLayout(btn_panel, 1)
        
        self.main_widget_layout.addLayout(bottom_panel)

        self.apply_shadow(self.header_container)
        self.apply_shadow(self.top_container)
        self.apply_shadow(self.audio_box)
        self.apply_shadow(self.out_box)
        self.apply_shadow(self.console_container)
        self.apply_shadow(self.time_box)
        self.apply_shadow(self.res_box)

        self.apply_theme_style()

    # =========================================================
    # 🟢 SETUP NAVBAR METHOD IMPLEMENTATION
    # =========================================================
    def setup_navbar_system(self):
        menubar = self.menuBar()
        menubar.setStyleSheet("QMenuBar { background-color: #FFFFFF; color: #1F2937; font-family: 'Kantumruy Pro'; font-size: 11px; border-bottom: 1px solid #D1D5DB; } QMenuBar::item:selected { background-color: #E6F0FA; color: #007AFF; }")
        
        # 1. FILE MENU
        file_menu = menubar.addMenu("File")
        act_min = QAction("Minimize Window", self)
        act_min.triggered.connect(self.showMinimized)
        act_max = QAction("Maximize Window", self)
        act_max.triggered.connect(self.toggle_max_restore)
        act_close = QAction("Close Application", self)
        act_close.triggered.connect(self.close)
        
        file_menu.addActions([act_min, act_max])
        file_menu.addSeparator()
        file_menu.addAction(act_close)
        
        # 2. EDIT MENU
        edit_menu = menubar.addMenu("Edit")
        edit_menu.addActions([QAction("Undo", self), QAction("Redo", self), QAction("Cut", self), QAction("Copy", self), QAction("Paste", self)])
        
        # 3. SETTING MENU
        setting_menu = menubar.addMenu("Setting")
        act_set_vid = QAction("១. Setting Video (កូដ CMD Video)", self)
        act_set_vid.triggered.connect(self.open_ffmpeg_editor)
        act_set_mus = QAction("២. Setting Music (កូដ CMD Music)", self)
        act_set_mus.triggered.connect(self.open_ffmpeg_editor)
        act_set_msk = QAction("៣. Setting Mask (កូដ CMD Mask Overlay)", self)
        act_set_msk.triggered.connect(self.open_ffmpeg_editor)
        setting_menu.addActions([act_set_vid, act_set_mus, act_set_msk])
        
        # 4. UPDATE MENU
        update_menu = menubar.addMenu("Update")
        act_git = QAction("Update New Version", self)
        act_git.triggered.connect(self.trigger_github_update)
        update_menu.addAction(act_git)
        
        # 5. ABOUT MENU
        about_menu = menubar.addMenu("About")
        act_guide = QAction("១. របៀបប្រើប្រាស់ (User Guide)", self)
        act_guide.triggered.connect(self.show_about_guide)
        act_version = QAction("២. ជំនាន់របស់កម្មវិធី (App Version)", self)
        act_version.triggered.connect(self.show_about_version)
        act_admin = QAction("៣. ទំនាក់ទំនងមកកាន់ Admin", self)
        act_admin.triggered.connect(self.show_about_admin)
        about_menu.addActions([act_guide, act_version, act_admin])

    def toggle_max_restore(self):
        if self.isMaximized(): self.showNormal()
        else: self.showMaximized()

    def open_ffmpeg_editor(self):
        dialog = FfmpegSettingsDialog(self)
        dialog.cmd_video.setPlainText(self.custom_ffmpeg_video)
        dialog.cmd_music.setPlainText(self.custom_ffmpeg_music)
        dialog.cmd_mask.setPlainText(self.custom_ffmpeg_mask)
        if dialog.exec():
            self.custom_ffmpeg_video = dialog.cmd_video.toPlainText().strip()
            self.custom_ffmpeg_music = dialog.cmd_music.toPlainText().strip()
            self.custom_ffmpeg_mask = dialog.cmd_mask.toPlainText().strip()
            self.console_log.append("⚙️ [SYSTEM] FFmpeg Command configurations updated successfully.")

    def trigger_github_update(self):
        self.statusBar().showMessage("កំពុងពិនិត្យមើលកំណែទម្រង់ថ្មីពី GitHub...", 5000)
        self.updater = AutoUpdater()
        
        def on_update_found(latest_ver, url):
            reply = QMessageBox.question(
                self, 
                "រកឃើញកំណែទម្រង់ថ្មី!", 
                f"កម្មវិធីមាន Version ថ្មី ({latest_ver})។ តើអ្នកចង់អាប់ដេតឡើយទេ?",
                QMessageBox.Yes | QMessageBox.No
            )
            if reply == QMessageBox.Yes:
                self.statusBar().showMessage("កំពុងទាញយកឯកសារកែប្រែថ្មីពី GitHub សូមរង់ចាំ...", 15000)
                if execute_update(url):
                    QMessageBox.information(self, "ជោគជ័យ", "ការអាប់ដេតបានជោគជ័យ! កម្មវិធីនឹងបិទដើម្បីដំឡើងជំនាន់ថ្មីស្វ័យប្រវត្ត។")
                    sys.exit(0) # បិទខ្លួនឯង ដើម្បីឱ្យ file .bat ធ្វើការ Reboot បើកឡើងវិញ
                else:
                    QMessageBox.critical(self, "កំហុស", "ការទាញយកកូដថ្មីមានបញ្ហា។")
                    self.statusBar().clearMessage()
                    
        def on_no_update():
            QMessageBox.information(self, "ព័ត៌មាន", f"កម្មវិធីរបស់អ្នកជាកំណែទម្រង់ចុងក្រោយបង្អស់ហើយ ({CURRENT_VERSION})។")
            self.statusBar().showMessage("កម្មវិធីជាជំនាន់ចុងក្រោយបង្អស់ហើយ។", 3000)

        self.updater.update_available.connect(on_update_found)
        self.updater.no_update_found.connect(on_no_update)
        self.updater.start()

    def show_about_guide(self):
        QMessageBox.information(self, "របៀបប្រើប្រាស់ (User Guide)", "១. ទាញទម្លាក់វីដេអូ ឬ Alpha Mask ចូលទៅក្នុង Lanes នីមួយៗ\n២. កំណត់ម៉ោងលេង (Playtime Vector) និងទំហំ (Resolution)\n៣. បញ្ចូលសំឡេងផ្ទៃក្រោយ ឬសំឡេងបរិយាកាស (Optional)\n៤. ចុចប៊ូតុង INITIALIZE SYSTEM CORE ដើម្បីចាប់ផ្ដើមផលិត។")

    def show_about_version(self):
        QMessageBox.information(self, "ជំនាន់របស់កម្មវិធី (App Version)", "Core Engine Architecture: Premium Core Alpha 2026\nApplication Version: 2.5.0\nFramework Engine: PySide6 (Qt for Python)")

    def show_about_admin(self):
        QMessageBox.information(self, "ទំនាក់ទំនងមកកាន់ Admin", "Software Developer / System Architect: Noy Vathana\nTelegram System Route Support: https://t.me/vathana_trader\nFunder of AK Digital Tool PRO STUDIO")

    def apply_shadow(self, widget, color=QColor(0,0,0,100)):
        shadow = QGraphicsDropShadowEffect()
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 5)
        shadow.setColor(color)
        widget.setGraphicsEffect(shadow)

    def toggle_theme_style(self):
        self.is_dark_theme = not self.is_dark_theme
        self.apply_theme_style()

    def apply_theme_style(self):
        if self.is_dark_theme:
            self.main_widget.setStyleSheet("background-color: #0B0F19;")
            self.btn_theme_toggle.setText("LIGHT MODE")
            self.btn_theme_toggle.setIcon(get_studio_icon("light_mode"))
            self.header_container.setStyleSheet("QWidget#HeaderContainer { background-color: transparent; border: none; }")
            self.lbl_main_title.setText("<div><div style='font-size:20px; font-weight:900; color:#FFFFFF; font-family: \"Kantumruy Pro\";'>AK Digital Tool PRO STUDIO</div></div>")
            self.lbl_gpu.setStyleSheet("background-color: rgba(0,229,255,0.1); border: 1px solid #00E5FF; border-radius: 6px; padding: 3px 8px; color: #00E5FF; font-size: 9px; font-weight: bold;")
            self.lbl_engine.setStyleSheet("background-color: rgba(191,90,242,0.1); border: 1px solid #BF5AF2; border-radius: 6px; padding: 3px 8px; color: #BF5AF2; font-size: 9px; font-weight: bold;")
            
            widget_bg = "rgba(22, 30, 49, 0.7)"
            border_css = "border: 1px solid rgba(255,255,255,0.05); border-radius: 12px;"
            text_color = "#9CA3AF"
            sec_header = "#00E5FF"  
            input_css = "QLineEdit { background-color: #0F172A; border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; color: #FFFFFF; padding: 6px 10px; font-size: 11px; font-family: 'Kantumruy Pro'; }"
            btn_browse = "background-color: rgba(0,229,255,0.1); color: #00E5FF; border: 1px solid #00E5FF; border-radius: 6px; padding: 5px 12px; font-weight: bold; font-size: 11px;"
            self.console_log.setStyleSheet("background-color: #020617; border: 1px solid rgba(255,255,255,0.05); border-radius: 10px; color: #00FF99; padding: 10px; font-size: 11px; font-family: Consolas;")
            self.progress_bar.setStyleSheet("QProgressBar { background-color: #1E293B; border-radius: 3px; } QProgressBar::chunk { border-radius: 3px; background: #00E5FF; }")
            self.btn_theme_toggle.setStyleSheet("background-color: #1E293B; color: #E5E7EB; border: 1px solid rgba(255,255,255,0.1); border-radius: 6px; font-size: 11px; font-weight: bold;")
            self.btn_start.setStyleSheet("QPushButton { background-color: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #00E5FF, stop:1 #BF5AF2); color: white; border-radius: 10px; font-size: 12px; font-weight: bold; min-width: 220px; min-height: 46px; }")
            self.btn_stop.setStyleSheet("QPushButton { background-color: rgba(255,69,58,0.08); color: #FF453A; border: 1px solid #FF453A; border-radius: 10px; font-size: 11px; font-weight: bold; min-width: 220px; min-height: 40px; }")
            if self.menuBar(): self.menuBar().setStyleSheet("QMenuBar { background-color: #111827; color: #E5E7EB; font-family: 'Kantumruy Pro'; font-size: 11px; } QMenuBar::item:selected { background-color: #1F2937; color: #00E5FF; }")
        else:
            self.main_widget.setStyleSheet("background-color: #F4F6F9;")
            self.btn_theme_toggle.setText("DARK MODE")
            self.btn_theme_toggle.setIcon(get_studio_icon("dark_mode"))
            self.header_container.setStyleSheet("QWidget#HeaderContainer { background-color: transparent; border: none; }")
            self.lbl_main_title.setText("<div><div style='font-size:20px; font-weight:900; color:#1F2937; font-family: \"Kantumruy Pro\";'>AI VIDEO LOOP GENERATOR</div></div>")
            self.lbl_gpu.setStyleSheet("background-color: rgba(0,122,255,0.08); border: 1px solid #007AFF; border-radius: 6px; padding: 3px 8px; color: #007AFF; font-size: 9px; font-weight: bold;")
            self.lbl_engine.setStyleSheet("background-color: rgba(191,90,242,0.08); border: 1px solid #BF5AF2; border-radius: 6px; padding: 3px 8px; color: #BF5AF2; font-size: 9px; font-weight: bold;")
            
            widget_bg = "#FFFFFF"   
            border_css = "border: 1px solid #D1D5DB; border-radius: 12px;"
            text_color = "#374151"
            sec_header = "#007AFF"  
            input_css = "QLineEdit { background-color: #F9FAFB; border: 1px solid #C4C7CC; border-radius: 8px; color: #111827; padding: 6px 10px; font-size: 11px; font-family: 'Kantumruy Pro'; }"
            btn_browse = "background-color: #007AFF; color: #FFFFFF; border: none; border-radius: 6px; padding: 5px 12px; font-weight: bold; font-size: 11px;"
            self.console_log.setStyleSheet("background-color: #FFFFFF; border: 1px solid #C4C7CC; border-radius: 10px; color: #111827; padding: 10px; font-size: 11px; font-family: 'Kantumruy Pro';")
            self.progress_bar.setStyleSheet("QProgressBar { background-color: #E5E7EB; border-radius: 4px; } QProgressBar::chunk { border-radius: 4px; background-color: #007AFF; }")
            self.btn_theme_toggle.setStyleSheet("background-color: #FFFFFF; color: #1F2937; border: 1px solid #D1D5DB; border-radius: 6px; font-size: 11px; font-weight: bold;")
            self.btn_start.setStyleSheet("QPushButton { background-color: #007AFF; color: white; border-radius: 14px; font-size: 13px; font-weight: bold; min-width: 260px; min-height: 56px; padding: 12px; font-family: 'Kantumruy Pro'; } QPushButton:hover { background-color: #0056B3; }")
            self.btn_stop.setStyleSheet("QPushButton { background-color: rgba(255,59,48,0.06); color: #FF3B30; border: 1px solid #FF3B30; border-radius: 14px; font-size: 12px; font-weight: bold; min-width: 260px; min-height: 50px; font-family: 'Kantumruy Pro'; } QPushButton:hover { background-color: #FF3B30; color: white; } QPushButton:disabled { border-color: #D1D5DB; color: #9CA3AF; }")
            if self.menuBar(): self.menuBar().setStyleSheet("QMenuBar { background-color: #FFFFFF; color: #1F2937; font-family: 'Kantumruy Pro'; font-size: 11px; border-bottom: 1px solid #D1D5DB; } QMenuBar::item:selected { background-color: #E6F0FA; color: #007AFF; }")

        self.top_container.setStyleSheet(f"background-color: {widget_bg}; {border_css}")
        self.audio_box.setStyleSheet(f"background-color: {widget_bg}; {border_css}")
        self.out_box.setStyleSheet(f"background-color: {widget_bg}; {border_css}")
        self.console_container.setStyleSheet(f"background-color: {widget_bg}; {border_css}")
        self.time_box.setStyleSheet(f"background-color: {widget_bg}; {border_css}")
        self.res_box.setStyleSheet(f"background-color: {widget_bg}; {border_css}")
        
        self.lbl_seq_title.setStyleSheet(f"color: {sec_header}; font-weight: bold; font-size: 11px;")
        self.lbl_sec_audio.setStyleSheet(f"color: {sec_header}; font-weight: bold; font-size: 11px;")
        self.lbl_sec_time.setStyleSheet(f"color: {sec_header}; font-weight: bold; font-size: 11px;")
        self.lbl_sec_res.setStyleSheet(f"color: {sec_header}; font-weight: bold; font-size: 11px;")
        self.lbl_sec_out.setStyleSheet(f"color: {sec_header}; font-weight: bold; font-size: 11px;")
        self.lbl_sec_con.setStyleSheet(f"color: {sec_header}; font-weight: bold; font-size: 11px;")
        
        self.lbl_bg_score.setStyleSheet(f"color: {text_color}; font-size: 10px;")
        self.lbl_video_title.setStyleSheet(f"color: {text_color}; font-size: 11px;")
        self.lbl_timer_display.setStyleSheet(f"color: {sec_header}; font-size: 11px; font-weight: bold;")

        for txt in self.findChildren(QLineEdit): txt.setStyleSheet(input_css)
        self.btn_m.setStyleSheet(btn_browse)
        self.btn_browse_out.setStyleSheet(btn_browse)

        for drop in self.findChildren(PremiumDropWidget): drop.update_style("normal")
        self.update_active_panel_styles()
        self.action_mute_toggle_changed()

    def update_active_panel_styles(self):
        if not hasattr(self, 'time_box') or not hasattr(self, 'res_box') or not hasattr(self, 'loop_group') or not hasattr(self, 'res_group'):
            return

        if self.is_dark_theme:
            active_style = """
                QRadioButton {
                    color: #00E5FF; font-weight: bold; font-size: 11px; font-family: 'Kantumruy Pro';
                    background-color: rgba(0,229,255,0.08); border: 1px solid #00E5FF; border-radius: 10px; padding: 4px 8px;
                }
                QRadioButton::indicator { width: 10px; height: 10px; border-radius: 6px; border: 2px solid #00E5FF; background-color: white; }
            """
            inactive_style = """
                QRadioButton { color: #9CA3AF; font-size: 11px; font-family: 'Kantumruy Pro'; background-color: transparent; border: 1px solid transparent; padding: 4px 8px; }
                QRadioButton:hover { color: #FFFFFF; background-color: rgba(255,255,255,0.04); border-radius: 10px; }
                QRadioButton::indicator { width: 12px; height: 12px; border-radius: 6px; border: 1.5px solid #374151; background-color: #0B0F19; }
            """
        else:
            active_style = """
                QRadioButton {
                    color: #007AFF; font-weight: bold; font-size: 11px; font-family: 'Kantumruy Pro';
                    background-color: #E6F0FA; border: 1px solid #007AFF; border-radius: 10px; padding: 4px 8px;
                }
                QRadioButton::indicator { width: 10px; height: 10px; border-radius: 6px; border: 2px solid #007AFF; background-color: white; }
            """
            inactive_style = """
                QRadioButton { color: #374151; font-size: 11px; background-color: transparent; border: 1px solid transparent; padding: 4px 8px; font-family: 'Kantumruy Pro'; }
                QRadioButton:hover { color: #111827; background-color: #E5E7EB; border-radius: 10px; }
                QRadioButton::indicator { width: 12px; height: 12px; border-radius: 6px; border: 1.5px solid #9CA3AF; background-color: #FFFFFF; }
            """

        for rb in self.findChildren(QRadioButton):
            if rb.isChecked():
                rb.setStyleSheet(active_style)
            else:
                rb.setStyleSheet(inactive_style)

    def action_mute_toggle_changed(self):
        if not hasattr(self, 'btn_mute_toggle'): return
        is_muted = self.btn_mute_toggle.isChecked()
        if self.is_dark_theme:
            self.btn_mute_toggle.setStyleSheet("background-color: #2A1415; color: #FF453A; border: 1px solid #FF453A; border-radius: 8px;" if is_muted else "background-color: #0B1E14; color: #30D158; border: 1px solid #30D158; border-radius: 8px;")
            self.btn_mute_toggle.setText("ORIGINAL AUDIO: MUTED" if is_muted else "ORIGINAL AUDIO: ON")
        else:
            self.btn_mute_toggle.setStyleSheet("background-color: #FFEBEB; color: #FF3B30; border: 1px solid #FF3B30; border-radius: 8px;" if is_muted else "background-color: #E8F5E9; color: #34C759; border: 1px solid #34C759; border-radius: 8px;")
            self.btn_mute_toggle.setText("ORIGINAL AUDIO: MUTED" if is_muted else "ORIGINAL AUDIO: ON")

    def browse_music(self):
        fp, _ = QFileDialog.getOpenFileName(self, "Load Background Score", "", "Audio Tracks (*.mp3 *.wav)")
        if fp: self.txt_music.setText(os.path.normpath(fp))

    def browse_water(self):
        fp, _ = QFileDialog.getOpenFileName(self, "Load Environmental Audio", "", "Audio Tracks (*.mp3 *.wav)")
        if fp: self.txt_water.setText(os.path.normpath(fp))

    def browse_output_destination(self):
        folder = QFileDialog.getExistingDirectory(self, "Target Routing Output")
        if folder: self.txt_output_path.setText(os.path.normpath(folder))

    def update_elapsed_time(self):
        self.elapsed_time = self.elapsed_time.addSecs(1)
        time_str = self.elapsed_time.toString("hh:mm:ss")
        self.lbl_timer_display.setText(f"ELAPSED TIME: [{time_str}]")

    def start_process(self):
        if not self.input_mid.file_paths:
            self.console_log.append("🛑 [SYSTEM ERROR] TARGET MID-TRACK TIMELINE LANE IS EMPTY.")
            return
            
        time_str = self.txt_custom_time.text() if self.rb_custom.isChecked() else self.loop_group.checkedButton().text()
        try:
            h, m, s = map(int, time_str.split(':'))
            secs = h * 3600 + m * 60 + s
        except Exception:
            secs = 3600
            
        config = {
            'transition_mov': self.input_transition.get_single_path(),
            'mid_list': self.input_mid.file_paths,
            'outro': self.input_outro.get_single_path(),
            'music': self.txt_music.text(),
            'water': self.txt_water.text(),
            'duration_sec': secs,
            'resolution': self.res_group.checkedButton().text(),
            'output_folder': self.txt_output_path.text(),
            'mute_original': self.btn_mute_toggle.isChecked(),
            'video_title': self.txt_video_title.text().strip(),
            'custom_ffmpeg_video': self.custom_ffmpeg_video,
            'custom_ffmpeg_music': self.custom_ffmpeg_music,
            'custom_ffmpeg_mask': self.custom_ffmpeg_mask
        }
        
        self.console_log.clear()
        self.console_log.append("🚀 [SYSTEM_BOOT] BOOTING CODES... ENGAGING COMPILATION PIPELINE.")
        
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        
        self.elapsed_time = QTime(0, 0, 0)
        self.lbl_timer_display.setText("ELAPSED TIME: [00:00:00]")
        self.render_timer.start(1000)
        
        self.worker = FFmpegWorker(config)
        self.worker.progress_signal.connect(self.update_progress)
        self.worker.finished_signal.connect(self.process_finished)
        self.worker.start()

    def stop_process(self):
        if self.worker:
            self.worker.stop()
            self.render_timer.stop()
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)

    def update_progress(self, percent, msg):
        if percent >= 0: self.progress_bar.setValue(percent)
        self.console_log.append(msg)

    def process_finished(self, success, message):
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.render_timer.stop()
        
        if success:
            self.progress_bar.setValue(100)
            total_duration_spent = self.elapsed_time.toString("hh:mm:ss")
            self.console_log.append(f"\n✨ [TERMINAL_SUCCESS] RENDERING PROCESS FINALIZED IN ({total_duration_spent}).")
            self.console_log.append(f"👉 LOCATION EXPORT: {message}")
            try:
                clean_target = os.path.normpath(message)
                os.system(f'explorer /select,"{clean_target}"')
            except Exception: pass
        else:
            self.progress_bar.setValue(0)
            self.console_log.append(f"\n❌ [TERMINAL_CRASH] CORRUPTION DETECTED IN PIPELINE EXPORT: {message}")

if __name__ == "__main__":
    # កំណត់ AppUserModelID ដើម្បីឱ្យ Windows បង្ហាញ Icon ផ្ទាល់ខ្លួននៅលើ Taskbar
    try:
        import ctypes
        myappid = 'akdigital.prostudio.version.1.0' 
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)
    except Exception:
        pass

    app = QApplication(sys.argv)
    
    # កំណត់ពុម្ពអក្សរ Kantumruy Pro 9 ដូចដើមរបស់បង
    font = QFont("Kantumruy Pro", 9)
    app.setFont(font)
    
    window = MainWindow()

    # កំណត់ Icon ឱ្យ Window & Taskbar (icons/icon.png)
    app_icon = get_studio_icon("icon")
    if not app_icon.isNull():
        window.setWindowIcon(app_icon)
        app.setWindowIcon(app_icon)
        
    window.show()
    sys.exit(app.exec())