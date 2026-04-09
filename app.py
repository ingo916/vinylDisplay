import time, os, json, wave, hmac, hashlib, base64, threading
import pyaudio, requests, numpy as np
import subprocess
from datetime import datetime
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

ACR_HOST   = "identify-us-west-2.acrcloud.com"
ACR_KEY    = "0dd64d02f5712245fec7385393d3100c"
ACR_SECRET = "EnLvJpr77q2LSTpjpxiaKAfWpWAUl3SBDvEmRF0"

STATE_FILE     = "/home/pi/vinylDisplay/nowplaying.json"
RECORD_SECONDS = 10
POLL_INTERVAL  = 25
RESPEAKER_RATE = 16000
RESPEAKER_CHANNELS = 2
RESPEAKER_INDEX = None

def save_state(data):
    with open(STATE_FILE, "w") as f:
        json.dump(data, f)

def get_device_index():
    p = pyaudio.PyAudio()
    for i in range(p.get_device_count()):
        info = p.get_device_info_by_index(i)
        if 'seeed' in info['name'].lower():
            p.terminate()
            return i
    p.terminate()
    return None

def record_audio(filename="/home/pi/vinylDisplay/sample.wav"):
    global RESPEAKER_INDEX
    if RESPEAKER_INDEX is None:
        RESPEAKER_INDEX = get_device_index()

    p = pyaudio.PyAudio()

    if RESPEAKER_INDEX is not None:
        stream = p.open(rate=RESPEAKER_RATE, format=pyaudio.paInt16,
                        channels=RESPEAKER_CHANNELS, input=True,
                        input_device_index=RESPEAKER_INDEX,
                        frames_per_buffer=1024)
        frames = [stream.read(1024, exception_on_overflow=False)
                  for _ in range(int(RESPEAKER_RATE / 1024 * RECORD_SECONDS))]
        stream.stop_stream(); stream.close(); p.terminate()
        raw  = np.frombuffer(b''.join(frames), dtype=np.int16)
        mono = ((raw[0::2].astype(np.int32) + raw[1::2].astype(np.int32)) // 2).astype(np.int16)
        rate = RESPEAKER_RATE
    else:
        # Fallback to default mic if HAT not present yet
        stream = p.open(rate=44100, format=pyaudio.paInt16,
                        channels=1, input=True, frames_per_buffer=1024)
        frames = [stream.read(1024, exception_on_overflow=False)
                  for _ in range(int(44100 / 1024 * RECORD_SECONDS))]
        stream.stop_stream(); stream.close(); p.terminate()
        mono = np.frombuffer(b''.join(frames), dtype=np.int16)
        rate = 44100

    with wave.open(filename, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(mono.tobytes())

def identify_song():
    filename = "/home/pi/vinylDisplay/sample.wav"
    ts  = int(datetime.now().timestamp())
    msg = f"POST\n/v1/identify\n{ACR_KEY}\naudio\n1\n{ts}"
    sig = base64.b64encode(
        hmac.new(ACR_SECRET.encode(), msg.encode(), hashlib.sha1).digest()
    ).decode()
    with open(filename, "rb") as f:
        r = requests.post(
            f"https://{ACR_HOST}/v1/identify",
            files=[("sample", ("sample.wav", f, "audio/wav"))],
            data={"access_key": ACR_KEY,
                  "sample_bytes": os.path.getsize(filename),
                  "timestamp": ts, "signature": sig,
                  "data_type": "audio", "signature_version": "1"}
        )
    return r.json()

def detection_loop():
    save_state({"status": "listening", "title": "", "artist": "", "album": "", "art_url": ""})
    last_title = None
    while True:
        try:
            record_audio()
            result = identify_song()
            meta   = result["metadata"]["music"][0]
            title  = meta["title"]
            artist = meta["artists"][0]["name"]
            album  = meta.get("album", {}).get("name", "")
            art    = meta.get("album", {}).get("cover_url", "")
            if not art:
                itunes = requests.get(
                    f"https://itunes.apple.com/search",
                    params={"term": f"{artist} {title}", "limit": 1, "entity": "song"}
                ).json()
                if itunes["results"]:
                    art = itunes["results"][0].get("artworkUrl100", "").replace("100x100bb", "600x600bb")
            if title != last_title:
                last_title = title
                save_state({"status": "playing", "title": title,
                            "artist": artist, "album": album, "art_url": art})
        except (KeyError, IndexError):
            save_state({"status": "listening", "title": "",
                        "artist": "", "album": "", "art_url": ""})
        except Exception as e:
            print(f"Error: {e}")
        time.sleep(POLL_INTERVAL)

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/state")
def state():
    try:
        with open(STATE_FILE) as f:
            return jsonify(json.load(f))
    except:
        return jsonify({"status": "listening", "title": "", "artist": "", "album": "", "art_url": ""})

@app.route("/wifi")
def wifi():
    return render_template("wifi.html")

@app.route("/wifi/scan")
def wifi_scan():
    try:
        # Force a fresh scan first
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True, text=True, timeout=10
        )
        import time
        time.sleep(3)  # wait for scan to complete
        
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=15
        )
        networks = []
        seen = set()
        for line in result.stdout.strip().split("\n"):
            parts = line.split(":")
            if len(parts) >= 2:
                ssid = parts[0].strip()
                signal = parts[1].strip() if len(parts) > 1 else "?"
                security = parts[2].strip() if len(parts) > 2 else ""
                if ssid and ssid not in seen and ssid != "NowPlaying-Setup":
                    seen.add(ssid)
                    networks.append({"ssid": ssid, "signal": signal, "security": security})
        networks.sort(key=lambda x: int(x["signal"]) if x["signal"].isdigit() else 0, reverse=True)
        return jsonify({"networks": networks})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/wifi/connect", methods=["POST"])
def wifi_connect():
    data = request.get_json()
    ssid = data.get("ssid")
    password = data.get("password")
    try:
        if password:
            result = subprocess.run(
                ["nmcli", "device", "wifi", "connect", ssid, "password", password],
                capture_output=True, text=True, timeout=30
            )
        else:
            result = subprocess.run(
                ["nmcli", "device", "wifi", "connect", ssid],
                capture_output=True, text=True, timeout=30
            )
        if "successfully" in result.stdout.lower():
            return jsonify({"status": "connected", "message": f"Connected to {ssid}"})
        else:
            return jsonify({"status": "error", "message": result.stdout or result.stderr}), 400
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/wifi/reboot", methods=["POST"])
def wifi_reboot():
    subprocess.run(["sudo", "reboot"])
    return jsonify({"status": "rebooting"})

if __name__ == "__main__":
    threading.Thread(target=detection_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
