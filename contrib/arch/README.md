# Tuning automático no CachyOS / Arch (sobrevive a crashes)

Com isto, o Auto-Tuner do Curve Optimizer roda a noite toda sem você mexer:

```
offset instável → PC trava ou dá kernel panic → watchdog/panic reinicia o PC
→ login → serviço systemd retoma a sessão sem GUI → o núcleo que crashou recua
→ continua... → terminou: o modo automático se desarma e avisa no desktop
```

## Peças

| Peça | O que faz |
|---|---|
| `corecycler resume --active-only` | Retoma só uma sessão em andamento (`running`/`validating`), nunca uma pausada por você. Sai com 8 se não houver nada. |
| `corecycler-autoresume.service` | A cada boot (enquanto armado), espera 60 s e roda o comando acima como root, com o banco do seu usuário. |
| `corecycler-watchdog.service` | Carrega o `sp5100_tco` (watchdog de hardware da AMD) e o alimenta. Se o PC congelar, a placa reinicia sozinha em ~30 s. Também liga `kernel.panic=10` e `panic_on_oops`. Só fica ativo enquanto armado e restaura tudo ao desarmar. |
| `corecycler-autotune` | Arma, desarma, mostra status e log. |

O CachyOS vem com `nowatchdog` e com o `sp5100_tco` na blacklist. A blacklist só impede o carregamento automático, então o serviço carrega o módulo pelo nome. No uso normal, sem estar armado, nada muda no sistema.

## Instalação

```bash
git clone https://github.com/menorziin/linux-corecycler.git
cd linux-corecycler
sudo ./contrib/arch/install.sh --deps   # pacman: python pyside6 stress-ng dmidecode libnotify
paru -S ryzen_smu-dkms-git mprime-bin   # AUR, com o seu usuário
```

Depois disso, confira com `corecycler doctor`.

## Uso

Com a GUI (recomendado na primeira vez, para escolher backend e limites):

```bash
sudo corecycler                 # aba Auto-Tuner → configure → Start
sudo corecycler-autotune arm    # em outro terminal, logo depois de iniciar
```

Sem GUI (usa a configuração padrão ou um JSON):

```bash
sudo corecycler-autotune start [--config tuner.json]
```

Para acompanhar:

```bash
corecycler-autotune status      # armado? watchdog? sessões e núcleos prontos
corecycler-autotune log         # log ao vivo do serviço
```

Para parar: `sudo corecycler-autotune disarm`. O tuner recebe SIGTERM, reverte os offsets para o baseline e salva o estado.

## Quando terminar

Os valores aplicados pelo SMU **somem no reboot**. Pegue os offsets finais na aba History (ou em `corecycler status`) e coloque-os na BIOS: PBO → Curve Optimizer → Per Core. Depois use o PC normalmente por alguns dias. Instabilidade em idle ou em carga leve só aparece com o tempo.

## Segurança

- **Loop de crash:** depois de 60 boots armado (`CORECYCLER_MAX_RESUMES`), o modo se desarma sozinho.
- **Emergência:** no menu de boot, edite a linha do kernel e adicione `corecycler.noresume`. Nenhum dos dois serviços sobe.
- **Watchdog não sobe:** veja `journalctl -u corecycler-watchdog -b`. Algumas BIOS desligam o TCO. Sem ele, travamento total ainda precisa de reset manual, mas kernel panic continua reiniciando o PC.
- Crashes repetidos são duros com o sistema de arquivos. Tenha backup do que importa (btrfs + snapper ajuda).

## Desinstalar

```bash
sudo ./contrib/arch/uninstall.sh   # o histórico em ~/.local/share/corecycler é mantido
```
