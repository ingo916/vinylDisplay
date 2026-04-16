import time, os, json, wave, threading
import pyaudio, requests, numpy as np
import traceback
import subprocess
from flask import Flask, render_template, jsonify, request

app = Flask(__name__)

AUDD_TOKEN = "YOUR_AUDD_TOKEN"
STATE_FILE         = "/home/pi/vinylDisplay/nowplaying.json"
RECORD_SECONDS     = 10
POLL_INTERVAL      = 25
RESPEAKER_RATE     = 44100
RESPEAKER_CHANNELS = 1
RESPEAKER_INDEX    = None

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
        mono = np.frombuffer(b''.join(frames), dtype=np.int16)
        rate = RESPEAKER_RATE
    else:
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
    print("Sending to AudD...")
    with open(filename, "rb") as f:
        r = requests.post(
            "https://api.audd.io/",
            data={"api_token": AUDD_TOKEN, "return": "apple_music,spotify"},
            files={"file": f},
            timeout=15
        )
    return r.json()

def detection_loop():
    save_state({"status": "listening", "title": "", "artist": "", "album": "", "art_url": ""})
    last_title      = None
    no_result_count = 0
    while True:
        try:
            print("Starting recording...")
            record_audio()
            print("Recording done...")
            result = identify_song()
            match  = result.get("result")
            print(f"AudD result: {match.get('title') if match else 'no match'}")
            meta   = result["result"]
            title  = meta["title"]
            artist = meta["artist"]
            album  = meta.get("album", "")
            art    = ""
            if "apple_music" in meta and meta["apple_music"]:
                art_url = meta["apple_music"]["artwork"]["url"]
                art = art_url.replace("{w}x{h}", "600x600")
            elif "spotify" in meta and meta["spotify"]:
                images = meta["spotify"]["album"]["images"]
                if images:
                    art = images[0]["url"]
            no_result_count = 0
            if title != last_title:
                last_title = title
                save_state({"status": "playing", "title": title,
                            "artist": artist, "album": album, "art_url": art})
        except (KeyError, IndexError, TypeError):
            no_result_count += 1
            print(f"No match ({no_result_count}/3)")
            if no_result_count >= 3:
                last_title = None
                save_state({"status": "listening", "title": "",
                            "artist": "", "album": "", "art_url": ""})
        except Exception as e:
            traceback.print_exc()
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
        subprocess.run(["nmcli", "device", "wifi", "rescan"],
            capture_output=True, text=True, timeout=10)
        time.sleep(3)
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
            capture_output=True, text=True, timeout=15)
        networks = []
        seen     = set()
        for line in result.stdout.strip().split("\n"):
            parts    = line.split(":")
            if len(parts) >= 2:
                ssid     = parts[0].strip()
                signal   = parts[1].strip() if len(parts) > 1 else "?"
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
    data     = request.get_json()
    ssid     = data.get("ssid")
    password = data.get("password")
    try:
        if password:
            result = subprocess.run(
                ["nmcli", "device", "wifi", "connect", ssid, "password", password],
                capture_output=True, text=True, timeout=30)
        else:
            result = subprocess.run(
                ["nmcli", "device", "wifi", "connect", ssid],
                capture_output=True, text=True, timeout=30)
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

@app.after_request
def add_cache_headers(response):
    if request.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
    return response

if __name__ == "__main__":
    threading.Thread(target=detection_loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
