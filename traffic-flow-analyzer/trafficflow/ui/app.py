"""Gradio dashboard. Plots and tables are deterministic; the AI brief and Q&A are explicit
button actions, so the live-ticking view never makes model calls."""

from __future__ import annotations

from collections.abc import Callable

import gradio as gr

from trafficflow.agents.pipeline import ask, run_report
from trafficflow.data.feed import Feed, SyntheticFeed
from trafficflow.ui.plots import heatmap_figure, speed_figure, status_table

BADGE = {"normal": "🟢 NORMAL", "watch": "🟡 WATCH", "degraded": "🔴 DEGRADED"}


def _refresh(feed, segment, horizon):
    return (
        f"**Simulated time:** {feed.now:%a %Y-%m-%d %H:%M}",
        speed_figure(feed, segment, int(horizon)),
        heatmap_figure(feed, int(horizon)),
        status_table(feed, int(horizon)),
    )


def build_app(
    model: str | None = None, feed_factory: Callable[[], Feed] | None = None
) -> gr.Blocks:
    """`feed_factory` builds the (per-session) feed; defaults to the simulated network."""
    make_feed = feed_factory or SyntheticFeed
    first_feed = make_feed()
    seg_choices = [(f"{s.id} {s.name}", s.id) for s in first_feed.segments()]
    with gr.Blocks(title="Traffic Flow Analyzer") as demo:
        gr.Markdown(
            "# Traffic Flow Analyzer\nCongestion forecasting with PydanticAI agents. "
            "Forecasts come from a deterministic model and agents only read tool results. "
            "The data source is a simulated or replayed recording, not a live feed."
        )
        feed = gr.State(first_feed)
        with gr.Row():
            segment = gr.Dropdown(seg_choices, value=seg_choices[0][1], label="Segment")
            horizon = gr.Slider(15, 180, value=60, step=15, label="Forecast horizon (min)")
            step_btn = gr.Button("Advance 30 min")
            live = gr.Checkbox(label="Live (advance 5 min every 5 s)", value=False)
        clock = gr.Markdown()
        with gr.Row():
            speed_plot = gr.Plot(label="Speed")
            heat_plot = gr.Plot(label="Heatmap")
        table = gr.Dataframe(label="Network status", interactive=False)

        with gr.Tab("AI briefing"):
            brief_btn = gr.Button("Generate briefing", variant="primary")
            status_md = gr.Markdown()
            brief_md = gr.Markdown()
            evidence = gr.JSON(label="Evidence")
        with gr.Tab("Ask"):
            q = gr.Textbox(label="Question", placeholder="How is Ring Road North in the next hour?")
            answer = gr.Markdown()

        outs = [clock, speed_plot, heat_plot, table]
        timer = gr.Timer(5, active=False)

        def advance(f, steps):
            f.advance(steps)
            return f

        def tick(f, seg, hz):
            f.advance(1)
            return (f, *_refresh(f, seg, hz))

        async def brief(f, hz):
            r = await run_report(f, int(hz), model)
            lines = [f"### {r.brief.headline}", r.brief.summary, "", "**Findings**"]
            lines += [f"- {x.claim} _({', '.join(x.evidence_ids)})_" for x in r.brief.findings]
            lines += ["", "**Recommendations**"] + [f"- {x}" for x in r.brief.recommendations]
            if r.issues:
                lines += ["", "⚠️ **Verification issues:** " + "; ".join(r.issues)]
            return (
                f"## {BADGE[r.status.value]}  (rule-based status, as of {r.as_of:%H:%M})",
                "\n".join(lines),
                r.evidence,
            )

        async def answer_q(f, text):
            return await ask(f, text, model) if text.strip() else ""

        demo.load(lambda f, s, h: _refresh(f, s, h), [feed, segment, horizon], outs)
        for c in (segment, horizon):
            c.change(lambda f, s, h: _refresh(f, s, h), [feed, segment, horizon], outs)
        step_btn.click(lambda f: advance(f, 6), feed, feed).then(
            lambda f, s, h: _refresh(f, s, h), [feed, segment, horizon], outs
        )
        live.change(lambda on: gr.Timer(active=on), live, timer)
        timer.tick(tick, [feed, segment, horizon], [feed, *outs])
        brief_btn.click(brief, [feed, horizon], [status_md, brief_md, evidence])
        q.submit(answer_q, [feed, q], answer)
    return demo
