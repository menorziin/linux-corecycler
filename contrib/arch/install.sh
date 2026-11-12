#!/bin/bash
# Instala o CoreCycler + modo de tuning automático (sobrevive a crashes) no
# CachyOS / Arch. Uso:  sudo ./contrib/arch/install.sh [--deps]
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HERE="$REPO/contrib/arch"
PREFIX=/opt/corecycler
LIBEXEC=/usr/local/lib/corecycler

if [ "$(id -u)" -ne 0 ]; then
    echo "rode com sudo: sudo $0 $*" >&2
    exit 1
fi

if [ "${1:-}" = --deps ]; then
    pacman -S --needed --noconfirm python pyside6 stress-ng dmidecode libnotify
    echo
    echo "Pacotes do AUR (instale com seu usuário, não com sudo):"
    echo "  paru -S ryzen_smu-dkms-git mprime-bin    # mprime é o melhor backend para CO"
    echo
fi

echo "==> verificando dependências"
missing=0
python3 -c 'import sys; assert sys.version_info >= (3, 12)' 2>/dev/null \
    || { echo "  falta: python >= 3.12"; missing=1; }
python3 -c 'import PySide6' 2>/dev/null || { echo "  falta: pyside6 (pacman -S pyside6)"; missing=1; }
modinfo ryzen_smu >/dev/null 2>&1 || { echo "  falta: módulo ryzen_smu (AUR ryzen_smu-dkms-git)"; missing=1; }
if ! command -v mprime >/dev/null && ! command -v stress-ng >/dev/null; then
    echo "  falta: um backend de stress (AUR mprime-bin ou pacman -S stress-ng)"; missing=1
fi
command -v mprime >/dev/null || echo "  dica: mprime (AUR mprime-bin) acha instabilidade de CO melhor que stress-ng"
[ "$missing" -eq 0 ] || echo "  (continuando a instalação; resolva o que falta antes de usar)"

echo "==> copiando para $PREFIX"
rm -rf "$PREFIX"
mkdir -p "$PREFIX"
cp -a "$REPO/src" "$REPO/assets" "$PREFIX/"
mkdir -p "$PREFIX/contrib"
cp -a "$HERE" "$PREFIX/contrib/"
find "$PREFIX" -name __pycache__ -prune -exec rm -rf {} +

cat > /usr/local/bin/corecycler <<WRAP
#!/bin/sh
exec /usr/bin/python3 $PREFIX/src/corecycler/main.py "\$@"
WRAP
chmod 755 /usr/local/bin/corecycler

install -Dm755 "$HERE/libexec/watchdog-run" "$LIBEXEC/watchdog-run"
install -Dm755 "$HERE/libexec/autoresume-run" "$LIBEXEC/autoresume-run"
install -Dm755 "$HERE/libexec/corecycler-autotune" /usr/local/bin/corecycler-autotune
install -Dm644 "$HERE/systemd/corecycler-watchdog.service" /etc/systemd/system/corecycler-watchdog.service
install -Dm644 "$HERE/systemd/corecycler-autoresume.service" /etc/systemd/system/corecycler-autoresume.service

# ryzen_smu em todo boot: o serviço precisa dele antes de retomar.
echo ryzen_smu > /etc/modules-load.d/corecycler.conf
modprobe ryzen_smu 2>/dev/null || true


systemctl daemon-reload

echo
echo "Pronto. Fluxo:"
echo "  1. sudo corecycler                 -> configure e inicie o Auto-Tuner na GUI, depois feche"
echo "  2. sudo corecycler-autotune resume -> o serviço continua; crashes e reboots são retomados"
echo "     (ou tudo sem GUI: sudo corecycler-autotune start)"
echo "  corecycler-autotune status | log   -> acompanhar"
