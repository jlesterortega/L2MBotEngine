"""
Audio fingerprinting for fast sound cue detection.

Uses mel-frequency band energy template matching to detect game sound cues
in ~100-200ms, running alongside Vosk speech recognition as a parallel
detection path.

Templates are extracted from reference audio files and compared against
incoming audio using cosine similarity of spectral energy profiles.
"""

import logging
import os
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

logger = logging.getLogger(__name__)

TARGET_RATE = 16000

N_MEL_BANDS = 32
MEL_FMIN = 80.0     
MEL_FMAX = 4000.0   

TEMPLATE_DURATION_MS = 300    
WINDOW_DURATION_MS = 100      
MATCH_THRESHOLD = 0.70        
COOLDOWN_SECONDS = 2.0        

SAMPLE_DIR_NAME = "audio_sample"
# ✓ FIXED: Removed "an enemy is near" mapping to lock template calculations onto attack audio files exclusively
REFERENCE_FILES = {
    "your character is under attack": "character_under_attack_sample.mp3",
}


def _get_sample_dir() -> Path:
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).parent
        if (exe_dir / SAMPLE_DIR_NAME).exists():
            return exe_dir / SAMPLE_DIR_NAME
        return Path(sys._MEIPASS) / SAMPLE_DIR_NAME
    return Path(__file__).parent / SAMPLE_DIR_NAME


def _hz_to_mel(hz: float) -> float:
    return 2595.0 * np.log10(1.0 + hz / 700.0)


def _mel_to_hz(mel: float) -> float:
    return 700.0 * (10.0 ** (mel / 2595.0) - 1.0)


def _create_mel_filterbank(n_fft: int, sample_rate: int,
                           n_mels: int = N_MEL_BANDS,
                           fmin: float = MEL_FMIN,
                           fmax: float = MEL_FMAX) -> np.ndarray:
    mel_min = _hz_to_mel(fmin)
    mel_max = _hz_to_mel(fmax)
    mel_points = np.linspace(mel_min, mel_max, n_mels + 2)
    hz_points = np.array([_mel_to_hz(m) for m in mel_points])

    n_freqs = n_fft // 2 + 1
    bin_points = np.floor((n_fft + 1) * hz_points / sample_rate).astype(int)
    bin_points = np.clip(bin_points, 0, n_freqs - 1)

    filterbank = np.zeros((n_mels, n_freqs))
    for i in range(n_mels):
        left = bin_points[i]
        center = bin_points[i + 1]
        right = bin_points[i + 2]

        if center > left:
            filterbank[i, left:center] = np.linspace(0, 1, center - left, endpoint=False)
        if right > center:
            filterbank[i, center:right] = np.linspace(1, 0, right - center, endpoint=False)

    return filterbank


def compute_mel_energy(audio: np.ndarray, sample_rate: int,
                       filterbank: np.ndarray, n_fft: int) -> np.ndarray:
    if len(audio) < n_fft:
        audio = np.pad(audio, (0, n_fft - len(audio)))

    window = np.hanning(len(audio[:n_fft]))
    spectrum = np.abs(np.fft.rfft(audio[:n_fft] * window)) ** 2

    mel_energies = filterbank @ spectrum
    mel_energies = np.log(mel_energies + 1e-10)

    return mel_energies


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    norm_a = np.linalg.norm(a)
    norm_b = np.linalg.norm(b)
    if norm_a < 1e-10 or norm_b < 1e-10:
        return 0.0
    return float(np.dot(a, b) / (norm_a * norm_b))


def load_reference_audio(path: Path) -> np.ndarray:
    data, original_rate = sf.read(str(path), dtype='float32')

    if data.ndim > 1:
        data = data.mean(axis=1)

    if original_rate != TARGET_RATE:
        from math import gcd
        g = gcd(int(original_rate), TARGET_RATE)
        up = TARGET_RATE // g
        down = int(original_rate) // g
        data = resample_poly(data, up, down).astype(np.float32)

    return data


def extract_voice_segment(audio: np.ndarray, sample_rate: int,
                          energy_threshold_ratio: float = 0.05,
                          window_ms: int = 50,
                          gap_windows: int = 3) -> np.ndarray:
    window_size = int(sample_rate * window_ms / 1000)
    n_windows = len(audio) // window_size

    energies = np.array([
        np.sqrt(np.mean(audio[i * window_size:(i + 1) * window_size] ** 2))
        for i in range(n_windows)
    ])

    max_energy = energies.max()
    if max_energy < 1e-10:
        return audio  

    threshold = max_energy * energy_threshold_ratio

    segments = []
    in_sound = False
    seg_start = 0
    quiet_count = 0

    for i, e in enumerate(energies):
        if e > threshold:
            quiet_count = 0
            if not in_sound:
                seg_start = i
                in_sound = True
        else:
            if in_sound:
                quiet_count += 1
                if quiet_count >= gap_windows:
                    seg_end = i - gap_windows + 1
                    segments.append((seg_start, seg_end))
                    in_sound = False
                    quiet_count = 0

    if in_sound:
        segments.append((seg_start, n_windows))

    if not segments:
        return audio

    longest = max(segments, key=lambda s: s[1] - s[0])
    start_sample = longest[0] * window_size
    end_sample = longest[1] * window_size

    return audio[start_sample:end_sample]


class AudioFingerprinter:
    def __init__(self, triggers: list[str] | None = None):
        self._n_fft = int(TARGET_RATE * TEMPLATE_DURATION_MS / 1000)
        self._filterbank = _create_mel_filterbank(
            self._n_fft, TARGET_RATE, N_MEL_BANDS, MEL_FMIN, MEL_FMAX)
        self._templates: dict[str, np.ndarray] = {}
        self._last_trigger_time: float = 0.0

        self._window_samples = int(TARGET_RATE * WINDOW_DURATION_MS / 1000)
        self._template_samples = int(TARGET_RATE * TEMPLATE_DURATION_MS / 1000)
        self._buffer = np.zeros(self._template_samples, dtype=np.float32)

        self._load_templates(triggers)
        self._warm_up()

    @property
    def templates_loaded(self) -> int:
        return len(self._templates)

    def _load_templates(self, triggers: list[str] | None) -> None:
        sample_dir = _get_sample_dir()
        if not sample_dir.exists():
            return

        for trigger_phrase, filename in REFERENCE_FILES.items():
            if triggers is not None and trigger_phrase not in triggers:
                continue

            filepath = sample_dir / filename
            if not filepath.exists():
                continue

            try:
                audio = load_reference_audio(filepath)
                voice = extract_voice_segment(audio, TARGET_RATE)

                template_samples = min(self._template_samples, len(voice))
                template_audio = voice[:template_samples]

                template_energy = compute_mel_energy(
                    template_audio, TARGET_RATE, self._filterbank, self._n_fft)

                self._templates[trigger_phrase] = template_energy
            except Exception:
                pass

    def _warm_up(self) -> None:
        synthetic = np.random.randn(self._template_samples).astype(np.float32) * 0.01
        compute_mel_energy(synthetic, TARGET_RATE, self._filterbank, self._n_fft)

    def match(self, audio_chunk: np.ndarray) -> str | None:
        if not self._templates:
            return None

        now = time.time()
        if now - self._last_trigger_time < COOLDOWN_SECONDS:
            return None

        if audio_chunk.dtype == np.int16:
            chunk_float = audio_chunk.astype(np.float32) / 32768.0
        else:
            chunk_float = audio_chunk.astype(np.float32)

        self._buffer = np.concatenate([self._buffer, chunk_float])

        max_buffer = self._template_samples * 2
        if len(self._buffer) > max_buffer:
            self._buffer = self._buffer[-max_buffer:]

        if len(self._buffer) < self._template_samples:
            return None

        window = self._buffer[-self._template_samples:]

        rms = np.sqrt(np.mean(window ** 2))
        if rms < 0.003:  
            return None

        window_energy = compute_mel_energy(
            window, TARGET_RATE, self._filterbank, self._n_fft)

        best_match = None
        best_score = 0.0

        for trigger_phrase, template_energy in self._templates.items():
            score = cosine_similarity(window_energy, template_energy)
            if score > best_score:
                best_score = score
                best_match = trigger_phrase

        if best_score >= MATCH_THRESHOLD and best_match is not None:
            self._last_trigger_time = now
            logger.info('Fingerprint match verified: "%s"', best_match)
            self._buffer = np.zeros(self._template_samples, dtype=np.float32)
            return best_match

        return None

    def reset(self) -> None:
        self._buffer = np.zeros(self._template_samples, dtype=np.float32)
        self._last_trigger_time = 0.0