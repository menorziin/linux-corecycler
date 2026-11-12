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
| `corecycler resume --active-only` | Retoma só uma sessão em andamento (`running`/`validating`), nunca uma pausada por você. Sai com 8 se não houver nada. Um SIGTERM (reboot) reverte os offsets mas mantém a sessão em andamento (`--keep-on-signal`). |
| `corecycler-autoresume.service` | A cada boot (enquanto armado), espera 60 s e roda o comando acima como root, com o banco do seu usuário. |
| `corecycler-watchdog.service` | Carrega o `sp5100_tco` (watchdog de hardware da AMD, com `nowayout=0`) e o alimenta. Se o PC congelar, a placa reinicia sozinha em ~30 s. Também liga `kernel.panic=10` e `panic_on_oops`. Só fica ativo enquanto armado e restaura tudo ao desarmar. |
| `corecycler-autotune` | `resume`, `start`, `arm`, `disarm`, `status`, `log`. |

O CachyOS vem com `nowatchdog` e com o `sp5100_tco` na blacklist. A blacklist só impede o carregamento automático, então o serviço carrega o módulo pelo nome. No uso normal, sem estar armado, nada muda no sistema.

## Instalação

```bash
git clone https://github.com/menorziin/linux-corecycler.git
cd linux-corecycler
sudo ./contrib/arch/install.sh --deps   # pacman: python pyside6 stress-ng dmidecode libnotify
paru -S ryzen_smu-dkms-git mprime-bin   # AUR, com o seu usuário
```

Depois disso, confira com `corecycler doctor`.

## Antes de começar: deixe a BIOS na configuração final

Os valores do Curve Optimizer só valem para o PBO com que foram testados. Um boost mais alto pede mais tensão, então um offset estável sem boost override pode travar com ele. Por isso, **configure o PBO exatamente como vai usar no dia a dia e só depois rode o tuning**:

| Opção na BIOS | Como deixar |
|---|---|
| Precision Boost Overdrive | `Advanced` (ou `Enabled`) |
| PBO Limits (PPT / TDC / EDC) | Os valores que você vai usar (`Manual`), ou `Auto`/`Motherboard` se for usar assim |
| Boost Clock Override | O valor final (ex.: +200 MHz), ou desligado se não for usar |
| PBO Scalar | O valor final (`1X` / `Auto` é o mais seguro) |
| **Curve Optimizer** | **`All Cores`, sinal `Negative`, valor `0`**. Evite `Disabled`: em algumas placas ele desliga o recurso, e os valores que o tuner grava pelo SMU podem não ter efeito. Nunca deixe offsets antigos: o tuner precisa partir do zero. |

Os nomes mudam um pouco entre fabricantes (MSI, ASUS, Gigabyte, ASRock), mas as opções são as mesmas. Normalmente ficam em *Overclocking / AI Tweaker / Tweaker → AMD Overclocking → Precision Boost Overdrive*.

**Confira se o boost override pegou** antes de começar. Com um núcleo em carga, o clock máximo deve passar do boost de fábrica da sua CPU mais o override:

```bash
stress-ng --cpu 1 --taskset 0 -t 30s &
watch -n1 "grep MHz /proc/cpuinfo | sort -t: -k2 -nr | head -3"
```

**Mudou o PBO depois?** Refaça o tuning. Isso vale para mais boost override, outros limites, scalar ou troca de BIOS. Os valores antigos podem não ser mais estáveis.

**"Hardware Error" no primeiro boot depois de mexer na BIOS:** as placas AM4/AM5 às vezes registram um machine check no reinício que aplica o PBO novo. Se ele aparece só nesse boot (`journalctl -k -b | grep "Hardware Error"`), não é problema. Se aparecer em boots normais, com o Curve Optimizer ainda em 0, o PBO em si está agressivo demais para a sua CPU. Reduza o boost override antes de fazer undervolt.

Durante o tuning, deixe o PC parado e **desligue a suspensão automática** (no KDE: Configurações do Sistema → Energia). Se o PC suspender, os testes param.

## Uso

**Recomendado:** configure na GUI e entregue para o serviço.

```bash
sudo corecycler                    # aba Auto-Tuner → configure → Start → feche a GUI
sudo corecycler-autotune resume    # o serviço retoma a sessão mais recente agora
```

Fechar a GUI aborta a sessão (os offsets voltam ao baseline e nada se perde). O `resume` retoma essa sessão dentro do serviço, onde crashes **e** reboots/desligamentos normais não a encerram: ela continua no próximo boot. Para escolher uma sessão específica: `sudo corecycler-autotune resume 12` (IDs em `corecycler status`).

**Tudo sem GUI** (configuração padrão ou um JSON), rodando no terminal:

```bash
sudo corecycler-autotune start [--config tuner.json]
```

Se der crash, o serviço assume no boot seguinte. Ctrl+C no terminal encerra a sessão de propósito.

**Só armar** (`sudo corecycler-autotune arm`) com a GUI aberta também funciona, mas só cobre crashes. Fechar a GUI ou reiniciar normalmente encerra a sessão, e o modo se desarma no boot seguinte.

Enquanto o serviço estiver tuning, a GUI não abre ("another instance"): só uma instância pode falar com o SMU. Para acompanhar:

```bash
corecycler-autotune status      # armado? watchdog? sessões e núcleos prontos
corecycler-autotune log         # log ao vivo do serviço
```

Para parar: `sudo corecycler-autotune disarm`. O tuner recebe SIGTERM, reverte os offsets para o baseline e salva o estado; dá para continuar depois com `resume`.

## Quando terminar

Os valores aplicados pelo SMU **somem no reboot**. Pegue os offsets finais na aba History (ou em `corecycler status`) e coloque-os na BIOS: PBO → Curve Optimizer → `Per Core`, com sinal `Negative` e o valor de cada núcleo. Não mude mais nada do PBO. De volta ao Linux, confira se a BIOS aplicou tudo com `sudo corecycler pbo`: ele lê da própria CPU o offset de cada núcleo, PPT/TDC/EDC e o boost. Depois use o PC normalmente por alguns dias, porque instabilidade em idle ou em carga leve só aparece com o tempo. Se der travamento ou "Hardware Error" no `journalctl`, deixe o núcleo correspondente 2 ou 3 pontos menos negativo.

Configs do CoreCycler de Windows (`.ini` com y-cruncher, por exemplo) não funcionam aqui e não são necessárias: o tuner já usa o mprime em vários tipos de carga, mais uma etapa de carga variável e a validação final com vários núcleos. Não precisa de nenhum teste extra antes do uso real.

## Segurança

- **Loop de crash:** depois de 60 boots armado (`CORECYCLER_MAX_RESUMES`), o modo se desarma sozinho.
- **Emergência:** no menu de boot, edite a linha do kernel e adicione `corecycler.noresume`. Nenhum dos dois serviços sobe.
- **Watchdog não sobe:** veja `journalctl -u corecycler-watchdog -b`. Algumas BIOS desligam o TCO. Sem ele, travamento total ainda precisa de reset manual, mas kernel panic continua reiniciando o PC.
- Crashes repetidos são duros com o sistema de arquivos. Tenha backup do que importa (btrfs + snapper ajuda).

## Desinstalar

```bash
sudo ./contrib/arch/uninstall.sh   # o histórico em ~/.local/share/corecycler é mantido
```
