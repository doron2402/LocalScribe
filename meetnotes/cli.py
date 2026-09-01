"""meetnotes — record a meeting, transcribe it offline, summarize it."""
from __future__ import annotations

import argparse
import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from . import config
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


def resolve_devices(mic_query: str | None, loopback_query: str | None):
    mic_query = config.MIC_DEVICE if mic_query is None else mic_query
    loopback_query = config.LOOPBACK_DEVICE if loopback_query is None else loopback_query
    mic = find_device(mic_query) if mic_query else default_input()
    loopback = find_device(loopback_query) if loopback_query else None
    return mic, loopback


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
    from .summarize import ollama_available, ollama_models

    ok = True
    console.print("[bold]meetnotes doctor[/bold]\n")

    mic, loopback = resolve_devices(args.mic, args.loopback)
    if mic:
        console.print(f"  [green]✓[/green] Microphone: {mic.name}")
    else:
        ok = False
        console.print("  [red]✗[/red] No microphone found. Grant mic access under "
                      "System Settings → Privacy & Security → Microphone.")
    if loopback:
        console.print(f"  [green]✓[/green] System audio: {loopback.name}")
    else:
        console.print(
            f"  [yellow]![/yellow] No loopback device matching "
            f"'{config.LOOPBACK_DEVICE}'. You will record [bold]only your own "
            f"voice[/bold]. See README → System audio."
        )

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
        else:
            ok = False
            console.print(f"  [red]✗[/red] Ollama not reachable at {config.OLLAMA_HOST}. "
                          f"`brew install ollama && ollama serve`, or use --backend extractive.")
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


def _record(args) -> Path | None:
    mic, loopback = resolve_devices(args.mic, args.loopback)
    if not mic and not loopback:
        console.print("[red]No usable input device.[/red] Run `meetnotes devices`.")
        return None
    if not loopback:
        console.print("[yellow]No system-audio loopback — recording your microphone "
                      "only.[/yellow] Run `meetnotes doctor` for setup.\n")

    label = args.label or "meeting"
    started = datetime.now()
    base = f"{slugify(label)}_{started:%Y-%m-%d_%H%M}"
    config.ensure_dirs()
    audio_path = config.AUDIO_DIR / f"{base}.wav"

    rec = Recorder(audio_path, mic, loopback)
    limit = parse_duration(args.duration)

    console.print(Panel.fit(
        f"[bold]{label}[/bold]\n"
        f"mic: {mic.name if mic else '—'}\n"
        f"sys: {loopback.name if loopback else '—'}\n"
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
                    "them": loopback.name if loopback else None},
    }, indent=2))

    console.print(f"[green]Saved[/green] {hms(rec.seconds)} → {audio_path}")
    return audio_path


def _process(audio_path: Path, args) -> int:
    from .summarize import SummaryError, summarize, write_summary
    from .transcribe import transcribe, write_transcript

    with console.status(f"Transcribing with Whisper ({args.model or config.WHISPER_MODEL})…"):
        t = transcribe(audio_path, model_name=args.model, language=args.language)
    md, js = write_transcript(t)
    console.print(f"[green]Transcript[/green] {len(t.segments)} segments → {md}")

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
    console.print(Markdown(summary))
    return 0


def cmd_record(args) -> int:
    audio_path = _record(args)
    if audio_path is None:
        return 1
    if args.no_transcribe:
        return 0
    return _process(audio_path, args)


def cmd_process(args) -> int:
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
    p = argparse.ArgumentParser(prog="meetnotes", description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)

    def add_device_args(sp):
        sp.add_argument("--mic", help="microphone name (substring match)")
        sp.add_argument("--loopback", help="system-audio device name (substring match)")

    def add_pipeline_args(sp):
        sp.add_argument("--model", help=f"Whisper model (default {config.WHISPER_MODEL})")
        sp.add_argument("--language", help="force a language code, e.g. en, he, es")
        sp.add_argument("--backend", choices=["ollama", "anthropic", "extractive"],
                        help=f"summarizer (default {config.SUMMARY_BACKEND})")
        sp.add_argument("--summary-model", help="model name for the chosen backend")
        sp.add_argument("--no-summary", action="store_true", help="transcribe only")

    sp = sub.add_parser("devices", help="list audio input devices")
    sp.set_defaults(func=cmd_devices)

    sp = sub.add_parser("doctor", help="check the setup")
    add_device_args(sp)
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
