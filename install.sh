#!/bin/bash

# ============================================================
#  vinylDisplay — Vinyl Now Playing Display
#  Install Script for Raspberry Pi OS Trixie (64-bit)
# ============================================================
#
#  BEFORE RUNNING THIS SCRIPT YOU MUST HAVE:
#  - An ACRCloud account (free at acrcloud.com)
#  - An ACRCloud project created with:
#      Audio Source: Recorded Audio
#      Audio Engine: Audio Fingerprinting
#  - Your ACRCloud Host, Access Key and Access Secret ready
#
#  One-line install:
#  curl -sSL https://raw.githubusercontent.com/ingo916/vinylDisplay/main/install.sh | bash
#
# ============================================================

set -e

REPO="https://raw.githubusercontent.com/ingo916/vinylDisplay/main"
INSTALL_DIR="/home/pi/vinylDisplay"

echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   vinylDisplay — Now Playing Display     ║"
echo "║   Installer v1.0                         ║"
echo "╚══════════════════════════════════════════╝"
echo ""

# ── ACRCloud credentials ──────────────────────────────────
echo "► ACRCloud Setup"
echo "  You must have your ACRCloud credentials ready."
echo "  Sign up free at https://acrcloud.com if you haven't."
echo ""
read -p "  ACRCloud Host (e.g. identify-us-west-2.acrcloud.com): " ACR_HOST
read -p "  ACRCloud Access Key: " ACR_KEY
read -p "  ACRCloud Access Secret: " ACR_SECRET
echo ""

# ── System update ─────────────────────────────────────────
echo "► Updating system packages..."
sudo apt update && sudo apt upgrade -y

# ── Install system dependencies ───────────────────────────
echo "► Installing system dependencies..."
sudo apt install -y \
  git make \
  xserver-xorg \
  x11-xserver-utils \
  xinit \
  openbox \
  chromium \
  unclutter \
  xdotool \
  python3-pip \
  python3-dev \
  portaudio19-dev \
  hostapd \
  dnsmasq

# ── Install Python packages ───────────────────────────────
echo "► Installing Python packages..."
pip3 install flask pyaudio requests numpy --break-system-packages

# ── Install ReSpeaker V2.0 drivers ───────────────────────
echo "► Installing ReSpeaker 2-Mic HAT V2.0 drivers..."
if [ ! -d "/home/pi/seeed-linux-dtoverlays" ]; then
  git clone https://github.com/Seeed-Studio/seeed-linux-dtoverlays.git /home/pi/seeed-linux-dtoverlays
fi
cd /home/pi/seeed-linux-dtoverlays
make overlays/rpi/respeaker-2mic-v2_0-overlay.dtbo
sudo cp overlays/rpi/respeaker-2mic-v2_0-overlay.dtbo /boot/firmware/overlays/respeaker-2mic-v2_0.dtbo
if ! grep -q "dtoverlay=respeaker-2mic-v2_0" /boot/firmware/config.txt; then
  echo "dtoverlay=respeaker-2mic-v2_0" | sudo tee -a /boot/firmware/config.txt
fi
cd ~

# ── Create project directory ──────────────────────────────
echo "► Creating project directory..."
mkdir -p $INSTALL_DIR/templates $INSTALL_DIR/static

# ── Download project files ────────────────────────────────
echo "► Downloading project files..."
curl -sSL $REPO/app.py -o $INSTALL_DIR/app.py
curl -sSL $REPO/templates/index.html -o $INSTALL_DIR/templates/index.html
curl -sSL $REPO/templates/wifi.html -o $INSTALL_DIR/templates/wifi.html
curl -sSL $REPO/static/style.css -o $INSTALL_DIR/static/style.css

# ── Inject ACRCloud credentials ───────────────────────────
echo "► Configuring ACRCloud credentials..."
sed -i "s|YOUR_HOST|$ACR_HOST|g" $INSTALL_DIR/app.py
sed -i "s|YOUR_KEY|$ACR_KEY|g" $INSTALL_DIR/app.py
sed -i "s|YOUR_SECRET|$ACR_SECRET|g" $INSTALL_DIR/app.py

# ── Create initial state file ─────────────────────────────
echo '{"status": "listening", "title": "", "artist": "", "album": "", "art_url": ""}' \
  > $INSTALL_DIR/nowplaying.json

# ── Install Flask systemd service ────────────────────────
echo "► Installing Flask service..."
sudo curl -sSL $REPO/config/vinylapp.service -o /etc/systemd/system/vinylapp.service
sudo systemctl daemon-reload
sudo systemctl enable vinylapp.service

# ── Configure hostapd ─────────────────────────────────────
echo "► Configuring WiFi fallback hotspot..."
sudo tee /etc/hostapd/hostapd.conf > /dev/null <<EOF
interface=wlan0
driver=nl80211
ssid=NowPlaying-Setup
hw_mode=g
channel=7
wmm_enabled=0
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0
wpa=2
wpa_passphrase=nowplaying
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP
EOF

sudo tee /etc/dnsmasq.d/nowplaying-ap.conf > /dev/null <<EOF
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
address=/#/192.168.4.1
EOF

# ── WiFi check service ────────────────────────────────────
echo "► Installing WiFi fallback service..."
sudo tee /usr/local/bin/wifi-check.sh > /dev/null <<'WIFIEOF'
#!/bin/bash
IFACE=wlan0
AP_IP=192.168.4.1
sleep 20
if nmcli -t -f STATE general | grep -q "connected"; then
  echo "WiFi connected, normal boot"
  exit 0
fi
echo "No WiFi found, starting hotspot..."
nmcli device set $IFACE managed no
ip addr flush dev $IFACE
ip addr add $AP_IP/24 dev $IFACE
ip link set $IFACE up
systemctl unmask hostapd
systemctl start hostapd
systemctl restart dnsmasq
iptables -t nat -A PREROUTING -i $IFACE -p tcp --dport 80 -j REDIRECT --to-port 5000
WIFIEOF
sudo chmod +x /usr/local/bin/wifi-check.sh

sudo tee /etc/systemd/system/wifi-check.service > /dev/null <<EOF
[Unit]
Description=WiFi Fallback Hotspot
After=NetworkManager.service
Wants=NetworkManager.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/wifi-check.sh
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
EOF
sudo systemctl daemon-reload
sudo systemctl enable wifi-check.service

# ── Allow reboot without password ─────────────────────────
echo 'pi ALL=(ALL) NOPASSWD: /sbin/reboot' | sudo tee /etc/sudoers.d/pi-reboot

# ── Configure Openbox kiosk ───────────────────────────────
echo "► Configuring kiosk mode..."
mkdir -p /home/pi/.config/openbox
tee /home/pi/.config/openbox/autostart > /dev/null <<'EOF'
unclutter -idle 0.1 -root &
xset s off
xset -dpms
xset s noblank
while true; do
  chromium --no-memcheck --kiosk --noerrdialogs --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-features=TranslateUI \
    --no-first-run \
    --password-store=basic \
    --check-for-update-interval=604800 \
    --disable-gpu \
    http://localhost:5000
  sleep 5
done &
EOF

# ── Configure auto login and startx ──────────────────────
echo "► Configuring auto login..."
sudo raspi-config nonint do_boot_behaviour B2

tee /home/pi/.bash_profile > /dev/null <<'EOF'
if [ -f ~/.bashrc ]; then
  source ~/.bashrc
fi
if [ -z "$DISPLAY" ] && [ "$(tty)" = "/dev/tty1" ]; then
  startx /usr/bin/openbox-session
fi
EOF

# ── Enable colored prompt ─────────────────────────────────
sed -i 's/#force_color_prompt=yes/force_color_prompt=yes/' /home/pi/.bashrc

# ── Useful aliases ────────────────────────────────────────
cat << 'ALIASES' >> /home/pi/.bashrc

# vinylDisplay aliases
alias nowtest='echo '"'"'{"status":"playing","title":"Cruel Summer","artist":"Taylor Swift","album":"Lover","art_url":"https://upload.wikimedia.org/wikipedia/en/c/cd/Taylor_Swift_-_Lover.png"}'"'"' > ~/vinylDisplay/nowplaying.json'
alias nowidle='echo '"'"'{"status":"listening","title":"","artist":"","album":"","art_url":""}'"'"' > ~/vinylDisplay/nowplaying.json'
alias refreshdisplay='sudo systemctl restart vinylapp.service && sleep 2 && DISPLAY=:0 xdotool key ctrl+shift+r'
ALIASES

# ── Done ──────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════╗"
echo "║   Installation complete!                 ║"
echo "║                                          ║"
echo "║   Reboot to start the display:           ║"
echo "║   sudo reboot                            ║"
echo "║                                          ║"
echo "║   WiFi setup (if needed):                ║"
echo "║   http://nowplaying.local:5000/wifi      ║"
echo "╚══════════════════════════════════════════╝"
echo ""