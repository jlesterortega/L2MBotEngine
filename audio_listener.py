"""
Real-time speech recognition using Vosk for detecting trigger phrases.
Supports WASAPI Loopback capture to intercept game audio from speakers.
Supports per-process audio capture via proctap to isolate a single application.
Supports screen-space pixel monitoring to trigger emergency low-HP escapes.
"""

import json
import logging
import os
import queue
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable
from urllib.request import urlretrieve

import numpy as np
import pyaudiowpatch as pyaudio

logger = logging.getLogger(__name__)

MODEL_URL = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
MODEL_DIR_NAME = "vosk-model-small-en-us-0.15"
TARGET_RATE = 16000

# ── EXCLUSIVITY ATTACK FILTER ──
TRIGGER_PHRASES = [
    "your character is under attack",
]

TARGET_APP_PREFIX = "Lineage2M"

MIN_PARTIAL_WORDS_SYSTEM = 3
MIN_PARTIAL_WORDS_PER_PROCESS = 2
MIN_PARTIAL_WORDS = MIN_PARTIAL_WORDS_SYSTEM

SILENCE_RMS_THRESHOLD = 80
MIN_CONFIDENCE = 0.6


def _get_model_path() -> str:
    import sys
    if getattr(sys, 'frozen', False):
        app_dir = Path(sys._MEIPASS)
    else:
        app_dir = Path(__file__).parent
    model_path = app_dir / MODEL_DIR_NAME
    if model_path.exists():
        return str(model_path)
        
    if getattr(sys, 'frozen', False):
        download_dir = Path(sys.executable).parent
    else:
        download_dir = app_dir
    model_path = download_dir / MODEL_DIR_NAME
    if model_path.exists():
        return str(model_path)

    zip_path = download_dir / f"{MODEL_DIR_NAME}.zip"
    try:
        urlretrieve(MODEL_URL, str(zip_path))
        with zipfile.ZipFile(str(zip_path), 'r') as zf:
            zf.extractall(str(download_dir))
        os.remove(str(zip_path))
    except Exception:
        raise
    return str(model_path)


def _strip_unk(text: str) -> str:
    return " ".join(w for w in text.split() if w != "[unk]")


def find_matched_trigger(text: str, triggers: list[str] | str | None = None, min_words: int = 3) -> str | None:
    if not text or not text.strip():
        return None
        
    text_lower = " ".join(_strip_unk(text).strip().lower().split())
    if not text_lower:
        return None

    # Anti-bleed circuit breaker overrides combined proximity alerts
    if "enemy is near" in text_lower or "enemy is" in text_lower or "territories" in text_lower or "an enemy" in text_lower:
        return None

    if triggers is None:
        triggers = TRIGGER_PHRASES
    elif isinstance(triggers, str):
        triggers = [triggers]
        
    for trigger in triggers:
        trigger_words = trigger.strip().lower().split()
        for i in range(len(trigger_words) - min_words + 1):
            for j in range(i + min_words, len(trigger_words) + 1):
                sub_phrase = " ".join(trigger_words[i:j])
                if f" {sub_phrase} " in f" {text_lower} ":
                    return trigger
    return None


def get_audio_devices() -> list[dict]:
    p = pyaudio.PyAudio()
    devices = []
    seen_indices = set()
    try:
        device_count = p.get_device_count()
        for i in range(device_count):
            try:
                info = p.get_device_info_by_index(i)
                if info.get("maxInputChannels", 0) > 0:
                    name = info["name"]
                    if "[Loopback]" in name:
                        continue
                    if i not in seen_indices:
                        seen_indices.add(i)
                        devices.append({
                            "index": i,
                            "name": f"🎤 {name}",
                            "channels": info["maxInputChannels"],
                            "rate": int(info["defaultSampleRate"]),
                        })
            except Exception:
                continue

        try:
            wasapi_info = p.get_host_api_info_by_type(pyaudio.paWASAPI)
            default_out_idx = wasapi_info.get("defaultOutputDevice", -1)
            default_out_name = ""
            if default_out_idx >= 0:
                try:
                    default_out = p.get_device_info_by_index(default_out_idx)
                    default_out_name = default_out.get("name", "")
                except Exception:
                    pass

            for loopback in p.get_loopback_device_info_generator():
                idx = loopback["index"]
                if idx not in seen_indices:
                    seen_indices.add(idx)
                    is_default = default_out_name and default_out_name in loopback["name"]
                    suffix = " ★" if is_default else ""
                    devices.append({
                        "index": idx,
                        "name": f"🔊 System Audio (Loopback) [{loopback['name']}]{suffix}",
                        "channels": loopback["maxInputChannels"],
                        "rate": int(loopback["defaultSampleRate"]),
                    })
        except Exception:
            pass

    except Exception:
        pass
    finally:
        p.terminate()
    return devices


def get_audio_processes() -> list[dict]:
    import psutil
    import ctypes
    import sys
    
    processes = []
    seen_pids = set()
    titles_map = {}
    
    if sys.platform == "win32":
        try:
            EnumWindows = ctypes.windll.user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int))
            GetWindowText = ctypes.windll.user32.GetWindowTextW
            GetWindowTextLength = ctypes.windll.user32.GetWindowTextLengthW
            IsWindowVisible = ctypes.windll.user32.IsWindowVisible
            GetWindowThreadProcessId = ctypes.windll.user32.GetWindowThreadProcessId

            def foreach_window(hwnd, lParam):
                if IsWindowVisible(hwnd):
                    length = GetWindowTextLength(hwnd)
                    if length > 0:
                        buff = ctypes.create_unicode_buffer(length + 1)
                        GetWindowText(hwnd, buff, length + 1)
                        pid = ctypes.c_uint()
                        GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                        title = buff.value
                        
                        current = titles_map.get(pid.value, "")
                        if len(title) > len(current) and title.lower() not in ('ngptop', 'default ime', 'msctfime ui', 'program manager'):
                            titles_map[pid.value] = title
                return True

            EnumWindows(EnumWindowsProc(foreach_window), 0)
        except Exception:
            pass

    for proc in psutil.process_iter(['pid', 'name']):
        try:
            pid = proc.info['pid']
            name = proc.info['name']
            if not name or pid in seen_pids:
                continue
            if pid <= 4:
                continue
            
            window_title = titles_map.get(pid)
            if not window_title or not window_title.startswith(TARGET_APP_PREFIX):
                continue
                
            seen_pids.add(pid)
            processes.append({
                'pid': pid,
                'name': name,
                'display': f"🎮 {window_title} (PID {pid})",
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue

    processes.sort(key=lambda p: p['display'].lower())
    return processes


class AudioListener:
    def __init__(self, on_trigger: Callable[[str], None],
                 device_index: int | None = None, device_rate: int = 16000, device_channels: int = 1,
                 on_status: Callable[[str], None] | None = None, on_model_progress: Callable[[str], None] | None = None,
                 triggers: list[str] | None = None,
                 process_pid: int | None = None, process_name: str | None = None):
        self._on_trigger = on_trigger
        self._device_index = device_index
        self._device_rate = device_rate
        self._device_channels = device_channels
        self._on_status = on_status or (lambda msg: None)
        self._on_model_progress = on_model_progress or (lambda msg: None)
        self._triggers = triggers or TRIGGER_PHRASES
        self._process_pid = process_pid
        self._process_name = process_name or str(process_pid)
        self._running = False
        self._thread = None
        self._audio_queue = queue.Queue()
        self._recognizer = None
        self._model = None
        self._pyaudio = None
        self._fingerprinter = None
        self._stream = None
        
        if self._process_pid is not None:
            self._min_words = MIN_PARTIAL_WORDS_PER_PROCESS
        else:
            self._min_words = MIN_PARTIAL_WORDS_SYSTEM

    def _report_status(self, message: str) -> None:
        logger.info('%s', message)
        self._on_status(message)

    def _audio_callback(self, in_data, frame_count, time_info, status):
        try:
            data = np.frombuffer(in_data, dtype=np.int16)
            if self._device_channels > 1:
                data = data.reshape(-1, self._device_channels).mean(axis=1).astype(np.int16)
                
            if self._device_rate != TARGET_RATE:
                num_samples = int(len(data) * TARGET_RATE / self._device_rate)
                x = np.arange(num_samples) * (self._device_rate / TARGET_RATE)
                orig_x = np.arange(len(data))
                data = np.interp(x, orig_x, data).astype(np.int16)
                
            self._audio_queue.put(data.tobytes())
            return (in_data, pyaudio.paContinue)
        except Exception as e:
            logger.error("Audio callback error: %s", e)
            return (in_data, pyaudio.paAbort)

    def _load_model(self):
        from vosk import Model, KaldiRecognizer
        self._on_model_progress("Loading Vosk language engine model...")
        model_path = _get_model_path()
        self._model = Model(model_path)
        self._recognizer = KaldiRecognizer(self._model, TARGET_RATE)
        self._recognizer.SetWords(True)

        try:
            from audio_fingerprint import AudioFingerprinter
            self._fingerprinter = AudioFingerprinter(triggers=self._triggers)
        except Exception:
            self._fingerprinter = None

        self._on_model_progress("Warming up sound model graphs...")
        warmup_samples = int(TARGET_RATE * 0.5)
        silent_audio = np.zeros(warmup_samples, dtype=np.int16).tobytes()
        self._recognizer.AcceptWaveform(silent_audio)
        self._recognizer.Result()

    def _listen_loop(self):
        if self._process_pid is not None:
            self._listen_loop_per_process()
        else:
            self._listen_loop_system()

    def _listen_loop_system(self):
        self._pyaudio = pyaudio.PyAudio()
        try:
            self._load_model()
            chunk_duration = 0.1
            kwargs = {
                'format': pyaudio.paInt16,
                'channels': self._device_channels,
                'rate': self._device_rate,
                'input': True,
                'frames_per_buffer': int(self._device_rate * chunk_duration),
                'stream_callback': self._audio_callback
            }
            if self._device_index is not None:
                kwargs['input_device_index'] = self._device_index
                
            self._stream = self._pyaudio.open(**kwargs)
            self._report_status(f"Listening on System Audio loopback channel index: {self._device_index}...")

            self._process_audio_queue()
        except Exception as e:
            self._report_status(f"Stream error: {e}")
        finally:
            if self._stream:
                try: self._stream.stop_stream(); self._stream.close()
                except Exception: pass
                self._stream = None
            if self._pyaudio: self._pyaudio.terminate()
            self._running = False

    def _listen_loop_per_process(self):
        try:
            from proctap import ProcessAudioCapture
        except ImportError:
            self._report_status("❌ Error: proctap hook module package unmapped.")
            self._running = False
            return

        try:
            self._load_model()
            self._report_status(f"Hooking proctap layer onto target client account PID {self._process_pid}...")
            capture = ProcessAudioCapture(pid=self._process_pid, resample_quality='fast')
            capture.start()
            self._report_status(f"Listening explicitly to App Instance audio canvas stream...")

            proctap_rate = 48000
            proctap_channels = 2

            while self._running:
                raw = capture.read(timeout=0.5)
                if raw is None: continue

                float_data = np.frombuffer(raw, dtype=np.float32)
                float_data = np.clip(float_data, -1.0, 1.0)
                int_data = (float_data * 32767).astype(np.int16)

                if proctap_channels > 1:
                    int_data = int_data.reshape(-1, proctap_channels).mean(axis=1).astype(np.int16)

                num_samples = int(len(int_data) * TARGET_RATE / proctap_rate)
                if num_samples == 0: continue
                x = np.arange(num_samples) * (proctap_rate / TARGET_RATE)
                orig_x = np.arange(len(int_data))
                resampled = np.interp(x, orig_x, int_data).astype(np.int16)

                rms = np.sqrt(np.mean(resampled.astype(np.float64) ** 2))
                if rms < SILENCE_RMS_THRESHOLD: continue

                if self._fingerprinter is not None:
                    fp_match = self._fingerprinter.match(resampled)
                    if fp_match:
                        self._report_status(f'TRIGGERED (Fingerprint verification): "{fp_match}"')
                        self._on_trigger(fp_match)
                        continue

                data_bytes = resampled.tobytes()
                if self._recognizer.AcceptWaveform(data_bytes):
                    result = json.loads(self._recognizer.Result())
                    text = result.get("text", "")
                    if text: self._check_trigger(text, is_partial=False, result_json=result)
                else:
                    partial = json.loads(self._recognizer.PartialResult())
                    text = partial.get("partial", "")
                    if text: self._check_trigger(text, is_partial=True)

            capture.stop()
            capture.close()
        except Exception as e:
            self._report_status(f"Process hook engine fault: {e}")
        finally:
            self._running = False
            self._report_status("Stopped listening.")

    def _process_audio_queue(self):
        while self._running:
            try: data = self._audio_queue.get(timeout=0.5)
            except queue.Empty: continue

            samples = np.frombuffer(data, dtype=np.int16)
            rms = np.sqrt(np.mean(samples.astype(np.float64) ** 2))
            if rms < SILENCE_RMS_THRESHOLD:
                continue

            if self._fingerprinter is not None:
                fp_match = self._fingerprinter.match(samples)
                if fp_match:
                    self._report_status(f'TRIGGERED (Fingerprint verification): "{fp_match}"')
                    self._on_trigger(fp_match)
                    continue

            if self._recognizer.AcceptWaveform(data):
                result = json.loads(self._recognizer.Result())
                text = result.get("text", "")
                if text: self._check_trigger(text, is_partial=False, result_json=result)
            else:
                partial = json.loads(self._recognizer.PartialResult())
                text = partial.get("partial", "")
                if text: self._check_trigger(text, is_partial=True)

    def _check_trigger(self, text: str, is_partial: bool, result_json: dict | None = None) -> None:
        # ── FIX: Ignore partial triggers to prevent spam ──
        if is_partial:
            return

        matched = find_matched_trigger(text, triggers=self._triggers, min_words=self._min_words)
        if not matched: return

        # ... (rest of the function remains the same)
        label = "final"
        self._report_status(f'TRIGGERED ({label}): "{text}"')
        self._on_trigger(matched)

    def start(self) -> None:
        self._running = True
        self._audio_queue = queue.Queue()
        self._thread = threading.Thread(target=self._listen_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._stream:
            try: self._stream.stop_stream(); self._stream.close()
            except Exception: pass
            self._stream = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)
        self._thread = None


# ── NATIVE BACKGROUND PIXEL HEALTH MONITOR THREAD ──
class HPMonitorThread(threading.Thread):
    def __init__(self, check_x: int, check_y: int, on_trigger_callback: Callable[[str], None], status_callback: Callable[[str], None]):
        super().__init__(daemon=True)
        self.check_x = check_x
        self.check_y = check_y
        self.on_trigger = on_trigger_callback
        self.report_status = status_callback
        self.running = False

    def run(self):
        import mss
        from PIL import Image
        
        self.running = True
        self.report_status(f"🩸 HP Fallback Scanner Active: Watching pixel position ({self.check_x}, {self.check_y})")
        
        monitor_zone = {"top": self.check_y, "left": self.check_x, "width": 1, "height": 1}
        
        with mss.mss() as sct:
            while self.running:
                try:
                    screenshot = sct.grab(monitor_zone)
                    img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
                    r, g, b = img.getpixel((0, 0))
                    
                    # Lineage2M HP bar uses a pure solid crimson red.
                    # If Red intensity drops below threshold (turns grey/black), fire trigger sequence
                    if r < 130 or (g > 50 and b > 50):
                        self.report_status(f"🚨 LOW-HP FALLBACK ENGAGED! Color dropped to: RGB({r}, {g}, {b})")
                        self.on_trigger("hp_critical_drop")
                        time.sleep(5.0)  # Cooldown prevents spamming macros while loading safe-zones
                        
                except Exception as e:
                    logger.error(f"HP pixel grab error: {e}")
                    
                time.sleep(0.2)  # Samples 5 times per second for instant reactions at 0% CPU

    def stop(self):
        self.running = False