#!/usr/bin/env python3
"""Pretty-print benchmark results using rich terminal formatting.

Usage:
    python3 scripts/show_results.py                          # auto-finds latest
    python3 scripts/show_results.py artifacts/benchmark-*/   # specific run
"""

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.columns import Columns
from rich.rule import Rule

console = Console()

WEIGHT_LABELS = {
    "detection": ("Detection", "Identified an incident"),
    "correlation": ("Correlation", "Gathered evidence via tools"),
    "rca": ("Root Cause", "Correct RCA hypothesis"),
    "action_safety": ("Action Safety", "Safe remediation advice"),
    "auditability": ("Auditability", "Traceable reasoning chain"),
}

WEIGHTS = {"detection": 0.15, "correlation": 0.15, "rca": 0.35,
           "action_safety": 0.20, "auditability": 0.15}


def score_bar(score: float, width: int = 20) -> Text:
    filled = int(score * width)
    empty = width - filled
    if score >= 0.8:
        color = "green"
    elif score >= 0.5:
        color = "yellow"
    else:
        color = "red"
    bar = Text()
    bar.append("\u2588" * filled, style=color)
    bar.append("\u2591" * empty, style="dim")
    bar.append(f" {score:.0%}", style=f"bold {color}")
    return bar


def result_badge(result: str) -> Text:
    if result == "PASS":
        return Text(" PASS ", style="bold white on green")
    else:
        return Text(" FAIL ", style="bold white on red")


def find_latest_benchmark() -> Path:
    artifacts = Path("artifacts")
    if not artifacts.exists():
        console.print("[red]No artifacts/ directory found.[/red]")
        sys.exit(1)
    runs = sorted(artifacts.glob("benchmark-*"), reverse=True)
    if not runs:
        console.print("[red]No benchmark runs found in artifacts/[/red]")
        sys.exit(1)
    return runs[0]


def load_results(run_dir: Path) -> dict:
    comp_file = run_dir / "comparison.json"
    if not comp_file.exists():
        console.print(f"[red]No comparison.json in {run_dir}[/red]")
        sys.exit(1)
    with open(comp_file) as f:
        return json.load(f)


def show_results(run_dir: Path):
    data = load_results(run_dir)
    models = data.get("models", {})
    timestamp = data.get("benchmark_time", "unknown")[:19]

    # --- Header ---
    console.print()
    console.print(Panel.fit(
        "[bold white]AIOps Harness Benchmark Results[/bold white]\n"
        f"[dim]{timestamp} UTC[/dim]",
        border_style="blue",
        padding=(1, 4),
    ))
    console.print()

    # --- Find winner ---
    passing = {k: v for k, v in models.items()
               if v.get("result") == "PASS"}
    winner_key = max(passing, key=lambda k: passing[k]["weighted_score"]) if passing else None

    # --- Model cards ---
    cards = []
    for mk, m in models.items():
        is_winner = mk == winner_key
        border = "green bold" if is_winner else "dim"
        name = m["name"]
        if is_winner:
            title = f"[green]\u2605 [bold]{name}[/bold] \u2605[/green]"
        else:
            title = f"[bold]{name}[/bold]"
        body = Text()
        body.append(f"Score: {m['weighted_score']:.2f}  ")
        body.append_text(result_badge(m["result"]))
        body.append(f"\nTime:  {m['elapsed_seconds']:.1f}s")
        body.append(f"\nTools: {m['tool_calls_count']} calls")
        cards.append(Panel(body, title=title, border_style=border,
                           width=38, padding=(1, 2)))

    console.print(Columns(cards, equal=True, expand=True))
    console.print()

    # --- Score breakdown table ---
    table = Table(title="Score Breakdown by Dimension",
                  show_header=True, header_style="bold cyan",
                  border_style="blue", pad_edge=True, expand=True)
    table.add_column("Dimension", style="bold", min_width=16)
    table.add_column("Weight", justify="center", min_width=8)
    for mk in models:
        short_name = models[mk]["name"].split("(")[0].strip()
        table.add_column(short_name, justify="center", min_width=24)

    for dim_key, (dim_label, _) in WEIGHT_LABELS.items():
        row = [dim_label, f"{WEIGHTS[dim_key]:.0%}"]
        for mk in models:
            score = models[mk].get("category_scores", {}).get(dim_key, 0)
            row.append(score_bar(score, width=15))
        table.add_row(*row)

    # Totals row
    table.add_section()
    total_row = ["[bold]WEIGHTED TOTAL[/bold]", ""]
    for mk in models:
        ws = models[mk]["weighted_score"]
        color = "green" if ws >= 0.6 else "red"
        total_row.append(Text(f"{ws:.4f}", style=f"bold {color}"))
    table.add_row(*total_row)

    console.print(table)
    console.print()

    # --- RCA Hypotheses ---
    console.print(Rule("Root Cause Hypotheses", style="cyan"))
    console.print()
    for mk, m in models.items():
        is_winner = mk == winner_key
        name = m["name"].split("(")[0].strip()
        style = "green bold" if is_winner else "white"
        console.print(f"  [bold]{name}[/bold]", style=style)
        rca = m.get("rca_ranked", [])
        if rca:
            for i, h in enumerate(rca):
                marker = "\u2714" if i == 0 and is_winner else "\u2022"
                h_display = h[:90] + "..." if len(h) > 90 else h
                console.print(f"    {marker} {h_display}",
                              style="green" if i == 0 and is_winner else "dim")
        else:
            console.print("    [dim italic]No hypotheses produced[/dim italic]")
        console.print()

    # --- Detail: tool calls for each model ---
    console.print(Rule("Investigation Detail", style="cyan"))
    console.print()
    for mk, m in models.items():
        model_dir = run_dir / mk
        output_file = model_dir / "aiops_output.json"
        if not output_file.exists():
            continue
        with open(output_file) as f:
            output = json.load(f)

        name = m["name"].split("(")[0].strip()
        tc = output.get("tool_calls", [])
        console.print(f"  [bold]{name}[/bold] - {len(tc)} tool call(s):")
        for call in tc:
            tool = call.get("tool", "?")
            args = call.get("arguments", {})
            summary = call.get("result_summary", "")
            # Determine success
            is_error = '"status": "error"' in summary
            is_empty = '"resultCount": 0' in summary or '"results": []' in summary
            if is_error:
                icon, style = "\u2718", "red"
            elif is_empty:
                icon, style = "\u25cb", "yellow"
            else:
                icon, style = "\u2714", "green"

            # Format args preview
            if isinstance(args, dict):
                query = args.get("query", "")
                if query:
                    preview = query[:60] + ("..." if len(query) > 60 else "")
                else:
                    preview = json.dumps(args)[:60]
            else:
                preview = str(args)[:60]

            console.print(f"    [{style}]{icon}[/{style}] {tool}({preview})",
                          style="dim" if is_empty else "")
        console.print()

    # --- Cross-Model Judge Matrix ---
    has_judges = any(m.get("judge_scores") for m in models.values())
    if has_judges:
        console.print(Rule("Cross-Model RCA Judge Matrix", style="cyan"))
        console.print()

        judge_table = Table(
            title="Each model scores the others' RCA quality (1-10)",
            show_header=True, header_style="bold cyan",
            border_style="blue", pad_edge=True, expand=True,
        )
        judge_table.add_column("RCA by →", style="bold", min_width=16)
        for mk in models:
            short = models[mk]["name"].split("(")[0].strip()
            judge_table.add_column(f"Judged by\n{short}", justify="center", min_width=14)
        judge_table.add_column("Avg", justify="center", style="bold", min_width=8)

        for sk, sm in models.items():
            s_name = sm["name"].split("(")[0].strip()
            row = [s_name]
            scores_list = []
            for jk in models:
                if jk == sk:
                    row.append(Text("—", style="dim"))
                else:
                    js = sm.get("judge_scores", {}).get(jk, {})
                    overall = js.get("overall")
                    if isinstance(overall, (int, float)):
                        scores_list.append(overall)
                        color = "green" if overall >= 7 else ("yellow" if overall >= 5 else "red")
                        row.append(Text(f"{overall:.0f}", style=f"bold {color}"))
                    else:
                        row.append(Text("err", style="dim red"))
            avg = sum(scores_list) / len(scores_list) if scores_list else 0
            avg_color = "green" if avg >= 7 else ("yellow" if avg >= 5 else "red")
            row.append(Text(f"{avg:.1f}", style=f"bold {avg_color}"))
            judge_table.add_row(*row)

        console.print(judge_table)
        console.print()

        # Show justifications
        for sk, sm in models.items():
            s_name = sm["name"].split("(")[0].strip()
            js_all = sm.get("judge_scores", {})
            if not js_all:
                continue
            console.print(f"  [bold]{s_name}[/bold] — judge feedback:")
            for jk, js in js_all.items():
                if jk == sk:
                    continue
                j_name = models.get(jk, {}).get("name", jk).split("(")[0].strip()
                justification = js.get("justification", "no feedback")
                overall = js.get("overall", "?")
                console.print(f"    {j_name} ({overall}/10): [dim]{justification}[/dim]")
            console.print()

    # --- Footer ---
    console.print(Rule(style="blue"))
    console.print(f"  Artifacts: [link={run_dir}]{run_dir}[/link]", style="dim")
    console.print()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        run_dir = Path(sys.argv[1])
    else:
        run_dir = find_latest_benchmark()
    console.print(f"[dim]Loading results from {run_dir}...[/dim]")
    show_results(run_dir)
