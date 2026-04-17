#!/bin/bash
# Gestion du service Ampersage

SERVICE=ampersage
UNIT_FILE="$(dirname "$0")/${SERVICE}.service"
SYSTEMD_DIR="/etc/systemd/system"

case "$1" in
  install)
    echo "Installation du service ${SERVICE}..."
    sudo cp "$UNIT_FILE" "${SYSTEMD_DIR}/${SERVICE}.service"
    sudo systemctl daemon-reload
    sudo systemctl enable "$SERVICE"
    echo "Service installé. Lancer avec : $0 start"
    ;;
  uninstall)
    echo "Suppression du service ${SERVICE}..."
    sudo systemctl stop "$SERVICE" 2>/dev/null
    sudo systemctl disable "$SERVICE" 2>/dev/null
    sudo rm -f "${SYSTEMD_DIR}/${SERVICE}.service"
    sudo systemctl daemon-reload
    echo "Service supprimé."
    ;;
  start)
    sudo systemctl start "$SERVICE"
    echo "Démarré."
    ;;
  stop)
    sudo systemctl stop "$SERVICE"
    echo "Arrêté."
    ;;
  restart)
    sudo systemctl restart "$SERVICE"
    echo "Redémarré."
    ;;
  status)
    systemctl status "$SERVICE"
    ;;
  logs)
    journalctl -u "$SERVICE" -f
    ;;
  *)
    echo "Usage: $0 {install|uninstall|start|stop|restart|status|logs}"
    exit 1
    ;;
esac
