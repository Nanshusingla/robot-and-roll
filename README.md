# Robot & Roll — Unified Video-Call Robot (LAN prototype)

A Raspberry Pi 4 runs the Uokier USB webcam, L298N two-motor direction control, the Uokier webcam microphone, and a JBL GO 2 Bluetooth speaker. A web dashboard opened **locally on your Mac** shows the live robot camera, drives the motors, allows text-to-speech, plays your own songs, and runs two-way live audio.

## Capabilities

1. **Camera to Mac:** `/dev/video0` → OpenCV MJPEG → dashboard browser.
2. **Motors:** L298N two independently controlled DC motors: positive, negative, both, forward, backward, left, right (if actual chassis supports differential steering). The buttons must be held; the Pi stops the motors if messages stop arriving for 0.55 sec.
3. **TTS:** Type into the dashboard → `espeak-ng` → selected PipeWire speaker.
4. **Mac voice → Robot speaker:** Mac browser mic → WebSocket PCM → `pw-play` → JBL GO 2.
5. **Music:** Choose your own file from the Pi's `music/` folder → `ffmpeg` → `pw-play` → JBL GO 2.
6. **Robot mic → Mac:** Uokier webcam mic (default PipeWire source) → `pw-record` → WebSocket PCM → browser speakers.

**This is a proof-of-concept telepresence video call:** one-way robot video + bidirectional voice. It does not stream the Mac's *video* to the robot (the robot has no display), provide encryption, or work across the public Internet. Audio latency varies with Wi-Fi and Bluetooth; no echo cancellation is performed on the Pi's speaker-to-webcam-mic path.

## Hardware pins — already confirmed physical Pi header pins

| L298N | Physical pin | GPIO Zero BCM | Purpose |
|:--|--:|--:|:--|
| IN1 | 16 | GPIO23 | Motor A positive |
| IN2 | 18 | GPIO24 | Motor A negative |
| IN3 | 29 | GPIO5 | Motor B positive |
| IN4 | 31 | GPIO6 | Motor B negative |

Leave L298N **ENA and ENB jumpers installed** for this full-speed demo. **Do not connect Pi GPIO wires to ENA/ENB while the jumpers are installed.** Motors need an appropriate independent battery supply. The L298N and Pi must share a ground. Turn **off all power** before touching wires. Physically lift the wheels for initial testing. If the motors drive front/rear axles instead of left/right wheel groups, left/right commands will not produce proper differential steering without drivetrain changes. Software “forward” is based on polarity and may need `LEFT_FORWARD_SIGN` or `RIGHT_FORWARD_SIGN` adjusted to -1 in the Pi service: see troubleshooting. (The current code uses direct raw positive/negative, and default forward assumes both positive.)

## Setup — Mac and Raspberry Pi

### On the Pi (one time)

Connect by SSH:

```bash
ssh robot@hackumass-robot.local
```

Install system packages (Trixie system Python):

```bash
sudo apt update
sudo apt install -y python3-flask python3-opencv python3-gpiozero python3-websockets python3-lgpio \
  pipewire-bin wireplumber bluez ffmpeg espeak-ng alsa-utils
```

**Stop the old camera and motor programs** first. Exit their original SSH terminals with Ctrl+C. Do not let two motor programs control the same GPIO pins, or let FFmpeg/VLC and OpenCV open `/dev/video0` at once.

Check for leftovers without blindly killing unrelated Python processes:

```bash
pgrep -af 'server.py|motor_l298n.py|ffmpeg|rpicam'
```

If necessary, return to the original terminals and stop only those old processes with Ctrl+C.

Make sure the **JBL GO 2** is connected and set as the default **sink**, and `Camera Mono` is the default **source**:

```bash
bluetoothctl connect 04:CB:88:E0:5C:3E
wpctl status
```

If JBL is listed as sink 84, for example:

```bash
wpctl set-default 84
wpctl set-volume 84 0.35
```

Use the ID you actually see; it can change. If the JBL is not available, power it on and re-pair using `bluetoothctl` as before. For the webcam, `wpctl status` should show `* Camera Mono` under Audio → Sources; if not, use `wpctl set-default <camera-source-ID>`.

### Copy files from your Mac

Extract this ZIP into Downloads, then on your **Mac terminal** (not SSH):

```bash
scp -r ~/Downloads/robot_and_roll_unified robot@hackumass-robot.local:~/
```

On the **Pi SSH terminal**, run:

```bash
cd ~/robot_and_roll_unified/pi
python3 server.py
```

Keep this terminal running. It prints your randomly generated **connection key** on start, plus Pi ports **5000** (web/video/control) and **8765** (duplex voice).

Do not also run your old `~/backend/server.py`, the standalone `motor_l298n.py`, or the prior camera `ffmpeg` stream while this server runs.

### On your Mac

Open a **new Mac terminal** (not SSH) and run:

```bash
cd ~/Downloads/robot_and_roll_unified/mac
python3 -m http.server 8080
```

In **Chrome** or Safari on your Mac open:

**http://localhost:8080**

Enter `hackumass-robot.local` and the access key shown in Pi terminal, then click **Connect dashboard**.

For voice calls, click **Start voice call** and allow browser microphone access. Audio streams in **both** directions. Buttons **Mute my mic** and **Mute robot audio** can prevent feedback. **End call** releases the mic. Don't test with the JBL right beside your Mac or the webcam mic pointed at the JBL.

### Play your own songs

Place an audio file you own or are permitted to use in the Pi's music folder, for example from the **Mac terminal**:

```bash
scp ~/Downloads/my-song.mp3 robot@hackumass-robot.local:~/robot_and_roll_unified/music/
```

Click **Refresh tracks**, select the track, and click **Play**. Supported extensions: MP3, WAV, OGG, FLAC, M4A, AAC (FFmpeg decoding). No music files or copyrighted recordings are included in the project. Press **Stop** to stop music.

## Safety and limitations

- **Use a trusted personal Wi-Fi hotspot only.** Access key limits casual unauthorized connections but this prototype uses unencrypted HTTP and `ws://`, which means video and microphone audio could be intercepted on the LAN. Do **not** port-forward 5000 or 8765 or expose them on public Wi-Fi or the Internet. For secure remote calling, add HTTPS/WSS and proper authentication (or WebRTC with secure signaling).
- The camera captures whenever the Pi server is running. Only record/share audio or video with everyone present's permission.
- There is **no physical E-stop**. Keep the motor power switch or battery disconnect easily accessible. Lift wheels for initial testing. A software timeout cannot protect against all software or hardware faults.
- Bluetooth and webcam mic can create an acoustic feedback loop. Keep volume low, position the JBL away from the webcam mic, and use mute controls. This is **not echo-free WebRTC**.
- All motors run at L298N enable-jumper **full speed**; no PWM speed setting is implemented in this wiring. Joystick/steering labels assume one motor drives the left wheels and one drives the right wheels.
- Local audio uses 16-bit 16kHz mono PCM over WebSocket, suitable for a demo, not an optimized internet voice codec. Some audio glitches/latency may occur. Speaker and webcam mic use PipeWire *default* sink/source as configured with `wpctl`.

## Troubleshooting

**Video unavailable**: `v4l2-ctl --list-devices` should show Uokier as `/dev/video0`. Stop old FFmpeg or VLC capture on Pi using `/dev/video0`; only one camera process can open it. Check the Pi log for `[camera] Opened /dev/video0`. Try `fswebcam -d /dev/video0 -r 640x480 --no-banner ~/test.jpg` after stopping this server.

**Sound from built-in Pi instead of JBL**: `wpctl status`, look under Sinks and select the *current* JBL sink ID via `wpctl set-default <ID>`. Make sure JBL is paired/connected via Bluetooth.

**Robot microphone audio absent on Mac**: `wpctl status`, set the *Camera Mono* source as default via `wpctl set-default <source-ID>`. In a Pi SSH terminal, `arecord -l` should list `HD Web Camera` and `pw-record --rate=16000 --channels=1 --format=s16 -` should produce raw data (don't run this concurrently with the dashboard call). Recheck the browser speakers aren't muted.

**Mac microphone won't activate**: launch the webpage at `http://localhost:8080`, not at Pi's `http://...`; local loopback is a secure context for browser microphone permission. Approve microphone access in Chrome/Safari.

**Motors don't move / busy GPIO**: stop old motor backend and motor test first. Verify wiring. Watch the Pi terminal for GPIO errors. Test safe STOP before moving. The code assumes positive direction of each motor = forward; if one motor is physically reversed, use the environment variables below for **the named forward/back/turn buttons** (raw `Both +` / `Both −` always show physical positive/negative). E.g., if Motor B's positive is backward:

```bash
# Run this instead of "python3 server.py" on the Pi:
RIGHT_FORWARD_SIGN=-1 python3 server.py
```

**Audio ports not reachable**: both Mac and Pi must be on same hotspot. Test `http://hackumass-robot.local:5000/api/health` from the Mac won't show JSON without the key; use the dashboard Connect button instead. Check Pi terminal and ensure no other app is using ports 5000 or 8765.

## Project structure

```text
robot_and_roll_unified/
  README.md
  pi/
    server.py        # Flask video + motor + TTS/music and WebSocket duplex audio
  mac/
    index.html       # Video-call interface
    style.css        # Dashboard styling
    app.js           # Motor buttons, remote audio, microphone, TTS, music
  music/             # Add your own audio files here
```
