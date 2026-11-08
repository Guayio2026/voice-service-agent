"""Optional local Smart Turn inference. Missing weights keep lexical endpointing active.

Algorithm follows https://github.com/pipecat-ai/smart-turn/blob/main/inference.py
Weights: pipecat-ai/smart-turn-v3, revision f766f81, smart-turn-v3.2-cpu.onnx (BSD-2-Clause).
"""
from pathlib import Path
import time
import logging
from functools import lru_cache

class TurnEndpoint:
    def __init__(self, path):
        import numpy as np
        import onnxruntime as ort
        from transformers import WhisperFeatureExtractor
        self.np = np
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(str(path), sess_options=options, providers=['CPUExecutionProvider'])
        self.extractor = WhisperFeatureExtractor(chunk_length=8)

    def predict(self, pcm):
        started = time.perf_counter()
        np = self.np
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        audio = audio[-128000:]
        if len(audio) < 128000:
            audio = np.pad(audio, (128000-len(audio), 0))
        features = self.extractor(audio, sampling_rate=16000, return_tensors='np',
            padding='max_length', max_length=128000, truncation=True, do_normalize=True).input_features
        probability = float(self.session.run(None, {'input_features':features.astype(np.float32)})[0].reshape(-1)[0])
        return {'complete': probability >= .9, 'probability': probability,
                'inference_ms': round((time.perf_counter()-started)*1000, 1)}

@lru_cache(maxsize=1)
def load_endpoint(root):
    path = Path(root) / 'runtime/models/smart-turn-v3.2-cpu.onnx'
    if not path.exists():
        return None
    try:
        model = TurnEndpoint(path)
        model.predict(bytes(32000))  # warm feature extractor / ONNX before serving
        return model
    except Exception as error:
        logging.getLogger(__name__).warning('Smart Turn unavailable: %s; lexical fallback active', type(error).__name__)
        return None
