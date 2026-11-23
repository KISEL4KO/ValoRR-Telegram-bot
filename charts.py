from io import BytesIO
from pathlib import Path
import json

from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure

# Палитра в стиле VALORANT
BG_COLOR = "#0F1923"
PANEL_COLOR = "#162432"
TEXT_COLOR = "#ECE8E1"
MUTED_COLOR = "#8B97A4"
ACCENT_RED = "#FF4655"
ACCENT_CYAN = "#00E5FF"
ACCENT_GOLD = "#F5C542"
ACCENT_PURPLE = "#A855F7"


def load_all_stats(stats_dir: Path) -> dict[str, list[dict]]:
    """Загружает все JSON из папки statistics для расчёта средних значений по игре."""
    result: dict[str, list[dict]] = {}
    if not stats_dir.exists():
        return result
    for p in stats_dir.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8-sig"))
            if isinstance(data, list):
                result[p.stem] = data
        except (OSError, json.JSONDecodeError):
            continue
    return result


def _ability_full_cost(ab: dict) -> int:
    cost = ab.get("cost") or 0
    uses = ab.get("uses") or 1
    # У сигнатурных способностей 1-й заряд бесплатный
    if ab.get("type") == "signature" and cost > 0 and uses > 1:
        return int(cost * (uses - 1))
    return int(cost * uses)


def _to_png(fig: Figure) -> bytes:
    buf = BytesIO()
    FigureCanvasAgg(fig).print_png(buf)
    buf.seek(0)
    return buf.getvalue()


def render_agent_stats(
    agent_name: str,
    stats: list[dict],
    all_stats: dict[str, list[dict]],
) -> bytes:
    """Общий дашборд по персонажу: экономика закупа + тайминги способностей."""
    fig = Figure(figsize=(10, 4.8), dpi=130, facecolor=BG_COLOR)
    ax_cost, ax_time = fig.subplots(1, 2)

    for ax in (ax_cost, ax_time):
        ax.set_facecolor(PANEL_COLOR)
        ax.tick_params(colors=TEXT_COLOR, labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#263849")

    # --- Левый график: стоимость полного закупа по способностям ---
    buyable = [ab for ab in stats if ab.get("type") in ("basic", "signature")]
    labels = [f"[{ab.get('keybind') or '?'}] {str(ab.get('name', '')).title()}" for ab in buyable]
    costs = [_ability_full_cost(ab) for ab in buyable]
    total_cost = sum(costs)

    # Средний полный закуп по всем агентам
    all_totals = [
        sum(_ability_full_cost(ab) for ab in ag if ab.get("type") in ("basic", "signature"))
        for ag in all_stats.values()
    ]
    avg_total = sum(all_totals) / len(all_totals) if all_totals else 0

    bars = ax_cost.barh(labels, costs, color=ACCENT_RED, height=0.55)
    ax_cost.invert_yaxis()
    ax_cost.set_title(
        f"Экономика (полный закуп: {total_cost} кр.)",
        color=TEXT_COLOR, fontsize=11, fontweight="bold", pad=10,
    )
    ax_cost.set_xlabel("Кредиты", color=MUTED_COLOR, fontsize=9)
    max_c = max(costs, default=200)
    ax_cost.set_xlim(0, max(max_c * 1.28, 100))

    for bar, val in zip(bars, costs):
        label = "бесплатно" if val == 0 else f"{val} кр."
        ax_cost.text(
            val + max_c * 0.03,
            bar.get_y() + bar.get_height() / 2,
            label,
            va="center", color=TEXT_COLOR, fontsize=8.5,
        )

    # --- Правый график: тайминги способностей ---
    timed_labels, windups, durations, debuffs = [], [], [], []
    for ab in stats:
        w = ab.get("windup") or 0
        d = ab.get("duration") or ab.get("duration_PF") or 0
        db = ab.get("debuff") or 0
        if w or d or db:
            timed_labels.append(f"[{ab.get('keybind') or '★'}] {str(ab.get('name', '')).title()}")
            windups.append(w)
            durations.append(d)
            debuffs.append(db)

    if timed_labels:
        y = list(range(len(timed_labels)))
        h = 0.25
        ax_time.barh([i - h for i in y], windups, height=h, color=ACCENT_GOLD, label="Задержка")
        ax_time.barh(y, durations, height=h, color=ACCENT_CYAN, label="Длительность")
        ax_time.barh([i + h for i in y], debuffs, height=h, color=ACCENT_PURPLE, label="Дебафф")
        ax_time.set_yticks(y)
        ax_time.set_yticklabels(timed_labels)
        ax_time.invert_yaxis()
        ax_time.legend(
            facecolor=BG_COLOR, edgecolor="#263849",
            labelcolor=TEXT_COLOR, fontsize=8, loc="lower right",
        )
    else:
        ax_time.text(
            0.5, 0.5, "Нет данных по таймингам",
            ha="center", va="center", color=MUTED_COLOR, transform=ax_time.transAxes,
        )

    ax_time.set_title("Тайминги способностей (сек.)", color=TEXT_COLOR, fontsize=11, fontweight="bold", pad=10)
    ax_time.set_xlabel("Секунды", color=MUTED_COLOR, fontsize=9)

    # Общий заголовок + подвал со сводкой
    ult = next((ab for ab in stats if ab.get("type") == "ultimate"), None)
    ult_pts = ult.get("ult_points") if ult else None
    all_ults = [
        ab["ult_points"]
        for ag in all_stats.values()
        for ab in ag
        if ab.get("type") == "ultimate" and ab.get("ult_points")
    ]
    avg_ult = sum(all_ults) / len(all_ults) if all_ults else 0

    footer = f"Средний закуп по игре: {avg_total:.0f} кр."
    if ult_pts is not None:
        footer += f"   |   Ульта: {ult_pts} очк. (среднее: {avg_ult:.1f})"

    fig.suptitle(f"Статистика агента: {agent_name}", color=TEXT_COLOR, fontsize=14, fontweight="bold", y=0.97)
    fig.text(0.5, 0.02, footer, ha="center", color=MUTED_COLOR, fontsize=9.5)
    fig.subplots_adjust(left=0.20, right=0.97, top=0.84, bottom=0.16, wspace=0.48)
    return _to_png(fig)


def render_ability_stats(
    agent_name: str,
    ab: dict,
    all_stats: dict[str, list[dict]],
) -> bytes | None:
    """График для конкретной способности: тайминги и сравнение цены/ульты со средним."""
    metrics: list[tuple[str, float, str]] = []
    field_map = [
        ("windup", "Задержка", ACCENT_GOLD),
        ("max_cast_duration", "Макс. каст", ACCENT_GOLD),
        ("duration", "Длительность", ACCENT_CYAN),
        ("duration_PF", "Длит. (огонь)", ACCENT_CYAN),
        ("duration_AF", "Длит. (альт.)", "#38BDF8"),
        ("debuff", "Дебафф", ACCENT_PURPLE),
        ("cooldown", "Кулдаун", ACCENT_RED),
    ]
    for key, label, color in field_map:
        val = ab.get(key)
        if isinstance(val, (int, float)) and val > 0:
            metrics.append((label, float(val), color))

    # Сравнение цены или очков ульты со средним по тому же типу способностей
    ab_type = ab.get("type")
    compare_info: tuple[str, float, float, str] | None = None
    if ab_type == "ultimate" and ab.get("ult_points"):
        vals = [
            x["ult_points"]
            for ag in all_stats.values()
            for x in ag
            if x.get("type") == "ultimate" and x.get("ult_points")
        ]
        if vals:
            compare_info = ("Очки ульты", float(ab["ult_points"]), sum(vals) / len(vals), "очк.")
    elif ab_type in ("basic", "signature") and ab.get("cost") is not None:
        vals = [
            x["cost"]
            for ag in all_stats.values()
            for x in ag
            if x.get("type") == ab_type and x.get("cost") is not None
        ]
        if vals:
            compare_info = ("Цена 1 заряда", float(ab["cost"]), sum(vals) / len(vals), "кр.")

    if not metrics and not compare_info:
        return None

    ncols = 2 if (metrics and compare_info) else 1
    fig = Figure(figsize=(8.5 if ncols == 2 else 5.5, 3.6), dpi=130, facecolor=BG_COLOR)
    axes = fig.subplots(1, ncols)
    if ncols == 1:
        axes = [axes]

    for ax in axes:
        ax.set_facecolor(PANEL_COLOR)
        ax.tick_params(colors=TEXT_COLOR, labelsize=9)
        for spine in ax.spines.values():
            spine.set_color("#263849")

    idx = 0
    if metrics:
        ax = axes[idx]
        idx += 1
        names = [m[0] for m in metrics]
        vals = [m[1] for m in metrics]
        cols = [m[2] for m in metrics]
        bars = ax.barh(names, vals, color=cols, height=0.55)
        ax.invert_yaxis()
        ax.set_title("Временные параметры (сек.)", color=TEXT_COLOR, fontsize=10.5, fontweight="bold")
        max_v = max(vals)
        ax.set_xlim(0, max_v * 1.28)
        for bar, v in zip(bars, vals):
            ax.text(
                v + max_v * 0.03,
                bar.get_y() + bar.get_height() / 2,
                f"{v:g} с",
                va="center", color=TEXT_COLOR, fontsize=8.5,
            )

    if compare_info:
        ax = axes[idx]
        title, cur_val, avg_val, unit = compare_info
        bars = ax.bar(
            ["Эта способка", "Среднее по игре"],
            [cur_val, avg_val],
            color=[ACCENT_RED, MUTED_COLOR],
            width=0.5,
        )
        ax.set_title(title, color=TEXT_COLOR, fontsize=10.5, fontweight="bold")
        max_v = max(cur_val, avg_val, 1)
        ax.set_ylim(0, max_v * 1.28)
        for bar, v in zip(bars, [cur_val, avg_val]):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                v + max_v * 0.04,
                f"{v:.1f}".rstrip("0").rstrip(".") + f" {unit}",
                ha="center", color=TEXT_COLOR, fontsize=9,
            )

    key = ab.get("keybind") or "★"
    ab_name = str(ab.get("name", "?")).title()
    fig.suptitle(f"{agent_name} — [{key}] {ab_name}", color=TEXT_COLOR, fontsize=13, fontweight="bold", y=0.96)
    fig.subplots_adjust(left=0.20 if metrics else 0.14, right=0.96, top=0.80, bottom=0.14, wspace=0.35)
    return _to_png(fig)