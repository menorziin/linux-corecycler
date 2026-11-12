"""Headless commands — tune, resume and inspect sessions without a display."""

from __future__ import annotations

import os
import signal
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from corecycler.config import tools
from corecycler.config.settings import load_settings

if TYPE_CHECKING:
    from corecycler.config.tools import Resolution

EXIT_COMPLETED = 0
EXIT_PAUSED = 3
EXIT_QUARANTINED = 4
EXIT_REFUSED = 5
EXIT_ENGINE_ABORTED = 6
EXIT_LOCKED = 7
EXIT_NOTHING_ACTIVE = 8
EXIT_SIGNAL = 130

USAGE = """\
corecycler headless commands:

  corecycler doctor               report every external tool and where it resolved
  corecycler status               list tuner sessions and their state
  corecycler pbo                  show the live PBO state read from the CPU: per-core
                                  Curve Optimizer offsets, PPT/TDC/EDC, max boost (root)
  corecycler tune [--config F]    start a NEW tuning session and run to the end
  corecycler resume [SESSION_ID]  resume a mid-run/paused session (newest if omitted)
  corecycler resume --active-only resume ONLY a mid-run (running/validating)
                                  session; never a paused one (boot service path)
  corecycler resume ... --keep-on-signal
                                  a SIGTERM/SIGINT still reverts offsets but leaves
                                  the session mid-run, so the next boot resumes it
                                  (implied by --active-only)

Exit codes: 0 completed, 3 paused (needs attention), 4 quarantined,
5 refused (bad config/environment), 6 engine aborted, 7 already running,
8 nothing mid-run to resume (--active-only),
130 stopped by signal (offsets reverted to baseline).

Running the binary with no command opens the GUI.
"""


def cli_main(argv: list[str]) -> int:
    command = argv[0]
    if command == "doctor":
        return cmd_doctor()
    if command == "status":
        return cmd_status()
    if command == "pbo":
        return cmd_pbo()
    if command == "tune":
        config_path = _flag_value(argv[1:], "--config")
        if config_path is _INVALID:
            print("corecycler tune: --config requires a file path", file=sys.stderr)
            return EXIT_REFUSED
        return cmd_run(config_path=config_path, resume_id=None, auto_resume=False)
    if command == "resume":
        flags = [a for a in argv[1:] if a.startswith("-")]
        rest = [a for a in argv[1:] if not a.startswith("-")]
        unknown = [f for f in flags if f not in ("--active-only", "--keep-on-signal")]
        if unknown:
            print(f"corecycler resume: unknown option {unknown[0]!r}", file=sys.stderr)
            return EXIT_REFUSED
        active_only = "--active-only" in flags
        keep_on_signal = active_only or "--keep-on-signal" in flags
        if active_only and rest:
            print("corecycler resume: --active-only takes no SESSION_ID", file=sys.stderr)
            return EXIT_REFUSED
        if len(rest) > 1:
            print("corecycler resume: at most one SESSION_ID", file=sys.stderr)
            return EXIT_REFUSED
        if rest:
            try:
                session_id = int(rest[0])
            except ValueError:
                print(f"corecycler resume: invalid session id {rest[0]!r}", file=sys.stderr)
                return EXIT_REFUSED
            return cmd_run(config_path=None, resume_id=session_id, auto_resume=False, keep_on_signal=keep_on_signal)
        return cmd_run(
            config_path=None,
            resume_id=None,
            auto_resume=True,
            active_only=active_only,
            keep_on_signal=keep_on_signal,
        )
    print(USAGE, file=sys.stderr)
    return EXIT_REFUSED


_INVALID = object()


def doctor_lines(resolutions: list[Resolution], unmet: list[str]) -> list[str]:
    """The dependency report, one tool per line, grouped by how much it matters."""
    width = max(len(r.key) for r in resolutions)
    lines = ["corecycler doctor", ""]
    for kind in (tools.BACKEND, tools.CORE, tools.OPTIONAL):
        lines.append(kind)
        for resolution in [r for r in resolutions if tools.TOOLS[r.key].kind == kind]:
            detail = str(resolution.path) if resolution.path else resolution.problem
            lines.append(f"  {resolution.key:<{width}}  {resolution.origin:<7} {detail}")
            if resolution.path is None:
                lines += [f"    candidate: {c}" for c in tools.discover(resolution.key)]
        lines.append("")
    if os.geteuid() == 0:
        lines += [tools.SUDO_PATH_NOTE, ""]
    lines.append("Pin a path with CORECYCLER_<TOOL>_BIN, or in the GUI when a backend is missing.")
    lines.append("")
    lines += [f"doctor: FAILED -- {problem}" for problem in unmet] or ["doctor: ok"]
    return lines


def cmd_doctor() -> int:
    tools.load_configured_paths()
    resolutions = tools.report()
    unmet = tools.unmet_requirements(resolutions)
    for line in doctor_lines(resolutions, unmet):
        print(line)
    return EXIT_REFUSED if unmet else EXIT_COMPLETED


def _flag_value(args: list[str], flag: str):
    if flag not in args:
        return None
    i = args.index(flag)
    if i + 1 >= len(args):
        return _INVALID
    return args[i + 1]


def cmd_status(db=None) -> int:
    from corecycler.history.db import HistoryDB
    from corecycler.tuner import persistence as tp

    own_db = db is None
    if db is None:
        db = HistoryDB()
    try:
        sessions = db.list_tuner_sessions(limit=50)
        if not sessions:
            print("no tuner sessions")
            return EXIT_COMPLETED
        for sess in sessions:
            states = tp.load_core_states(db, sess.id)
            done = sum(1 for cs in states.values() if cs.phase in ("confirmed", "hardened"))
            print(
                f"#{sess.id}  {sess.status:<12} {done}/{len(states)} cores done  "
                f"created {sess.created_at[:19]}  {sess.cpu_model or ''}"
            )
            if any(cs.best_offset is not None for cs in states.values()):
                for core_id in sorted(states):
                    cs = states[core_id]
                    best = "-" if cs.best_offset is None else f"{cs.best_offset:+d}"
                    phase = getattr(cs.phase, "value", cs.phase)
                    print(f"      core {core_id}: best {best:>4}  ({phase}, crashes {cs.crash_count})")
        return EXIT_COMPLETED
    finally:
        if own_db:
            db.close()


def cmd_pbo(*, topology=None, smu=None, power_limits=None) -> int:
    """Print the PBO state the CPU is running with right now (BIOS + SMU overrides)."""
    if topology is None:
        from corecycler.engine.topology import detect_topology

        topology = detect_topology()
    if topology is None or not topology.cores:
        print("corecycler pbo: CPU topology detection failed", file=sys.stderr)
        return EXIT_REFUSED
    num_cores = len(topology.cores)
    if smu is None:
        smu = _build_smu(topology)
    if smu is None:
        print(
            "corecycler pbo: cannot talk to the SMU -- run with sudo and make sure "
            "the ryzen_smu module is loaded (sudo modprobe ryzen_smu)",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    state = smu.detect_system_state(num_cores)
    if power_limits is None:
        try:
            from corecycler.smu.pmtable import read_power_limits

            power_limits = read_power_limits(num_cores)
        except Exception:
            power_limits = (None, None, None)
    ppt, tdc, edc = power_limits

    def _fmt(value, unit: str, fmt: str = "{:.0f}") -> str:
        return "n/a" if value is None else f"{fmt.format(value)} {unit}"

    gen = getattr(state.generation, "name", None) or "unknown"
    print(f"CPU: {topology.model_name or '?'}  ({gen})")
    print("Curve Optimizer (per core):")
    for core_id in sorted(state.co_offsets):
        value = state.co_offsets[core_id]
        shown = "n/a" if value is None else f"{value:+d}"
        print(f"  core {core_id}: {shown}")
    print(f"PPT: {_fmt(ppt, 'W')}   TDC: {_fmt(tdc, 'A')}   EDC: {_fmt(edc, 'A')}")
    if state.pbo_scalar is not None:
        print(f"PBO scalar: {state.pbo_scalar:g}x")
    if state.boost_limit_mhz is not None:
        print(f"Boost limit: {state.boost_limit_mhz} MHz")
    print(f"Max boost clock (cpufreq): {_fmt(state.max_freq_mhz, 'MHz')}")
    return EXIT_COMPLETED


def _build_smu(topology):
    from corecycler.smu.commands import detect_generation, get_commands
    from corecycler.smu.driver import RyzenSMU, core_map_blocked

    commands = get_commands(detect_generation(topology.family, topology.model, topology.model_name))
    if commands is None or not commands.has_co or not RyzenSMU.is_available():
        return None
    smu = RyzenSMU(commands)
    smu.set_topology(topology)
    map_err = core_map_blocked(smu)
    if map_err is not None:
        print(f"corecycler: per-core CO disabled: {map_err}", file=sys.stderr)
        return None
    return smu


def cmd_run(
    config_path: str | None,
    resume_id: int | None,
    auto_resume: bool,
    *,
    active_only: bool = False,
    keep_on_signal: bool = False,
    engine_factory=None,
    db=None,
) -> int:
    from PySide6.QtCore import QCoreApplication, QLockFile

    from corecycler.config.paths import user_home

    app = QCoreApplication.instance() or QCoreApplication(sys.argv[:1])

    lock_dir = user_home() / ".local" / "share" / "corecycler"
    lock_dir.mkdir(parents=True, exist_ok=True)
    instance_lock = QLockFile(str(lock_dir / "corecycler.lock"))
    if not instance_lock.tryLock(0):
        print("corecycler: another instance is already running.", file=sys.stderr)
        return EXIT_LOCKED

    from corecycler.engine.backends import get_backend, load_all
    from corecycler.engine.topology import detect_topology
    from corecycler.history.db import HistoryDB
    from corecycler.tuner import persistence as tp
    from corecycler.tuner.config import TunerConfig
    from corecycler.tuner.engine import TunerEngine

    config = TunerConfig()
    if config_path is not None:
        try:
            config = TunerConfig.from_json(Path(config_path).read_text())
        except OSError as e:
            print(f"corecycler: cannot read config: {e}", file=sys.stderr)
            return EXIT_REFUSED
    errors = config.validate()
    if errors:
        print("corecycler: invalid config: " + "; ".join(errors), file=sys.stderr)
        return EXIT_REFUSED

    load_all()
    tools.load_configured_paths()
    if db is None:
        db = HistoryDB()
    if active_only and tp.pick_auto_resume_session(db) is None:
        # Boot-service path: a paused session is a deliberate human choice and
        # quarantined/completed ones never qualify -- nothing to do is not an error.
        print("corecycler: no mid-run session to resume", flush=True)
        return EXIT_NOTHING_ACTIVE
    if engine_factory is not None:
        engine = engine_factory(db, config)
    else:
        topology = detect_topology()
        if topology is None or not topology.cores:
            print("corecycler: CPU topology detection failed", file=sys.stderr)
            return EXIT_REFUSED
        smu = _build_smu(topology)
        if smu is None:
            print(
                "corecycler: per-core SMU access is unavailable — the tuner "
                "needs it (any refusal reason is printed above; otherwise "
                "check modprobe ryzen_smu and device permissions).",
                file=sys.stderr,
            )
            return EXIT_REFUSED
        try:
            backend = get_backend(config.backend)
        except KeyError:
            print(f"corecycler: unknown backend {config.backend!r}", file=sys.stderr)
            return EXIT_REFUSED
        if not backend.is_available():
            resolution = backend.resolution()
            print(
                f"corecycler: backend {config.backend!r} {resolution.problem} -- "
                f"install {tools.TOOLS[config.backend].package}, or set "
                f"{tools.env_var(config.backend)}; run 'corecycler doctor' for the full report",
                file=sys.stderr,
            )
            return EXIT_REFUSED
        engine = TunerEngine(db=db, topology=topology, smu=smu, backend=backend, config=config)

    outcome: dict[str, int] = {}

    def finish(code: int, *, force: bool = False) -> None:
        if force:
            outcome["exit"] = code
        else:
            outcome.setdefault("exit", code)
        app.quit()

    engine.log_message.connect(lambda m: print(m, flush=True))
    engine.session_completed.connect(lambda _p: finish(EXIT_COMPLETED, force=True))

    def on_status(status: str) -> None:
        if status == "paused":
            finish(EXIT_PAUSED)
        elif status == "quarantined":
            finish(EXIT_QUARANTINED)
        elif status == "idle":
            finish(EXIT_ENGINE_ABORTED)

    engine.status_changed.connect(on_status)

    def on_signal(signum, _frame) -> None:
        print(f"corecycler: signal {signum} — aborting (offsets revert)", flush=True)
        outcome["exit"] = EXIT_SIGNAL
        # Boot-service path: a stop from systemd (reboot/shutdown) is not a
        # verdict on the session. abort() reverts offsets, clears in_test (so
        # no core is blamed) and marks the session "aborted"; put the mid-run
        # status back so the next boot's --active-only resumes it.
        session_id = getattr(engine, "session_id", None) if (keep_on_signal or active_only) else None
        prior = None
        if session_id is not None:
            session = tp.get_session(db, session_id)
            prior = session.status if session is not None else None
        engine.abort()
        if prior in ("running", "validating"):
            after = tp.get_session(db, session_id)
            if after is not None and after.status == "aborted":
                tp.update_session_status(db, session_id, prior)
                print(f"corecycler: session {session_id} kept {prior} for the next boot", flush=True)
        app.quit()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)

    if resume_id is not None:
        engine.resume(resume_id)
    elif auto_resume:
        session = tp.pick_auto_resume_session(db)
        if session is None and active_only:
            return EXIT_NOTHING_ACTIVE
        if session is None:
            sessions = db.list_resumable_tuner_sessions()
            if not sessions:
                print("corecycler: no resumable session", file=sys.stderr)
                return EXIT_REFUSED
            session = sessions[0]
        engine.resume(session.id)
    else:
        engine.start()

    if "exit" in outcome:
        return outcome["exit"]
    if engine.status not in ("running", "validating", "hunting"):
        print(
            f"corecycler: engine did not start (status {engine.status}) — see log",
            file=sys.stderr,
        )
        return EXIT_REFUSED

    app.exec()
    code = outcome.get("exit", EXIT_ENGINE_ABORTED)
    _notify_outcome(code)
    return code


_OUTCOME_NOTES = {
    EXIT_COMPLETED: ("Tuning complete", "The session finished and confirmed a profile.", "normal"),
    EXIT_PAUSED: ("Tuning paused", "The tuner stopped for attention — check the log.", "critical"),
    EXIT_QUARANTINED: ("Tuning quarantined", "The profile is unsafe; cores forced to stock.", "critical"),
    EXIT_ENGINE_ABORTED: ("Tuning aborted", "The engine stopped itself — check the log.", "critical"),
}


def _notify_outcome(code: int) -> None:
    note = _OUTCOME_NOTES.get(code)
    if note is None:
        return
    try:
        if not load_settings().notify_on_completion:
            return
        from corecycler.notify import desktop_notify

        title, body, urgency = note
        desktop_notify(title, body, urgency=urgency)
    except Exception as e:
        print(f"notification failed: {e}", file=sys.stderr)
