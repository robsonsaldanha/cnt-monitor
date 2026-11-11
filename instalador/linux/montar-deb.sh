#!/bin/bash
# Monta o pacote .deb do CNT Monitor (Ubuntu 22.04+, Debian 12+, Mint 21+).
# Roda numa máquina Linux com as dependências de build/requirements-linux.txt instaladas:
#
#   instalador/linux/montar-deb.sh            -> saida/cnt-monitor_<versão>_amd64.deb
#
# PYTHON escolhe o interpretador (padrão: python3). A pasta geoip/ (bases DB-IP), se existir, vai junto.
# Gere em Ubuntu 22.04: o programa empacotado usa a glibc de onde foi gerado (2.35), e funciona dali para cima.
set -euo pipefail
cd "$(dirname "$0")/../.."
RAIZ="$PWD"
PY="${PYTHON:-python3}"
VERSAO=$(sed -n 's/^VERSAO = "\(.*\)"/\1/p' src/monitorpc/config.py)
[ -n "$VERSAO" ] || { echo "não achei a versão em src/monitorpc/config.py"; exit 1; }
PYI=("$PY" -m PyInstaller --noconfirm --clean --distpath "$RAIZ/dist-linux" --workpath "$RAIZ/build/pyi-linux"
     --specpath "$RAIZ/build/pyi-linux" --paths "$RAIZ/src")
rm -rf dist-linux build/pyi-linux pacote

GEOIP=()
[ -d geoip ] && GEOIP=(--add-data "$RAIZ/geoip:geoip")
"${PYI[@]}" --name cnt-monitor-servico --hidden-import maxminddb --collect-submodules paramiko \
    --collect-submodules qrcode --add-data "$RAIZ/src/ui:ui" "${GEOIP[@]}" src/servico.py
"${PYI[@]}" --name cnt-monitor --hidden-import pystray._xorg --hidden-import PIL.ImageTk --collect-submodules Xlib \
    --collect-submodules qrcode src/bandeja.py
"${PYI[@]}" --name cnt-monitor-remoto --collect-all aiortc --collect-all av --collect-all aioice --collect-all pylibsrtp \
    --hidden-import mss --collect-submodules Xlib --collect-submodules jeepney src/remoto_agente.py

R="pacote/cnt-monitor_${VERSAO}_amd64"
mkdir -p "$R/DEBIAN" "$R/opt/cnt-monitor" "$R/lib/systemd/system" "$R/usr/share/applications" \
         "$R/usr/share/icons/hicolor/256x256/apps" "$R/usr/share/doc/cnt-monitor"
cp -a dist-linux/cnt-monitor/. "$R/opt/cnt-monitor/"
cp -a dist-linux/cnt-monitor-servico "$R/opt/cnt-monitor/servico"
cp -a dist-linux/cnt-monitor-remoto "$R/opt/cnt-monitor/remoto"
install -m 644 instalador/linux/cnt-monitor.service "$R/lib/systemd/system/"
install -m 644 instalador/linux/cnt-monitor.desktop "$R/usr/share/applications/"
install -m 644 LICENSE "$R/usr/share/doc/cnt-monitor/copyright"
"$PY" - "$R/usr/share/icons/hicolor/256x256/apps/cnt-monitor.png" <<'EOF'
import sys
from PIL import Image
im = Image.open("instalador/icone.ico")
im.size = max(im.ico.sizes())
im.convert("RGBA").resize((256, 256)).save(sys.argv[1])
EOF
for f in postinst prerm postrm; do install -m 755 "instalador/linux/$f" "$R/DEBIAN/"; done
TAM=$(du -sk --exclude=DEBIAN "$R" | cut -f1)
cat > "$R/DEBIAN/control" <<EOF
Package: cnt-monitor
Version: $VERSAO
Architecture: amd64
Maintainer: Robson Saldanha <54083389+robsonsaldanha@users.noreply.github.com>
Installed-Size: $TAM
Depends: libc6 (>= 2.35), systemd, iproute2, libx11-6, libxrandr2, libxtst6, libxfixes3, libxext6
Recommends: xclip, wl-clipboard, xdg-desktop-portal, gstreamer1.0-tools, gstreamer1.0-pipewire, libnotify-bin, iw, xdg-user-dirs, xdg-utils, power-profiles-daemon
Suggests: gnome-shell-extension-appindicator, chromium | google-chrome-stable | microsoft-edge-stable
Section: utils
Priority: optional
Homepage: https://monitor.cntoficial.com.br
Description: Monitor do computador e acesso remoto (CNT Monitor)
 CPU, memória, disco, rede por programa, temperatura e GPU, histórico de 7 dias,
 alertas, regras por programa, otimização, monitoramento de VPS e acesso remoto
 por ID e senha (atender e ser atendido, inclusive entre Windows e Linux).
 Funciona em X11 e Wayland.
EOF
mkdir -p saida
dpkg-deb --root-owner-group -Zxz --build "$R" "saida/cnt-monitor_${VERSAO}_amd64.deb"
ls -la saida/*.deb
