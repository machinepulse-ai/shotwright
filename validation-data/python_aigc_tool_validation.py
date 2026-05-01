from __future__ import annotations

import json
import math
import os
from pathlib import Path

import cv2
import insightface
import librosa
import numpy as np
import onnx
import onnxruntime as ort
import soundfile as sf
import whisper
from moviepy import VideoClip
from onnx import TensorProto, helper
from PIL import Image, ImageDraw


work = Path(os.environ["SHOTWRIGHT_WORK_DIR"])
assets = work / "assets" / "python"
assets.mkdir(parents=True, exist_ok=True)
summary: dict[str, object] = {"work_dir": str(work), "cases": []}
cases: list[dict[str, object]] = summary["cases"]  # type: ignore[assignment]


def record(name: str, **data: object) -> None:
    item = {"name": name, "ok": True}
    item.update(data)
    cases.append(item)


# 1. Synthetic audio plus librosa feature extraction.
sr = 16000
duration = 3.0
t = np.linspace(0, duration, int(sr * duration), endpoint=False, dtype=np.float32)
carrier = 0.42 * np.sin(2 * np.pi * (220 + 120 * t / duration) * t)
beat = (0.5 + 0.5 * np.sin(2 * np.pi * 2.0 * t)).astype(np.float32)
audio = (carrier * beat + 0.06 * np.sin(2 * np.pi * 880 * t)).astype(np.float32)
audio_path = assets / "synthetic_voice_bed.wav"
sf.write(audio_path, audio, sr)
rms = librosa.feature.rms(y=audio, frame_length=1024, hop_length=256)[0]
centroid = librosa.feature.spectral_centroid(y=audio, sr=sr)[0]
record(
    "audio_librosa_features",
    path=str(audio_path),
    rms_mean=float(np.mean(rms)),
    centroid_mean=float(np.mean(centroid)),
)

# 2. Whisper signal preparation without downloading a model.
mel = whisper.log_mel_spectrogram(whisper.pad_or_trim(audio)).detach().cpu().numpy()
mel_norm = mel[:80, :180]
mel_norm = (255 * (mel_norm - mel_norm.min()) / max(1e-6, mel_norm.max() - mel_norm.min())).astype(np.uint8)
mel_image_path = assets / "whisper_mel.png"
Image.fromarray(mel_norm).resize((720, 320)).save(mel_image_path)
record("whisper_mel_pipeline", path=str(mel_image_path), mel_shape=list(mel.shape), mel_mean=float(np.mean(mel)))

# 3. OpenCV synthetic motion video plus optical flow.
video_path = assets / "opencv_motion.mp4"
fourcc = cv2.VideoWriter_fourcc(*"mp4v")
writer = cv2.VideoWriter(str(video_path), fourcc, 24, (640, 360))
if not writer.isOpened():
    raise RuntimeError("OpenCV VideoWriter did not open")

prev_gray = None
flow_values: list[float] = []
last_frame = None
for i in range(72):
    frame = np.zeros((360, 640, 3), dtype=np.uint8)
    frame[:, :] = (12, 18 + i % 40, 34)
    x = int(60 + i * 7.2)
    y = int(180 + 70 * math.sin(i / 9.0))
    cv2.circle(frame, (x % 700 - 30, y), 42, (70, 220, 255), -1, cv2.LINE_AA)
    cv2.rectangle(frame, (420 - i * 2 % 180, 70), (610 - i * 2 % 180, 130), (245, 95, 220), -1, cv2.LINE_AA)
    cv2.putText(frame, "PYTHON AIGC", (34, 318), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (230, 245, 255), 2, cv2.LINE_AA)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    if prev_gray is not None:
        flow = cv2.calcOpticalFlowFarneback(prev_gray, gray, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        flow_values.append(float(np.linalg.norm(flow, axis=2).mean()))
    prev_gray = gray
    last_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    writer.write(frame)
writer.release()
record("opencv_video_optical_flow", path=str(video_path), mean_flow=float(np.mean(flow_values)), frames=72)

# 4. MoviePy procedural video render.
def frame_function(time_value: float) -> np.ndarray:
    w, h = 480, 270
    yy, xx = np.mgrid[0:h, 0:w]
    pulse = (np.sin((xx * 0.035) + time_value * 5.0) + np.cos((yy * 0.05) - time_value * 4.0)) * 0.5 + 0.5
    frame = np.zeros((h, w, 3), dtype=np.uint8)
    frame[..., 0] = np.clip(30 + pulse * 160, 0, 255)
    frame[..., 1] = np.clip(80 + (1 - pulse) * 120, 0, 255)
    frame[..., 2] = np.clip(140 + pulse * 80, 0, 255)
    return frame


moviepy_path = assets / "moviepy_procedural.mp4"
clip = VideoClip(frame_function=frame_function, duration=1.5)
clip.write_videofile(str(moviepy_path), fps=18, codec="libx264", audio=False, logger=None)
record("moviepy_procedural_video", path=str(moviepy_path), duration_seconds=1.5, fps=18)

# 5. ONNXRuntime CPU inference.
x = helper.make_tensor_value_info("x", TensorProto.FLOAT, [None, 4])
y = helper.make_tensor_value_info("y", TensorProto.FLOAT, [None, 4])
node = helper.make_node("Relu", ["x"], ["y"])
graph = helper.make_graph([node], "shotwright_relu_graph", [x], [y])
model = helper.make_model(
    graph,
    producer_name="shotwright-validation",
    opset_imports=[helper.make_operatorsetid("", 13)],
)
model.ir_version = 7
onnx_path = assets / "relu_identity.onnx"
onnx.save(model, onnx_path)
sess = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
onnx_out = sess.run(None, {"x": np.array([[-1, 0.25, 2, -3]], dtype=np.float32)})[0]
record("onnxruntime_cpu_inference", path=str(onnx_path), output=onnx_out.tolist())

# 6. InsightFace package availability. Model download is intentionally skipped.
record("insightface_import_ready", version=getattr(insightface, "__version__", "0.2.1"), model_download="skipped")

# Composite dashboard image for AE import.
hero_path = assets / "python_aigc_dashboard.png"
hero = Image.new("RGB", (1280, 720), (8, 12, 22))
draw = ImageDraw.Draw(hero)
if last_frame is not None:
    frame_img = Image.fromarray(last_frame).resize((560, 315))
    hero.paste(frame_img, (660, 70))
mel_img = Image.open(mel_image_path).convert("RGB").resize((560, 250))
hero.paste(mel_img, (70, 360))
draw.rectangle((48, 48, 1232, 672), outline=(64, 230, 255), width=3)
draw.text((70, 76), "Shotwright Python AIGC Toolchain", fill=(235, 250, 255))
draw.text((70, 122), "numpy / opencv / librosa / whisper / onnxruntime / insightface", fill=(145, 220, 235))
y_pos = 176
for item in cases:
    draw.text((90, y_pos), f"[ok] {item['name']}", fill=(180, 255, 205))
    y_pos += 34
hero.save(hero_path, quality=94)
record("pillow_composite_dashboard", path=str(hero_path), size=[1280, 720])

summary_path = assets / "validation_summary.json"
summary["asset_paths"] = {
    "audio": str(audio_path),
    "mel_image": str(mel_image_path),
    "opencv_video": str(video_path),
    "moviepy_video": str(moviepy_path),
    "dashboard": str(hero_path),
}
summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
print(json.dumps({"summary_path": str(summary_path), "case_count": len(cases), "asset_paths": summary["asset_paths"]}))
