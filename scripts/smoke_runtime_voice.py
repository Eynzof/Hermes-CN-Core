"""Exercise packaged local STT dependencies and Silero VAD without downloads."""
import numpy as np
import onnxruntime
import ctranslate2
from faster_whisper.vad import get_speech_timestamps

assert "CPUExecutionProvider" in onnxruntime.get_available_providers()
assert "float32" in ctranslate2.get_supported_compute_types("cpu")
segments = get_speech_timestamps(np.zeros(16000, dtype=np.float32), sampling_rate=16000)
assert segments == [], f"Silence unexpectedly contains speech: {segments}"
print(f"Local STT dependencies and Silero VAD OK: ONNX Runtime {onnxruntime.__version__}")
