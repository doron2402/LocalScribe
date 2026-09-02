"""localscribe — record a meeting, transcribe it offline, summarize it."""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from contextlib import ExitStack
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import config, engines, retention, systemaudio
from .audio import Recorder, default_input, find_device, list_input_devices

console = Console()


# ------------------------------------------------------------------ helpers

def parse_duration(text: str | None) -> float | None:
    """'90s' | '30m' | '1h30m' | '45' (minutes) -> seconds"""
    if not text:
        return None
    text = text.strip().lower()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        return float(text) * 60
    total, found = 0.0, False
    for value, unit in re.findall(r"(\d+(?:\.\d+)?)\s*([hms])", text):
        total += float(value) * {"h": 3600, "m": 60, "s": 1}[unit]
        found = True
    if not found:
        raise argparse.ArgumentTypeError(f"Cannot read duration '{text}' (try 45m, 90s, 1h30m).")
    return total


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "meeting"


def hms(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def meter(level: float, width: int = 24) -> str:
    """Log-scaled bar; speech sits around -30..-10 dBFS."""
    import math
    db = 20 * math.log10(max(level, 1e-6))
    frac = min(max((db + 60) / 60, 0.0), 1.0)
    filled = int(frac * width)
    color = "red" if frac > 0.95 else "green" if frac > 0.08 else "grey42"
    return f"[{color}]{'█' * filled}{'·' * (width - filled)}[/{color}]"


def resolve_mic(mic_query: str | None):
    mic_query = config.MIC_DEVICE if mic_query is None else mic_query
    return find_device(mic_query) if mic_query else default_input()


def open_system_audio(stack: ExitStack, mode: str, loopback_query: str | None):
    """Get a device carrying the other side of the call.

    Preference order is a Core Audio tap, which needs no driver and no admin
    password, then a named loopback device like BlackHole for machines too old
    for taps. Returns (device, how, note).
    """
    if mode == "off":
        return None, "off", ""

    explicit = loopback_query is not None
    query = config.LOOPBACK_DEVICE if loopback_query is None else loopback_query

    # An explicitly named device is an instruction, not a preference.
    if explicit or mode == "device":
        device = find_device(query) if query else None
        if device:
            return device, "device", ""
        return None, "none", f"no input device matching '{query}'"

    usable, why_not = systemaudio.available()
    if usable:
        try:
            tap = stack.enter_context(systemaudio.SystemAudioTap())
            device = find_device(tap.name)
            if device:
                return device, "tap", ""
            return None, "none", "the system-audio device never appeared"
        except systemaudio.SystemAudioError as e:
            why_not = str(e)
            if mode == "tap":
                return None, "none", why_not

    if mode == "tap":
        return None, "none", why_not

    device = find_device(query) if query else None
    if device:
        return device, "device", ""
    return None, "none", why_not


def resolve_devices(mic_query: str | None, loopback_query: str | None):
    """Read-only view for doctor: never creates a tap."""
    loopback_query = config.LOOPBACK_DEVICE if loopback_query is None else loopback_query
    return resolve_mic(mic_query), (find_device(loopback_query) if loopback_query else None)


# ----------------------------------------------------------------- commands

def cmd_devices(args) -> int:
    table = Table(title="Audio input devices", header_style="bold")
    table.add_column("#", justify="right")
    table.add_column("Name")
    table.add_column("Ch", justify="right")
    table.add_column("Rate", justify="right")
    default = default_input()
    for d in list_input_devices():
        name = d.name + ("  [dim](default)[/dim]" if default and d.index == default.index else "")
        table.add_row(str(d.index), name, str(d.channels), f"{int(d.samplerate)} Hz")
    console.print(table)
    return 0


def cmd_doctor(args) -> int:
    import shutil

    from .summarize import ollama_available, ollama_models

    ok = True
    console.print("[bold]localscribe doctor[/bold]\n")

    mic, loopback = resolve_devices(args.mic, args.loopback)
    if mic:
        console.print(f"  [green]✓[/green] Microphone: {mic.name}")
    else:
        ok = False
        console.print("  [red]✗[/red] No microphone found. Grant mic access under "
                      "System Settings → Privacy & Security → Microphone.")
    mode = getattr(args, "system_audio", None) or config.SYSTEM_AUDIO
    usable, why_not = systemaudio.available()
    if mode == "off":
        console.print("  [yellow]![/yellow] System audio disabled — microphone only")
    elif usable and mode in ("auto", "tap"):
        console.print("  [green]✓[/green] System audio: Core Audio tap "
                      "(no driver, no password, no reboot)")
    elif loopback:
        console.print(f"  [green]✓[/green] System audio: {loopback.name}")
    else:
        ok = False
        console.print(f"  [red]✗[/red] No way to capture the other side of the call "
                      f"({why_not}). Install BlackHole, or record your mic only "
                      f"with --system-audio off.")

    try:
        engine = engines.resolve(args.engine)
        if engine == "mlx":
            console.print("  [green]✓[/green] Engine: mlx (Metal GPU, ~2x faster)")
        elif engines.mlx_available():
            console.print("  [green]✓[/green] Engine: faster-whisper (CPU)")
        else:
            console.print("  [green]✓[/green] Engine: faster-whisper (CPU)")
            import platform
            if platform.system() == "Darwin" and platform.machine() == "arm64":
                console.print("  [yellow]![/yellow] mlx-whisper not installed — this Mac "
                              "could transcribe ~2x faster. Run: "
                              r"uv pip install -e '.\[mlx]'")
    except engines.EngineError as e:
        ok = False
        console.print(f"  [red]✗[/red] {e}")

    model_dir = Path("~/.cache/huggingface/hub").expanduser()
    cached = list(model_dir.glob(f"*{config.WHISPER_MODEL}*")) if model_dir.exists() else []
    if cached:
        console.print(f"  [green]✓[/green] Whisper model cached: {config.WHISPER_MODEL}")
    else:
        console.print(f"  [yellow]![/yellow] Whisper model '{config.WHISPER_MODEL}' "
                      f"not downloaded yet (happens on first transcribe).")

    backend = args.backend or config.SUMMARY_BACKEND
    if backend == "ollama":
        if ollama_available():
            models = ollama_models()
            if config.OLLAMA_MODEL in models:
                console.print(f"  [green]✓[/green] Ollama up, model {config.OLLAMA_MODEL} present")
            else:
                ok = False
                console.print(f"  [red]✗[/red] Ollama up but '{config.OLLAMA_MODEL}' is missing. "
                              f"Run: ollama pull {config.OLLAMA_MODEL}")
                if models:
                    console.print(f"      Installed: {', '.join(models)}")
        elif shutil.which("ollama"):
            console.print("  [green]✓[/green] Ollama installed (not running — "
                          "localscribe starts it when it needs it)")
        else:
            ok = False
            console.print("  [red]✗[/red] Ollama not installed. Run `brew install ollama`, "
                          "or use --backend extractive.")
    elif backend == "anthropic":
        if config.ANTHROPIC_API_KEY:
            console.print("  [green]✓[/green] ANTHROPIC_API_KEY set")
        else:
            ok = False
            console.print("  [red]✗[/red] ANTHROPIC_API_KEY not set")
    else:
        console.print("  [green]✓[/green] Extractive summarizer (no model needed)")

    console.print(f"\n  Data directory: {config.DATA_DIR}")
    return 0 if ok else 1


def _source_label(loopback, how: str) -> str:
    if loopback is None:
        return "—"
    if how == "tap":
        return "system audio (Core Audio tap)"
    return loopback.name


def _record(args) -> Path | None:
    stack = ExitStack()
    with stack:
        mic = resolve_mic(args.mic)
        mode = getattr(args, "system_audio", None) or config.SYSTEM_AUDIO
        loopback, how, note = open_system_audio(stack, mode, args.loopback)

        if not mic and not loopback:
            console.print("[red]No usable input device.[/red] Run `localscribe devices`.")
            return None
        if not loopback:
            console.print(
                f"[yellow]Recording your microphone only[/yellow] — the other side of "
                f"the call will be missing.\n[dim]{note}. Run `localscribe doctor`.[/dim]\n"
            )
        elif how == "tap":
            console.print("[dim]Capturing system audio directly — no driver needed.[/dim]")

        return _run_recorder(args, mic, loopback, how)


def _run_recorder(args, mic, loopback, how) -> Path | None:
    label = args.label or "meeting"
    started = datetime.now()
    config.ensure_dirs()
    audio_path = config.AUDIO_DIR / f"{slugify(label)}_{started:%Y-%m-%d_%H%M}.wav"

    rec = Recorder(audio_path, mic, loopback)
    limit = parse_duration(args.duration)

    console.print(Panel.fit(
        f"[bold]{label}[/bold]\n"
        f"mic: {mic.name if mic else '—'}\n"
        f"sys: {_source_label(loopback, how)}\n"
        f"file: {audio_path}\n"
        + (f"stops after {hms(limit)}\n" if limit else "")
        + "[dim]Ctrl-C to stop[/dim]",
        title="recording", border_style="red",
    ))

    def render() -> Table:
        t = Table.grid(padding=(0, 1))
        t.add_row("[dim]elapsed[/dim]", f"[bold]{hms(rec.seconds)}[/bold]")
        for role, lvl in rec.levels.items():
            t.add_row("You " if role == "me" else "Them", meter(lvl))
        return t

    stop_flag = threading.Event()

    def run():
        try:
            rec.run(max_seconds=limit, tick=lambda r: None)
        finally:
            stop_flag.set()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    try:
        with Live(render(), console=console, refresh_per_second=8) as live:
            while not stop_flag.wait(0.12):
                live.update(render())
    except KeyboardInterrupt:
        console.print("\n[dim]stopping…[/dim]")
        rec.stop()
        stop_flag.wait(5)
    worker.join(timeout=10)
    console.print()

    drops = {k: v for k, v in rec.overflow_report().items() if v}
    if drops:
        console.print(f"[yellow]Dropped audio blocks (machine was busy): {drops}[/yellow]")

    if rec.seconds < 1:
        console.print("[red]Recorded less than a second — nothing to do.[/red]")
        return None

    Path(audio_path).with_suffix(".json").write_text(json.dumps({
        "label": label,
        "started_at": started.isoformat(timespec="seconds"),
        "channel_roles": rec.roles,
        "devices": {"me": mic.name if mic else None,
                    "them": _source_label(loopback, how)},
        "system_audio": how,
    }, indent=2))

    console.print(f"[green]Saved[/green] {hms(rec.seconds)} → {audio_path}")
    return audio_path


def _process(audio_path: Path, args) -> int:
    from .summarize import SummaryError, summarize, write_summary
    from .transcribe import transcribe, write_transcript

    try:
        engine = engines.resolve(getattr(args, "engine", None))
    except engines.EngineError as e:
        console.print(f"[red]{e}[/red]")
        return 1

    label = f"{args.model or config.WHISPER_MODEL} via {engine}"
    with console.status(f"Transcribing ({label})…"):
        t = transcribe(audio_path, model_name=args.model, language=args.language, engine=engine)
    md, js = write_transcript(t)
    console.print(f"[green]Transcript[/green] {len(t.segments)} segments, {label} → {md}")

    if not t.segments:
        console.print("[yellow]Nothing was transcribed — skipping the summary.[/yellow]")
        return 1
    if args.no_summary:
        return 0

    backend = args.backend or config.SUMMARY_BACKEND
    status = console.status("Summarizing…")
    status.start()

    def progress(stage, i, n):
        status.update(f"Summarizing ({stage} {i}/{n})…")

    try:
        summary = summarize(t, backend=backend, model=args.summary_model, on_progress=progress)
    except SummaryError as e:
        status.stop()
        console.print(f"[red]{e}[/red]")
        console.print("[dim]Falling back to the extractive summarizer.[/dim]")
        backend = "extractive"
        summary = summarize(t, backend="extractive")
    finally:
        status.stop()

    path = write_summary(t, summary, backend)
    console.print(f"[green]Summary[/green] → {path}\n")

    # The notes are on disk now, so the recording has done its job.
    if config.DELETE_AUDIO_AFTER_SUMMARY and not getattr(args, "keep_audio", False):
        freed = retention.delete_recording(audio_path)
        if freed:
            console.print(
                f"[dim]Deleted the recording ({freed / 1e6:.1f} MB); the transcript "
                f"and summary are kept.[/dim]\n"
            )

    console.print(Markdown(summary))
    return 0


def auto_prune() -> None:
    """Expire old files as a side effect of normal use.

    A retention policy that needs a cron job is a retention policy that does not
    run, so every recording and reprocess sweeps first.
    """
    try:
        sweep = retention.prune()
    except OSError:
        return
    if sweep:
        console.print(f"[dim]Retention: {retention.describe(sweep)}.[/dim]")


def cmd_record(args) -> int:
    auto_prune()
    audio_path = _record(args)
    if audio_path is None:
        return 1
    if args.no_transcribe:
        return 0
    return _process(audio_path, args)


def cmd_process(args) -> int:
    auto_prune()
    path = Path(args.audio).expanduser()
    if not path.exists():
        console.print(f"[red]No such file:[/red] {path}")
        return 1
    return _process(path, args)


def cmd_summarize(args) -> int:
    from .summarize import SummaryError, summarize, write_summary
    from .transcribe import load_transcript

    path = Path(args.transcript).expanduser()
    if not path.exists():
        console.print(f"[red]No such file:[/red] {path}")
        return 1
    t = load_transcript(path)
    backend = args.backend or config.SUMMARY_BACKEND
    try:
        with console.status("Summarizing…"):
            summary = summarize(t, backend=backend, model=args.summary_model)
    except SummaryError as e:
        console.print(f"[red]{e}[/red]")
        return 1
    out = write_summary(t, summary, backend)
    console.print(f"[green]Summary[/green] → {out}\n")
    console.print(Markdown(summary))
    return 0


def cmd_prune(args) -> int:
    overrides = {}
    if args.days is not None:
        overrides["audio"] = args.days
        if args.all:
            overrides["transcripts"] = args.days
            overrides["summaries"] = args.days
    elif args.all:
        days = config.RETENTION_DAYS
        overrides = {"audio": days, "transcripts": days, "summaries": days}

    days = retention.policy(overrides)
    table = Table(title="Retention", header_style="bold")
    table.add_column("What")
    table.add_column("Kept for")
    table.add_column("Expired now", justify="right")
    for category, keep in days.items():
        count = len(retention.expired(category, keep))
        table.add_row(
            category,
            "forever" if keep <= 0 else f"{keep} days",
            str(count) if count else "[grey42]—[/grey42]",
        )
    console.print(table)

    sweep = retention.prune(overrides, dry_run=args.dry_run)
    if not sweep:
        console.print("Nothing to delete.")
        return 0
    for path in sweep.removed:
        console.print(f"  [grey42]{path}[/grey42]")
    console.print(
        f"[yellow]{retention.describe(sweep)}[/yellow]"
        + ("  [dim](dry run — nothing was touched)[/dim]" if args.dry_run else "")
    )
    return 0


def cmd_list(args) -> int:
    config.ensure_dirs()
    rows = sorted(config.AUDIO_DIR.glob("*.wav"), reverse=True)
    if not rows:
        console.print("No recordings yet.")
        return 0
    table = Table(title=str(config.DATA_DIR), header_style="bold")
    for col in ("Recording", "Size", "Transcript", "Summary"):
        table.add_column(col)
    def tick(present: bool) -> str:
        return "[green]✓[/green]" if present else "[grey42]—[/grey42]"

    for wav in rows:
        mb = wav.stat().st_size / 1e6
        table.add_row(
            wav.stem,
            f"{mb:.1f} MB",
            tick((config.TRANSCRIPT_DIR / f"{wav.stem}.md").exists()),
            tick((config.SUMMARY_DIR / f"{wav.stem}.md").exists()),
        )
    console.print(table)
    return 0


# -------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="localscribe", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def add_device_args(sp):
        sp.add_argument("--mic", help="microphone name (substring match)")
        sp.add_argument("--loopback", help="system-audio device name (substring match)")
        sp.add_argument("--system-audio", choices=["auto", "tap", "device", "off"],
                        help=f"how to capture the other side of the call "
                             f"(default {config.SYSTEM_AUDIO})")

    def add_pipeline_args(sp):
        sp.add_argument("--model", help=f"Whisper model (default {config.WHISPER_MODEL})")
        sp.add_argument("--engine", choices=list(engines.ENGINES),
                        help=f"speech engine (default {config.ENGINE}; mlx uses the "
                             f"Metal GPU and is ~2x faster on Apple Silicon)")
        sp.add_argument("--language", help="force a language code, e.g. en, he, es")
        sp.add_argument("--backend", choices=["ollama", "anthropic", "extractive"],
                        help=f"summarizer (default {config.SUMMARY_BACKEND})")
        sp.add_argument("--summary-model", help="model name for the chosen backend")
        sp.add_argument("--no-summary", action="store_true", help="transcribe only")
        sp.add_argument("--keep-audio", action="store_true",
                        help="keep the recording after summarizing "
                             f"(default: {'delete it' if config.DELETE_AUDIO_AFTER_SUMMARY else 'keep it'})")

    sp = sub.add_parser("devices", help="list audio input devices")
    sp.set_defaults(func=cmd_devices)

    sp = sub.add_parser("doctor", help="check the setup")
    add_device_args(sp)
    sp.add_argument("--engine", choices=list(engines.ENGINES))
    sp.add_argument("--backend", choices=["ollama", "anthropic", "extractive"])
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("record", help="record a meeting, then transcribe and summarize it")
    sp.add_argument("--label", "-l", help="what to call this meeting")
    sp.add_argument("--duration", "-d", help="auto-stop after e.g. 45m, 90s, 1h30m")
    sp.add_argument("--no-transcribe", action="store_true", help="just record")
    add_device_args(sp)
    add_pipeline_args(sp)
    sp.set_defaults(func=cmd_record)

    sp = sub.add_parser("process", help="transcribe + summarize an existing recording")
    sp.add_argument("audio")
    add_pipeline_args(sp)
    sp.set_defaults(func=cmd_process)

    sp = sub.add_parser("summarize", help="re-summarize an existing transcript .json")
    sp.add_argument("transcript")
    sp.add_argument("--backend", choices=["ollama", "anthropic", "extractive"])
    sp.add_argument("--summary-model")
    sp.set_defaults(func=cmd_summarize)

    sp = sub.add_parser("list", help="list past meetings")
    sp.set_defaults(func=cmd_list)

    sp = sub.add_parser("prune", help="delete recordings past their retention window")
    sp.add_argument("--days", type=int,
                    help=f"override the window (default {config.RETENTION_DAYS}; 0 keeps forever)")
    sp.add_argument("--all", action="store_true",
                    help="expire transcripts and summaries too, not just audio")
    sp.add_argument("--dry-run", action="store_true", help="list what would go, delete nothing")
    sp.set_defaults(func=cmd_prune)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
