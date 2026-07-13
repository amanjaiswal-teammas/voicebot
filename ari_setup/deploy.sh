#!/bin/bash
# ================================================================
# Deploy ARI-based voicebot handler
# Run as root on the Asterisk server
# ================================================================

set -e

echo "=== 1. Install Python dependencies ==="
cd /opt/voicebot
source .venv/bin/activate
pip install -r requirements-ari.txt

echo "=== 2. Install Asterisk configs ==="
cp ari_setup/ari.conf /etc/asterisk/ari.conf
cp ari_setup/http.conf /etc/asterisk/http.conf
cp ari_setup/manager.conf /etc/asterisk/manager.conf
cp ari_setup/extensions.conf /etc/asterisk/extensions.conf

echo "=== 3. Enable required modules in modules.conf ==="
# Ensure these are loaded (add if missing):
#   load => res_http.so
#   load => res_ari.so
#   load => res_ari_model.so
#   load => res_ari_channels.so
#   load => res_ari_endpoints.so
#   load => res_stasis.so
#   load => res_stasis_playback.so
#   load => res_stasis_recording.so
#   load => app_mixmonitor.so
grep -q "res_http.so" /etc/asterisk/modules.conf || \
  sed -i '/^\[modules\]/a load => res_http.so' /etc/asterisk/modules.conf
grep -q "res_ari.so" /etc/asterisk/modules.conf || \
  sed -i '/^\[modules\]/a load => res_ari.so' /etc/asterisk/modules.conf
grep -q "res_stasis.so" /etc/asterisk/modules.conf || \
  sed -i '/^\[modules\]/a load => res_stasis.so' /etc/asterisk/modules.conf
grep -q "app_mixmonitor.so" /etc/asterisk/modules.conf || \
  sed -i '/^\[modules\]/a load => app_mixmonitor.so' /etc/asterisk/modules.conf

echo "=== 4. Create recording directory ==="
mkdir -p /var/spool/asterisk/recording
chown asterisk:asterisk /var/spool/asterisk/recording

echo "=== 5. Create sounds directory ==="
mkdir -p /var/lib/asterisk/sounds/voicebot
chown asterisk:asterisk /var/lib/asterisk/sounds/voicebot

echo "=== 6. Reload Asterisk configuration ==="
asterisk -rx "module reload" || true
asterisk -rx "dialplan reload" || true

echo "=== 7. Create systemd service for ARI handler ==="
cat > /etc/systemd/system/voicebot-ari.service << 'SERVICE'
[Unit]
Description=Voicebot ARI Handler
After=asterisk.service network.target
Requires=asterisk.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/voicebot
ExecStart=/opt/voicebot/.venv/bin/python /opt/voicebot/voicebot_ari.py
Restart=always
RestartSec=5
StandardOutput=append:/var/log/voicebot_ari.log
StandardError=append:/var/log/voicebot_ari.log

[Install]
WantedBy=multi-user.target
SERVICE

systemctl daemon-reload
systemctl enable voicebot-ari.service

echo ""
echo "=== Deployment complete ==="
echo ""
echo "Next steps:"
echo "  1. Verify configs are correct: asterisk -rx 'module show like ari'"
echo "  2. Restart Asterisk: systemctl restart asterisk"
echo "  3. Start ARI handler: systemctl start voicebot-ari"
echo "  4. Check logs: tail -f /var/log/voicebot_ari.log"
echo ""
echo "NOTE: Your dialplan must route calls to Stasis(voicebot)"
echo "      Update the [voicebot] context in extensions.conf"
echo "      Then: asterisk -rx 'dialplan reload'"
