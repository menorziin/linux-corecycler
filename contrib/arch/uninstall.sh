#!/bin/bash
# Remove o que install.sh instalou. O histórico (~/.local/share/corecycler) fica.
set -u
if [ "$(id -u)" -ne 0 ]; then
    echo "rode com sudo: sudo $0" >&2
    exit 1
fi
[ -x /usr/local/bin/corecycler-autotune ] && /usr/local/bin/corecycler-autotune disarm
rm -f /etc/systemd/system/corecycler-watchdog.service /etc/systemd/system/corecycler-autoresume.service
rm -f /usr/local/bin/corecycler /usr/local/bin/corecycler-autotune /etc/modules-load.d/corecycler.conf
rm -rf /usr/local/lib/corecycler /opt/corecycler /etc/corecycler /var/lib/corecycler
systemctl daemon-reload
echo "CoreCycler removido (histórico em ~/.local/share/corecycler mantido)"
