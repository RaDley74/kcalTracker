import logging
import json
import os
from datetime import datetime, date

# ─── .env auto-create ────────────────────────────────────────────────────────

ENV_FILE = ".env"

def _ensure_env() -> None:
    """Создаёт .env с подсказкой если файла нет, затем загружает его."""
    if not os.path.exists(ENV_FILE):
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write("# Вставь сюда токен от @BotFather\n")
            f.write("BOT_TOKEN=\n")
        print(
            f"\n[!] Файл {ENV_FILE} создан.\n"
            "    Открой его и вставь токен бота:\n"
            "    BOT_TOKEN=123456789:AAF...\n"
            "    Затем запусти бота снова.\n"
        )
        raise SystemExit(0)

    # Загружаем переменные из .env вручную (без сторонних библиотек)
    with open(ENV_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key   = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and value and key not in os.environ:
                os.environ[key] = value

_ensure_env()

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# States for ConversationHandler
WAITING_FOOD, WAITING_CALORIES = range(2)

DATA_FILE = "data.json"

# 1 кг жира ≈ 7700 ккал
KCAL_PER_KG = 7700


# ─── Persistence ────────────────────────────────────────────────────────────

def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_data(data: dict) -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_today() -> str:
    return date.today().isoformat()


def get_user_data(data: dict, user_id: int) -> dict:
    uid = str(user_id)
    if uid not in data:
        data[uid] = {"goal": 2000, "log": {}}
    return data[uid]


# ─── Deficit helpers ─────────────────────────────────────────────────────────

def _calc_deficit_stats(log: dict, goal: int, days: list | None = None) -> dict:
    """
    Считает статистику дефицита за указанные дни (или за все дни).
    Возвращает dict с total_deficit, avg_deficit, days_count,
    days_deficit, days_surplus.
    """
    if days is None:
        days = list(log.keys())

    total_deficit = 0
    days_deficit = 0
    days_surplus = 0

    for day in days:
        entries = log.get(day, [])
        if not entries:
            continue
        consumed = sum(e["kcal"] for e in entries)
        diff = goal - consumed          # > 0 — дефицит, < 0 — профицит
        total_deficit += diff
        if diff > 0:
            days_deficit += 1
        else:
            days_surplus += 1

    tracked_days = days_deficit + days_surplus
    avg_deficit = round(total_deficit / tracked_days) if tracked_days else 0

    return {
        "total_deficit": total_deficit,
        "avg_deficit": avg_deficit,
        "tracked_days": tracked_days,
        "days_deficit": days_deficit,
        "days_surplus": days_surplus,
    }


def _format_deficit_block(stats: dict, label: str) -> str:
    td = stats["total_deficit"]
    avg = stats["avg_deficit"]
    td_days = stats["tracked_days"]

    if td_days == 0:
        return f"*{label}*: нет данных"

    sign = "📉" if td >= 0 else "📈"
    word = "дефицит" if td >= 0 else "профицит"
    kg_equiv = abs(td) / KCAL_PER_KG

    lines = [
        f"*{label}* ({td_days} дн.)",
        f"{sign} Суммарный {word}: *{abs(td):,}* ккал",
        f"⚖️ Эквивалент: *{'−' if td >= 0 else '+'}{kg_equiv:.2f}* кг жира",
        f"📊 Средний дефицит/день: *{avg:+}* ккал",
        f"   ✅ Дней в дефиците: {stats['days_deficit']}",
        f"   ⚠️ Дней с профицитом: {stats['days_surplus']}",
    ]
    return "\n".join(lines)


def _forecast(avg_daily_deficit: int) -> str:
    """Прогноз: сколько дней до −0.5 кг, −1 кг, −5 кг."""
    if avg_daily_deficit <= 0:
        return "📌 _Для прогноза нужен средний дефицит > 0 ккал/день_"

    lines = ["*🔮 Прогноз при текущем среднем дефиците:*\n"]
    for kg in [0.5, 1, 3, 5, 10]:
        needed = kg * KCAL_PER_KG
        days = needed / avg_daily_deficit
        if days > 3650:
            lines.append(f"  −{kg} кг: более 10 лет")
        elif days > 365:
            lines.append(f"  −{kg} кг: ~{days/365:.1f} лет")
        elif days > 30:
            lines.append(f"  −{kg} кг: ~{days/30.4:.1f} мес.")
        else:
            lines.append(f"  −{kg} кг: ~{round(days)} дней")
    lines.append("\n_* 1 кг жира ≈ 7700 ккал_")
    return "\n".join(lines)


# ─── Keyboards ──────────────────────────────────────────────────────────────

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ Добавить еду"),    KeyboardButton("📊 Сводка за день")],
        [KeyboardButton("📉 Дефицит калорий"), KeyboardButton("📅 История")],
        [KeyboardButton("🎯 Установить цель"), KeyboardButton("🗑 Очистить день")],
        [KeyboardButton("❓ Помощь")],
    ],
    resize_keyboard=True,
)

CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Отмена")]],
    resize_keyboard=True,
)


# ─── Handlers ───────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name
    await update.message.reply_text(
        f"Привет, {name}! 👋\n\n"
        "Я помогу отслеживать калории и считать дефицит.\n\n"
        "/add — добавить приём пищи\n"
        "/summary — сводка за сегодня\n"
        "/deficit — дефицит калорий\n"
        "/history — вся история\n"
        "/goal — установить дневную цель\n"
        "/clear — очистить данные за сегодня",
        reply_markup=MAIN_KEYBOARD,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "📖 *Как пользоваться ботом:*\n\n"
        "1\\. Нажми *➕ Добавить еду* или /add\n"
        "2\\. Введи название продукта\n"
        "3\\. Введи количество калорий\n\n"
        "*Быстрый ввод:* пиши сразу `Овсянка 350`\n\n"
        "📉 Кнопка *Дефицит калорий* показывает:\n"
        "  — дефицит за сегодня\n"
        "  — дефицит за 7 дней и за всё время\n"
        "  — прогноз похудения\n\n"
        "🎯 Установи цель через кнопку *Установить цель*",
        parse_mode="MarkdownV2",
        reply_markup=MAIN_KEYBOARD,
    )


# ── Add food ─────────────────────────────────────────────────────────────────

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "Введи название продукта или блюда:\n"
        "_(или сразу «Название Калории», например «Гречка 280»)_",
        parse_mode="Markdown",
        reply_markup=CANCEL_KEYBOARD,
    )
    return WAITING_FOOD


async def received_food(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    parts = text.rsplit(" ", 1)
    if len(parts) == 2:
        try:
            kcal = int(parts[1])
            context.user_data["food_name"] = parts[0]
            context.user_data["food_kcal"] = kcal
            return await _save_entry(update, context)
        except ValueError:
            pass

    context.user_data["food_name"] = text
    await update.message.reply_text(
        f"Сколько калорий в *{text}*?",
        parse_mode="Markdown",
        reply_markup=CANCEL_KEYBOARD,
    )
    return WAITING_CALORIES


async def received_calories(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    try:
        kcal = int(text)
        if kcal <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Введи целое положительное число:")
        return WAITING_CALORIES

    context.user_data["food_kcal"] = kcal
    return await _save_entry(update, context)


async def _save_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = context.user_data["food_name"]
    kcal = context.user_data["food_kcal"]
    uid  = update.effective_user.id
    today = get_today()

    data = load_data()
    user = get_user_data(data, uid)
    if today not in user["log"]:
        user["log"][today] = []

    user["log"][today].append({
        "name": name,
        "kcal": kcal,
        "time": datetime.now().strftime("%H:%M"),
    })
    save_data(data)

    total     = sum(e["kcal"] for e in user["log"][today])
    goal      = user["goal"]
    remaining = goal - total
    bar       = _progress_bar(total, goal)

    msg = (
        f"✅ *{name}* — {kcal} ккал добавлено!\n\n"
        f"{bar}\n"
        f"Сегодня: *{total}* / {goal} ккал\n"
    )
    if remaining > 0:
        msg += f"📉 Дефицит пока: *{remaining}* ккал"
    else:
        msg += f"⚠️ Превышение нормы на *{abs(remaining)}* ккал"

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


# ── Summary ──────────────────────────────────────────────────────────────────

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid   = update.effective_user.id
    today = get_today()
    data  = load_data()
    user  = get_user_data(data, uid)
    entries = user["log"].get(today, [])
    goal    = user["goal"]

    if not entries:
        await update.message.reply_text(
            "Сегодня ещё ничего не добавлено.\nНажми *➕ Добавить еду*!",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    total     = sum(e["kcal"] for e in entries)
    remaining = goal - total
    bar       = _progress_bar(total, goal)

    lines = [f"📊 *Сводка за {today}*\n", bar, f"*{total}* / {goal} ккал\n"]
    lines.append("─" * 28)
    for e in entries:
        lines.append(f"  {e['time']}  {e['name']} — {e['kcal']} ккал")
    lines.append("─" * 28)

    if remaining > 0:
        lines.append(f"📉 Дефицит за день: *{remaining}* ккал")
    else:
        lines.append(f"⚠️ Превышение: *{abs(remaining)}* ккал")

    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD
    )


# ── Deficit ───────────────────────────────────────────────────────────────────

async def deficit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid   = update.effective_user.id
    today = get_today()
    data  = load_data()
    user  = get_user_data(data, uid)
    log   = user["log"]
    goal  = user["goal"]

    if not log:
        await update.message.reply_text(
            "Нет данных для расчёта. Сначала добавь еду через *➕ Добавить еду*.",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    all_days    = sorted(log.keys())
    last_7_days = [d for d in all_days if d >= _days_ago(7)]

    # --- Дефицит за сегодня ---
    today_entries = log.get(today, [])
    today_consumed = sum(e["kcal"] for e in today_entries)
    today_deficit  = goal - today_consumed

    if today_entries:
        bar = _progress_bar(today_consumed, goal)
        if today_deficit >= 0:
            today_line = (
                f"📉 *Сегодня* ({today_consumed} / {goal} ккал)\n"
                f"{bar}\n"
                f"Дефицит: *{today_deficit}* ккал  ≈  *{today_deficit/KCAL_PER_KG:.3f}* кг"
            )
        else:
            today_line = (
                f"⚠️ *Сегодня* ({today_consumed} / {goal} ккал)\n"
                f"{bar}\n"
                f"Профицит: *{abs(today_deficit)}* ккал"
            )
    else:
        today_line = f"📌 *Сегодня* — данных нет"

    # --- За 7 дней ---
    stats_7  = _calc_deficit_stats(log, goal, last_7_days)
    block_7  = _format_deficit_block(stats_7, "За последние 7 дней")

    # --- За всё время ---
    stats_all  = _calc_deficit_stats(log, goal, all_days)
    block_all  = _format_deficit_block(stats_all, "За всё время")

    # --- Прогноз ---
    avg_for_forecast = stats_all["avg_deficit"] if stats_all["tracked_days"] >= 3 else stats_7["avg_deficit"]
    forecast_block   = _forecast(avg_for_forecast)

    msg = "\n\n".join([
        "📉 *Анализ дефицита калорий*\n",
        today_line,
        block_7,
        block_all,
        forecast_block,
    ])

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


# ── History ───────────────────────────────────────────────────────────────────

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = update.effective_user.id
    data = load_data()
    user = get_user_data(data, uid)
    log  = user["log"]
    goal = user["goal"]

    if not log:
        await update.message.reply_text(
            "История пуста — начни добавлять еду!",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    sorted_days = sorted(log.keys(), reverse=True)
    lines = [f"📅 *История за всё время ({len(sorted_days)} дн.)*\n"]

    for day in sorted_days:
        entries  = log[day]
        total    = sum(e["kcal"] for e in entries)
        deficit_val = goal - total
        if deficit_val > 0:
            status = f"✅ −{deficit_val} ккал"
        else:
            status = f"⚠️ +{abs(deficit_val)} ккал"
        lines.append(f"*{day}*: {total} ккал  {status}")

    # Разбиваем на части (лимит Telegram — 4096 символов)
    chunk, chunks = [], []
    for line in lines:
        chunk.append(line)
        if len("\n".join(chunk)) > 3800:
            chunks.append(chunk[:-1])
            chunk = [line]
    chunks.append(chunk)

    for i, part in enumerate(chunks):
        await update.message.reply_text(
            "\n".join(part),
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD if i == len(chunks) - 1 else None,
        )


# ── Goal ──────────────────────────────────────────────────────────────────────

async def set_goal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid  = update.effective_user.id
    data = load_data()
    user = get_user_data(data, uid)
    await update.message.reply_text(
        f"Текущая цель: *{user['goal']}* ккал/день\n\n"
        "Введи команду: `/goal <число>`\nНапример: `/goal 1800`",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def set_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id
    if not context.args:
        await set_goal_start(update, context)
        return

    try:
        new_goal = int(context.args[0])
        if new_goal <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Введи число: `/goal 2000`", parse_mode="Markdown")
        return

    data = load_data()
    user = get_user_data(data, uid)
    user["goal"] = new_goal
    save_data(data)

    await update.message.reply_text(
        f"🎯 Цель обновлена: *{new_goal}* ккал/день",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


# ── Clear ─────────────────────────────────────────────────────────────────────

async def clear_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid   = update.effective_user.id
    today = get_today()
    data  = load_data()
    user  = get_user_data(data, uid)

    if today in user["log"] and user["log"][today]:
        user["log"][today] = []
        save_data(data)
        await update.message.reply_text(
            f"🗑 Данные за *{today}* очищены.", parse_mode="Markdown", reply_markup=MAIN_KEYBOARD
        )
    else:
        await update.message.reply_text("Сегодня нет записей для очистки.", reply_markup=MAIN_KEYBOARD)


# ── Text router ───────────────────────────────────────────────────────────────

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    if text == "➕ Добавить еду":
        await add_start(update, context)
        context.user_data["_conv_state"] = WAITING_FOOD
    elif text == "📊 Сводка за день":
        await summary(update, context)
    elif text == "📉 Дефицит калорий":
        await deficit(update, context)
    elif text == "📅 История":
        await history(update, context)
    elif text == "🎯 Установить цель":
        await set_goal_start(update, context)
    elif text == "🗑 Очистить день":
        await clear_day(update, context)
    elif text == "❓ Помощь":
        await help_cmd(update, context)
    else:
        await update.message.reply_text(
            "Используй кнопки меню или /add для добавления еды.",
            reply_markup=MAIN_KEYBOARD,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _progress_bar(current: int, goal: int, width: int = 10) -> str:
    pct    = min(current / goal, 1.0) if goal > 0 else 0
    filled = round(pct * width)
    bar    = "🟩" * filled + "⬜" * (width - filled)
    return f"{bar} {round(pct * 100)}%"


def _days_ago(n: int) -> str:
    from datetime import timedelta
    return (date.today() - timedelta(days=n)).isoformat()


# ─── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print(
            "\n[!] BOT_TOKEN не задан.\n"
            f"    Открой файл {ENV_FILE} и вставь токен:\n"
            "    BOT_TOKEN=123456789:AAF...\n"
        )
        raise SystemExit(1)

    app = Application.builder().token(token).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("add", add_start),
            MessageHandler(filters.Regex("^➕ Добавить еду$"), add_start),
        ],
        states={
            WAITING_FOOD:     [MessageHandler(filters.TEXT & ~filters.COMMAND, received_food)],
            WAITING_CALORIES: [MessageHandler(filters.TEXT & ~filters.COMMAND, received_calories)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("summary", summary))
    app.add_handler(CommandHandler("deficit", deficit))
    app.add_handler(CommandHandler("history", history))
    app.add_handler(CommandHandler("goal",    set_goal))
    app.add_handler(CommandHandler("clear",   clear_day))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    logger.info("Бот запущен...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()