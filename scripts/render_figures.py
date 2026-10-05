"""Regenerate selected retrospective figures from included historical records."""
from pathlib import Path
import json
import os
from itertools import cycle

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "outputs" / ".matplotlib"))
os.environ.setdefault("XDG_CACHE_HOME", str(ROOT / "outputs" / ".cache"))
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter
import pandas as pd

plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 10,
    "axes.spines.top": False, "axes.spines.right": False,
    "axes.grid": True, "grid.alpha": 0.18, "figure.facecolor": "white",
    "axes.titleweight": "bold", "savefig.dpi": 150,
})
COLORS = ["#2563eb", "#0f766e", "#b45309"]


def save(fig, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)
    print(path.relative_to(ROOT))


def pnl_figures():
    for n in range(1, 5):
        folder = ROOT / f"rounds/round{n}"
        records = [json.loads(p.read_text()) for p in sorted((folder / "results").glob("*.json"))]
        columns = min(3, len(records))
        rows = (len(records) + columns - 1) // columns
        fig, axes = plt.subplots(rows, columns, figsize=(12, 3.6 * rows), squeeze=False)
        for ax in axes.flat[len(records):]:
            ax.set_visible(False)
        for ax, record, color in zip(axes.flat, records, cycle(COLORS)):
            curve = pd.read_csv(folder / f"results/{record['run_id']}_curve.csv")
            ax.plot(curve.timestamp, curve.pnl, color=color, linewidth=1.6)
            ax.scatter(record["activity_last_timestamp"], record["profit"], color=color, s=24, zorder=3)
            ax.axhline(0, color="#64748b", linewidth=0.8)
            label = ("Longer run" if "10000" in record["kind"] else "Backtest") if n < 4 else Path(record["algorithm"]).stem
            ax.set_title(f"{label} · {record['run_id']}\n{record['observed_timestamps']:,} observed timestamps")
            ax.set_xlabel("Timestamp")
            ax.ticklabel_format(axis="x", style="sci", scilimits=(0, 0))
            ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v / 1000:g}k"))
            ax.set_ylabel("Recorded PnL (XIRECs)")
        fig.suptitle(f"Round {n} · saved algorithmic runs", fontsize=14, fontweight="bold")
        fig.tight_layout()
        save(fig, folder / "figures/recorded_pnl.png")


def round1_market():
    frames = [pd.read_csv(p, sep=";") for p in sorted((ROOT / "data/round1").glob("prices_*.csv"))]
    prices = pd.concat(frames)
    fig, axes = plt.subplots(1, 2, figsize=(12, 3.8))
    for ax, product in zip(axes, ["ASH_COATED_OSMIUM", "INTARIAN_PEPPER_ROOT"]):
        for (day, frame), color in zip(prices[prices["product"] == product].groupby("day"), COLORS):
            frame = frame.sort_values("timestamp")
            # The source uses midpoint=0 when the book is empty. Plot those
            # observations as gaps rather than artificial price crashes.
            valid_mid = frame.mid_price.where(frame.bid_price_1.notna() & frame.ask_price_1.notna() & (frame.mid_price > 0))
            ax.plot(frame.timestamp, valid_mid, label=f"Day {day}", color=color, linewidth=0.9)
        ax.set_title(product)
        ax.set_xlabel("Timestamp")
        ax.set_ylabel("Midpoint")
        ax.legend(frameon=False)
        ax.ticklabel_format(axis="x", style="sci", scilimits=(0, 0))
    fig.suptitle("Round 1 · historical price behaviour", fontsize=14, fontweight="bold")
    fig.tight_layout()
    save(fig, ROOT / "rounds/round1/figures/market.png")


def manual_portfolios():
    data = pd.read_csv(ROOT / "rounds/round4/manual/recorded/aether_output_portfolio_comparison.csv")
    data = data.sort_values("mock_mean")
    fig, ax = plt.subplots(figsize=(10, 4))
    labels = data.portfolio.str.replace("_", " ")
    for i, (_, row) in enumerate(data.iterrows()):
        ax.plot([row.mock_p05 / 1000, row.mock_p95 / 1000], [i, i], color="#94a3b8", linewidth=4, solid_capstyle="round")
        ax.scatter(row.mock_mean / 1000, i, color=COLORS[0], s=55, zorder=3)
    ax.set_yticks(range(len(data)), labels)
    ax.axvline(0, color="#64748b", linewidth=1)
    ax.set_xlabel("Simulated 100-path portfolio score (thousands of XIRECs)")
    ax.set_title("Round 4 manual · recorded mock-game mean and 5th–95th percentile")
    fig.tight_layout()
    save(fig, ROOT / "rounds/round4/figures/manual_portfolios.png")


if __name__ == "__main__":
    pnl_figures()
    round1_market()
    manual_portfolios()
