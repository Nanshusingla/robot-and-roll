#!/usr/bin/env python3
"""Robot & Roll LAN demo: USB camera, L298N, JBL speaker, duplex audio.

Raspberry Pi OS Trixie, Python 3.13 system packages. Designed for a private LAN.
Never expose HTTP 5000 / WebSocket 8765 directly to the Internet.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import threading
import time
from urllib.parse import unquote

import cv2
from flask import Flask, Response, jsonify, request
from gpiozero import DigitalOutputDevice
from websockets.asyncio.server import serve

ROOT = Path(__file__).resolve().parent.parent
MUSIC = ROOT / "music"
MUSIC.mkdir(exist_ok=True)
KEY_FILE = ROOT / ".access_key"

# Use a persistent random access key; new process starts retain the same key.
def access_key():
    custom = os.getenv("ROBOT_KEY", "").strip()
    if custom:
        return custom
    if KEY_FILE.exists():
        return KEY_FILE.read_text().strip()
    key = secrets.token_urlsafe(24)
    fd = os.open(str(KEY_FILE), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    return key

KEY = access_key()
VIDEO_DEVICE = os.getenv("VIDEO_DEVICE", "/dev/video0")
VIDEO_WIDTH = int(os.getenv("VIDEO_WIDTH", "640"))
VIDEO_HEIGHT = int(os.getenv("VIDEO_HEIGHT", "480"))
VIDEO_FPS = int(os.getenv("VIDEO_FPS", "15"))
# Change to -1 if a motor's positive polarity actually moves the robot backward.
LEFT_FORWARD_SIGN = int(os.getenv("LEFT_FORWARD_SIGN", "1"))
RIGHT_FORWARD_SIGN = int(os.getenv("RIGHT_FORWARD_SIGN", "1"))
ALLOWED_ORIGINS = {"http://localhost:8081", "http://127.0.0.1:8081"}


class L298N:
    """GPIO Zero BCM pins for physical Pi pins 16,18,29,31.

    ENA/ENB jumpers installed, so PWM/ENA/ENB GPIO pins are not used.
    """
    def __init__(self):
        self.in1 = DigitalOutputDevice(23, initial_value=False)
        self.in2 = DigitalOutputDevice(24, initial_value=False)
        self.in3 = DigitalOutputDevice(5, initial_value=False)
        self.in4 = DigitalOutputDevice(6, initial_value=False)
        self.lock = threading.RLock()
        self.last_command = 0.0
        self.current = (0, 0)
        self.running = True
        threading.Thread(target=self.watchdog, daemon=True).start()

    def _all_low(self):
        self.in1.off()
        self.in2.off()
        self.in3.off()
        self.in4.off()

    def move(self, left: int, right: int):
        if left not in (-1, 0, 1) or right not in (-1, 0, 1):
            raise ValueError("Direction must be -1, 0, or 1")
        with self.lock:
            self.last_command = time.monotonic()
            if self.current == (left, right):
                return
            self._all_low()
            # Avoid abruptly reversing a motor without first removing drive.
            time.sleep(0.035)
            if left == 1:
                self.in1.on()
            elif left == -1:
                self.in2.on()
            if right == 1:
                self.in3.on()
            elif right == -1:
                self.in4.on()
            self.current = (left, right)

    def stop(self):
        with self.lock:
            self._all_low()
            self.current = (0, 0)
            self.last_command = 0.0

    def watchdog(self):
        while self.running:
            with self.lock:
                if self.current != (0, 0) and time.monotonic() - self.last_command > 0.55:
                    self._all_low()
                    self.current = (0, 0)
                    self.last_command = 0.0
            time.sleep(0.06)

    def close(self):
        self.running = False
        self.stop()
        for pin in (self.in1, self.in2, self.in3, self.in4):
            pin.close()


motors = L298N()


class USBVideo:
    def __init__(self):
        self.condition = threading.Condition()
        self.last_jpeg = None
        self.sequence = 0
        self.last_frame = 0.0
        self.running = True
        threading.Thread(target=self.capture, daemon=True).start()

    def capture(self):
        while self.running:
            cam = cv2.VideoCapture(VIDEO_DEVICE, cv2.CAP_V4L2)
            if not cam.isOpened():
                print(f"[camera] Cannot open {VIDEO_DEVICE}; retrying", flush=True)
                cam.release()
                time.sleep(2)
                continue
            cam.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
            cam.set(cv2.CAP_PROP_FRAME_WIDTH, VIDEO_WIDTH)
            cam.set(cv2.CAP_PROP_FRAME_HEIGHT, VIDEO_HEIGHT)
            cam.set(cv2.CAP_PROP_FPS, VIDEO_FPS)
            print(f"[camera] Opened {VIDEO_DEVICE}", flush=True)
            misses = 0
            while self.running:
                ok, frame = cam.read()
                if not ok:
                    misses += 1
                    if misses > 15:
                        break
                    time.sleep(0.06)
                    continue
                misses = 0
                ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 67])
                if ok:
                    with self.condition:
                        self.last_jpeg = encoded.tobytes()
                        self.sequence += 1
                        self.last_frame = time.monotonic()
                        self.condition.notify_all()
            cam.release()
            time.sleep(1)

    def frames(self):
        previous = -1
        while self.running:
            with self.condition:
                self.condition.wait_for(lambda: self.sequence != previous, timeout=3)
                if self.sequence == previous or self.last_jpeg is None:
                    continue
                jpeg = self.last_jpeg
                previous = self.sequence
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpeg + b"\r\n"


camera = USBVideo()
app = Flask(__name__)


@app.after_request
def cors(resp):
    origin = request.headers.get("Origin")
    if origin in ALLOWED_ORIGINS:
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Robot-Key"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def authorized():
    submitted = request.headers.get("X-Robot-Key") or request.args.get("key", "")
    return hmac.compare_digest(submitted, KEY)


@app.before_request
def check_key():
    if request.method == "OPTIONS":
        return Response(status=204)
    if not authorized():
        return jsonify(error="Access key incorrect"), 401


@app.get("/api/health")
def health():
    return jsonify(ok=True, camera=camera.last_jpeg is not None and
                   (time.monotonic() - camera.last_frame < 3),
                   motors_ready=True, speaker="Use wpctl status to verify JBL",
                   video_device=VIDEO_DEVICE)


@app.get("/video")
def video():
    return Response(camera.frames(), mimetype="multipart/x-mixed-replace; boundary=frame",
                    headers={"X-Accel-Buffering": "no"})


@app.post("/api/motor")
def motor_control():
    payload = request.get_json(silent=True) or {}
    try:
        left = int(payload.get("left", 0))
        right = int(payload.get("right", 0))
        # Named forward/back/turn commands send logical directions.
        # Raw positive/negative test commands can opt out of polarity mapping.
        if not payload.get("raw", False):
            left *= LEFT_FORWARD_SIGN
            right *= RIGHT_FORWARD_SIGN
        if (left, right) == (0, 0):
            motors.stop()
        else:
            motors.move(left, right)
        return jsonify(ok=True, left=left, right=right)
    except (ValueError, TypeError):
        return jsonify(error="Invalid motor command"), 400


# Save audio files of your own in music/. No music is bundled.
MUSIC_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".aac"}
play_lock = threading.RLock()
playing_music = None   # (ffmpeg process, pw-play process)


def stop_music():
    global playing_music
    with play_lock:
        procs = playing_music
        playing_music = None
        if procs:
            for proc in reversed(procs):
                if proc.poll() is None:
                    proc.terminate()
            for proc in procs:
                try:
                    proc.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    proc.kill()


def play_music(path):
    global playing_music
    stop_music()
    with play_lock:
        decoder = subprocess.Popen(
            ["ffmpeg", "-nostdin", "-loglevel", "error", "-i", str(path),
             "-vn", "-f", "s16le", "-acodec", "pcm_s16le", "-ar", "48000", "-ac", "2", "-"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        player = subprocess.Popen(
            ["pw-play","--raw", "--rate=48000", "--channels=2", "--format=s16", "-"],
            stdin=decoder.stdout, stderr=subprocess.DEVNULL)
        decoder.stdout.close()
        playing_music = (decoder, player)


@app.get("/api/music")
def music_list():
    return jsonify(tracks=sorted([p.name for p in MUSIC.iterdir()
                                  if p.is_file() and p.suffix.lower() in MUSIC_EXTS]))


@app.post("/api/music/play")
def music_play():
    payload = request.get_json(silent=True) or {}
    name = payload.get("name", "")
    if not isinstance(name, str) or not name or Path(name).name != name:
        return jsonify(error="Invalid filename"), 400
    path = MUSIC / name
    if not path.is_file() or path.is_symlink() or path.suffix.lower() not in MUSIC_EXTS:
        return jsonify(error="Music file not found"), 404
    try:
        play_music(path)
    except (FileNotFoundError, OSError) as exc:
        return jsonify(error=f"Cannot start music player: {exc}"), 500
    return jsonify(ok=True, track=name)


@app.post("/api/music/stop")
def music_stop():
    stop_music()
    return jsonify(ok=True)


def say_async(text):
    # Generate a small WAV then play it through the currently selected sink.
    fd, filename = tempfile.mkstemp(prefix="robot-roll-", suffix=".wav")
    os.close(fd)
    try:
        subprocess.run(["espeak-ng", "-w", filename, text], check=True, timeout=20,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["pw-play", filename], check=True, timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        print(f"[speech] {exc}", flush=True)
    finally:
        Path(filename).unlink(missing_ok=True)


@app.post("/api/speak")
def speak():
    payload = request.get_json(silent=True) or {}
    text = payload.get("text", "")
    if not isinstance(text, str) or not text.strip() or len(text) > 300:
        return jsonify(error="Text must be 1–300 characters"), 400
    threading.Thread(target=say_async, args=(text.strip(),), daemon=True).start()
    return jsonify(ok=True)


# Duplex audio: 16kHz mono signed 16-bit little endian PCM over WebSocket.
# The Pi records from the PipeWire default source (Camera Mono), and plays
# on the PipeWire default sink (JBL GO 2 once selected with wpctl).
AUDIO_RATE = 16000
AUDIO_FRAME = 640  # ~20ms
active_audio_lock = asyncio.Lock()


async def audio_handler(ws):
    try:
        raw = await asyncio.wait_for(ws.recv(), timeout=8)
        hello = json.loads(raw)
        if not isinstance(hello, dict) or not hmac.compare_digest(str(hello.get("key", "")), KEY):
            await ws.close(code=1008, reason="Invalid key")
            return
        if active_audio_lock.locked():
            await ws.close(code=1013, reason="Another call is active")
            return
    except Exception:
        await ws.close(code=1008, reason="Authentication required")
        return

    async with active_audio_lock:

        sender_task = None
        listening = True
        talking = True
        try:
            recorder = await asyncio.create_subprocess_exec(
                "pw-record","--raw", "--rate=16000", "--channels=1", "--format=s16", "--latency=80ms", "-",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            player = await asyncio.create_subprocess_exec(
                "pw-play","--raw", "--rate=16000", "--channels=1", "--format=s16", "--latency=80ms", "-",
                stdin=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
            await ws.send(json.dumps({"type": "ready", "rate": AUDIO_RATE}))

            async def send_robot_microphone():
                while True:
                    chunk = await recorder.stdout.read(AUDIO_FRAME)
                    if not chunk:
                        break
                    if listening:
                        await ws.send(chunk)

            sender_task = asyncio.create_task(send_robot_microphone())
            async for message in ws:
                if isinstance(message, bytes):
                    if talking and player.returncode is None and len(message) <= 65536:
                        player.stdin.write(message)
                        await player.stdin.drain()
                else:
                    try:
                        cmd = json.loads(message)
                        if cmd.get("type") == "listen":
                            listening = bool(cmd.get("enabled"))
                        elif cmd.get("type") == "talk":
                            talking = bool(cmd.get("enabled"))
                    except (ValueError, AttributeError):
                        pass
        except Exception as exc:
            print(f"[audio] Call ended: {exc}", flush=True)
        finally:
            if sender_task:
                sender_task.cancel()
                await asyncio.gather(sender_task, return_exceptions=True)
            for proc in (recorder, player):
                if proc and proc.returncode is None:
                    try:
                        proc.terminate()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=2)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()


def run_http():
    app.run(host="0.0.0.0", port=5000, threaded=True, use_reloader=False)


async def main():
    threading.Thread(target=run_http, daemon=True).start()
    print("\n=== Robot & Roll: integrated Raspberry Pi server ===", flush=True)
    print("Webcam: " + VIDEO_DEVICE, flush=True)
    print("HTTP video and motor API: port 5000", flush=True)
    print("Two-way voice: WebSocket port 8765", flush=True)
    print("Connection key: " + KEY, flush=True)
    print("Verify JBL is selected: wpctl status", flush=True)
    print("Privacy: demo is for a trusted local Wi-Fi network only.\n", flush=True)
    async with serve(audio_handler, "0.0.0.0", 8765, max_size=65536,
                     compression=None, ping_interval=15, ping_timeout=15):
        await asyncio.Future()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    finally:
        motors.close()
        stop_music()
        camera.running = False
        print("Stopped motors and music; server exited.", flush=True)
