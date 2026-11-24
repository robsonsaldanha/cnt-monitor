#!/bin/bash
# Atualiza a VPS com o que está na main: roda NA VPS, como o usuário robson (com sudo sem senha).
#   ssh -p 22022 robson@<vps> 'bash /var/www/html_cntmonitor/servidor-vps/implantar.sh'
# Faz, nesta ordem: git pull da main; copia as units do systemd e reinicia o encontro se mudaram; garante o trecho do
# changelog no nginx (com backup e `nginx -t`); copia agora o instalador da última release (sem esperar o timer de 30 min);
# confere o resultado. Pode rodar quantas vezes quiser: sem mudança, não mexe em nada.
set -euo pipefail

DIR=/var/www/html_cntmonitor
VHOST=/etc/nginx/sites-available/cntoficial.com.br
cd "$DIR"

antes=$(git rev-parse HEAD)
git fetch --quiet --tags origin
git merge --ff-only --quiet origin/main
depois=$(git rev-parse HEAD)
echo "código: ${antes:0:7} -> ${depois:0:7}"
mudou() { [ "$antes" != "$depois" ] && git diff --name-only "$antes" "$depois" | grep -qx "$1"; }

# serviço do ponto de encontro
if mudou servidor-vps/encontro.js; then
  sudo systemctl restart cnt-monitor-encontro
  echo "encontro.js mudou: serviço reiniciado"
fi

# units do systemd
for u in cnt-monitor-baixar.service cnt-monitor-baixar.timer cnt-monitor-encontro.service; do
  if mudou "servidor-vps/$u"; then
    sudo cp "servidor-vps/$u" "/etc/systemd/system/$u"
    recarregar=1
  fi
done
if [ "${recarregar:-0}" = 1 ]; then
  sudo systemctl daemon-reload
  sudo systemctl restart cnt-monitor-baixar.timer cnt-monitor-encontro.service
  echo "units do systemd atualizadas"
fi

# trecho do changelog no vhost da home (o vhost mora fora do repositório)
if ! sudo grep -q '/monitor/changelog' "$VHOST"; then
  sudo cp "$VHOST" "$HOME/backups/cntoficial.com.br.pre-changelog-$(date +%Y%m%d%H%M%S)" 2>/dev/null || sudo cp "$VHOST" "/tmp/cntoficial.pre-changelog"
  sudo python3 - "$DIR/servidor-vps/nginx-cntoficial.com.br-monitor.conf" "$VHOST" <<'PY'
import sys
fonte, alvo = sys.argv[1:3]
f = open(fonte, encoding="utf8").read()
ini = f.index("    # changelog público")
fim = f.index("    location = /baixar {")
bloco = f[ini:fim]
v = open(alvo, encoding="utf8").read()
ancora = "    # CNT Monitor: download e encontro"
assert ancora in v, "âncora não encontrada no vhost"
open(alvo, "w", encoding="utf8").write(v.replace(ancora, bloco + ancora, 1))
PY
  if sudo nginx -t 2>/dev/null; then
    sudo systemctl reload nginx
    echo "nginx: changelog adicionado e recarregado"
  else
    echo "ERRO: nginx -t falhou; restaure o backup do vhost" >&2
    exit 1
  fi
fi

# instalador da última release, agora
sudo systemctl start cnt-monitor-baixar.service
echo "instalador: $(cat /var/www/cntmonitor_downloads/ultima.json)"

# conferência
for url in https://cntoficial.com.br/monitor/changelog/ https://cntoficial.com.br/baixar; do
  codigo=$(curl -s -o /dev/null -w '%{http_code}' -H 'User-Agent: implantar' "$url" || true)
  echo "$codigo $url"
done
systemctl is-active cnt-monitor-encontro
