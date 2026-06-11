import os
import sys
import sqlite3
import logging
import textwrap
from datetime import datetime, date, timedelta
from logging.handlers import RotatingFileHandler

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          .env  auto-create                                  ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

ENV_FILE = ".env"


def _ensure_env() -> None:
    if not os.path.exists(ENV_FILE):
        with open(ENV_FILE, "w", encoding="utf-8") as f:
            f.write("# Вставь токен от @BotFather\n")
            f.write("BOT_TOKEN=\n")
        print(
            "\n┌─ ПЕРВЫЙ ЗАПУСК ──────────────────────────────┐\n"
            f"│  Файл {ENV_FILE} создан.                         │\n"
            "│  Открой его и вставь токен бота:             │\n"
            "│  BOT_TOKEN=123456789:AAF...                   │\n"
            "│  Затем запусти бота снова.                   │\n"
            "└──────────────────────────────────────────────┘\n"
        )
        raise SystemExit(0)

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

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          Logging setup                                      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

LOGS_DIR = "logs"
os.makedirs(LOGS_DIR, exist_ok=True)


class _Color:
    RESET   = "\033[0m"
    GREY    = "\033[90m"
    CYAN    = "\033[96m"
    GREEN   = "\033[92m"
    YELLOW  = "\033[93m"
    RED     = "\033[91m"
    MAGENTA = "\033[95m"
    BOLD    = "\033[1m"
    DIM     = "\033[2m"


LEVEL_COLORS = {
    "DEBUG":    _Color.GREY,
    "INFO":     _Color.GREEN,
    "WARNING":  _Color.YELLOW,
    "ERROR":    _Color.RED,
    "CRITICAL": _Color.MAGENTA,
}

# Иконки для уровней — сразу видно в терминале
LEVEL_ICONS = {
    "DEBUG":    "·",
    "INFO":     "✓",
    "WARNING":  "⚠",
    "ERROR":    "✗",
    "CRITICAL": "☠",
}


class _ConsoleFormatter(logging.Formatter):
    """Красивый цветной форматтер для терминала."""

    WIDTH = 20  # ширина поля имени логгера

    def format(self, record: logging.LogRecord) -> str:
        ts    = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        level = record.levelname
        color = LEVEL_COLORS.get(level, "")
        icon  = LEVEL_ICONS.get(level, "?")
        name  = record.name.split(".")[-1][:self.WIDTH].ljust(self.WIDTH)
        msg   = record.getMessage()

        # Многострочные сообщения с отступом
        if "\n" in msg:
            indent = " " * (len(ts) + 3 + 2 + 3 + self.WIDTH + 3)
            lines  = msg.splitlines()
            msg    = lines[0] + "\n" + "\n".join(indent + l for l in lines[1:])

        line = (
            f"{_Color.DIM}{ts}{_Color.RESET} "
            f"{color}{icon} {_Color.BOLD}{level:<8}{_Color.RESET} "
            f"{_Color.CYAN}{name}{_Color.RESET}  "
            f"{msg}"
        )

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)

        return line


class _FileFormatter(logging.Formatter):
    """Чистый форматтер без ANSI для файла."""
    def format(self, record: logging.LogRecord) -> str:
        ts    = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")
        level = record.levelname
        name  = record.name.split(".")[-1][:22].ljust(22)
        msg   = record.getMessage()
        line  = f"{ts}  {level:<8}  {name}  {msg}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # ── Консоль (INFO+) ──────────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(_ConsoleFormatter())
    root.addHandler(console)

    # ── Файл bot.log (DEBUG+, ротация 5 МБ × 3 файла) ───────────────────────
    fh = RotatingFileHandler(
        os.path.join(LOGS_DIR, "bot.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(_FileFormatter())
    root.addHandler(fh)

    # ── Файл errors.log (WARNING+) ───────────────────────────────────────────
    eh = RotatingFileHandler(
        os.path.join(LOGS_DIR, "errors.log"),
        maxBytes=2 * 1024 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    eh.setLevel(logging.WARNING)
    eh.setFormatter(_FileFormatter())
    root.addHandler(eh)

    # Заглушаем болтливые библиотеки
    for noisy in ("httpx", "httpcore", "telegram", "apscheduler"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


_setup_logging()
logger = logging.getLogger("calorie_bot")

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          SQLite  database                                   ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

DB_FILE     = "calories.db"
KCAL_PER_KG = 7700


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    logger.info("Инициализация базы данных: %s", DB_FILE)
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                username  TEXT,
                full_name TEXT,
                goal_kcal INTEGER NOT NULL DEFAULT 2000,
                created_at TEXT NOT NULL DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS food_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(user_id),
                name       TEXT    NOT NULL,
                kcal       INTEGER NOT NULL,
                logged_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                log_date   TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_food_log_user_date
                ON food_log(user_id, log_date);

            CREATE TABLE IF NOT EXISTS workout_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL REFERENCES users(user_id),
                name       TEXT    NOT NULL,
                kcal       INTEGER NOT NULL,
                logged_at  TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                log_date   TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_workout_log_user_date
                ON workout_log(user_id, log_date);
        """)
    logger.info("База данных готова ✓")


# ── DB helpers ────────────────────────────────────────────────────────────────

def db_ensure_user(user_id: int, username: str | None, full_name: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO users(user_id, username, full_name)
               VALUES(?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                   username  = excluded.username,
                   full_name = excluded.full_name""",
            (user_id, username, full_name),
        )
    logger.debug("Пользователь синхронизирован: id=%d name=%s", user_id, full_name)


def db_get_goal(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT goal_kcal FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
    return row["goal_kcal"] if row else 2000


def db_set_goal(user_id: int, goal: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET goal_kcal=? WHERE user_id=?", (goal, user_id)
        )
    logger.info("Цель изменена: user_id=%d → %d ккал", user_id, goal)


def db_add_entry(user_id: int, name: str, kcal: int) -> None:
    today = date.today().isoformat()
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO food_log(user_id, name, kcal, logged_at, log_date) VALUES(?,?,?,?,?)",
            (user_id, name, kcal, now, today),
        )
    logger.info(
        "Запись добавлена: user_id=%d  «%s» %d ккал  date=%s",
        user_id, name, kcal, today,
    )


def db_recent_food(user_id: int, limit: int = 15) -> list[sqlite3.Row]:
    """Последние уникальные продукты пользователя (по имени, самые свежие)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT name, kcal
               FROM (
                   SELECT name, kcal,
                          ROW_NUMBER() OVER (PARTITION BY name ORDER BY logged_at DESC) AS rn
                   FROM food_log
                   WHERE user_id = ?
               )
               WHERE rn = 1
               ORDER BY (SELECT MAX(logged_at) FROM food_log
                         WHERE user_id = ? AND name = food_log.name) DESC
               LIMIT ?""",
            (user_id, user_id, limit),
        ).fetchall()


def db_get_day(user_id: int, day: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT name, kcal, logged_at FROM food_log "
            "WHERE user_id=? AND log_date=? ORDER BY logged_at",
            (user_id, day),
        ).fetchall()


def db_clear_day(user_id: int, day: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM food_log WHERE user_id=? AND log_date=?",
            (user_id, day),
        )
        deleted = cur.rowcount
    logger.info("Очищен день %s для user_id=%d (%d записей)", day, user_id, deleted)
    return deleted


def db_all_days(user_id: int) -> list[tuple]:
    """Возвращает [(log_date, total_kcal, count)] desc."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT log_date,
                      SUM(kcal) AS total_kcal,
                      COUNT(*)  AS cnt
               FROM food_log
               WHERE user_id=?
               GROUP BY log_date
               ORDER BY log_date DESC""",
            (user_id,),
        ).fetchall()


def db_deficit_stats(user_id: int, since: str | None = None) -> dict:
    """Статистика дефицита с опциональным фильтром по дате.
    Учитывает сожжённые калории тренировок: дефицит = цель − (еда − тренировки).
    """
    goal = db_get_goal(user_id)
    params: list = [user_id]
    where = "WHERE user_id=?"
    if since:
        where += " AND log_date >= ?"
        params.append(since)

    with get_conn() as conn:
        # Калории из еды по дням
        food_rows = conn.execute(
            f"""SELECT log_date, SUM(kcal) AS total
                FROM food_log {where}
                GROUP BY log_date""",
            params,
        ).fetchall()

        # Калории тренировок по дням
        workout_rows = conn.execute(
            f"""SELECT log_date, SUM(kcal) AS total
                FROM workout_log {where}
                GROUP BY log_date""",
            params,
        ).fetchall()

    if not food_rows:
        return {"total_deficit": 0, "avg_deficit": 0,
                "tracked_days": 0, "days_deficit": 0, "days_surplus": 0}

    workout_map = {r["log_date"]: r["total"] for r in workout_rows}

    total_deficit = 0
    days_deficit  = 0
    days_surplus  = 0
    for row in food_rows:
        burned = workout_map.get(row["log_date"], 0)
        net    = row["total"] - burned          # чистые калории за день
        diff   = goal - net                     # дефицит (+) / профицит (−)
        total_deficit += diff
        if diff > 0:
            days_deficit += 1
        else:
            days_surplus += 1

    tracked = days_deficit + days_surplus
    return {
        "total_deficit": total_deficit,
        "avg_deficit":   round(total_deficit / tracked) if tracked else 0,
        "tracked_days":  tracked,
        "days_deficit":  days_deficit,
        "days_surplus":  days_surplus,
    }


def db_add_workout(user_id: int, name: str, kcal: int) -> None:
    today = date.today().isoformat()
    now   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO workout_log(user_id, name, kcal, logged_at, log_date) VALUES(?,?,?,?,?)",
            (user_id, name, kcal, now, today),
        )
    logger.info(
        "Тренировка добавлена: user_id=%d  «%s» %d ккал  date=%s",
        user_id, name, kcal, today,
    )


def db_get_workouts_day(user_id: int, day: str) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT name, kcal, logged_at FROM workout_log "
            "WHERE user_id=? AND log_date=? ORDER BY logged_at",
            (user_id, day),
        ).fetchall()


def db_workout_kcal_day(user_id: int, day: str) -> int:
    """Суммарные калории тренировок за день."""
    rows = db_get_workouts_day(user_id, day)
    return sum(r["kcal"] for r in rows)


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          Telegram bot                                       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters, ConversationHandler,
)

(
    WAITING_FOOD, WAITING_CALORIES,
    WAITING_WORKOUT, WAITING_WORKOUT_KCAL,
    TREADMILL_HR, TREADMILL_INCLINE, TREADMILL_SPEED,
    TREADMILL_DURATION, TREADMILL_AGE, TREADMILL_WEIGHT,
) = range(10)

# ── Keyboards ─────────────────────────────────────────────────────────────────

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ Добавить еду"),      KeyboardButton("🏋️ Добавить тренировку")],
        [KeyboardButton("🏃 Беговая дорожка"),   KeyboardButton("📊 Сводка за день")],
        [KeyboardButton("📉 Дефицит калорий"),   KeyboardButton("📅 История")],
        [KeyboardButton("🎯 Установить цель"),   KeyboardButton("🗑 Очистить день")],
        [KeyboardButton("❓ Помощь")],
    ],
    resize_keyboard=True,
)

CANCEL_KEYBOARD = ReplyKeyboardMarkup(
    [[KeyboardButton("❌ Отмена")]],
    resize_keyboard=True,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _today() -> str:
    return date.today().isoformat()


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _progress_bar(current: int, goal: int, width: int = 10) -> str:
    """
    Динамичный прогресс-бар с цветовой индикацией:
      🟩 зелёный — до 80% нормы
      🟨 жёлтый  — 80–100%
      🟥 красный  — при превышении нормы
    """
    if goal <= 0:
        return "⬜" * width + " 0%"

    pct         = current / goal
    filled      = min(round(pct * width), width)
    pct_display = round(pct * 100)

    if pct <= 0.80:
        bar = "🟩" * filled + "⬜" * (width - filled)
    elif pct <= 1.0:
        bar = "🟨" * filled + "⬜" * (width - filled)
    else:
        bar = "🟥" * width  # весь бар красный при превышении

    return f"{bar} {pct_display}%"


def _kcal_badge(kcal: int) -> str:
    """Цветной значок калорийности для списка приёмов пищи."""
    if kcal < 100:
        return "🟢"   # лёгкий перекус
    elif kcal < 300:
        return "🟡"   # средний приём
    elif kcal < 600:
        return "🟠"   # калорийное блюдо
    return "🔴"        # очень калорийно


def _format_deficit_block(stats: dict, label: str) -> str:
    td      = stats["total_deficit"]
    avg     = stats["avg_deficit"]
    tracked = stats["tracked_days"]

    if tracked == 0:
        return f"*{label}*: нет данных"

    sign     = "📉" if td >= 0 else "📈"
    word     = "дефицит" if td >= 0 else "профицит"
    kg_equiv = abs(td) / KCAL_PER_KG

    return "\n".join([
        f"*{label}* ({tracked} дн.)",
        f"{sign} Суммарный {word}: *{abs(td):,}* ккал",
        f"⚖️ Эквивалент: *{'−' if td >= 0 else '+'}{kg_equiv:.2f}* кг жира",
        f"📊 Средний дефицит/день: *{avg:+}* ккал",
        f"   ✅ Дней в дефиците: {stats['days_deficit']}",
        f"   ⚠️ Дней с профицитом: {stats['days_surplus']}",
    ])


def _forecast(avg_daily_deficit: int) -> str:
    if avg_daily_deficit <= 0:
        return "📌 _Для прогноза нужен средний дефицит > 0 ккал/день_"
    lines = ["*🔮 Прогноз при текущем среднем дефиците:*\n"]
    for kg in [0.5, 1, 3, 5, 10]:
        days = (kg * KCAL_PER_KG) / avg_daily_deficit
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


def _sync_user(update: Update) -> None:
    u = update.effective_user
    db_ensure_user(u.id, u.username, u.full_name)


def _split_message(lines: list[str], limit: int = 3800) -> list[list[str]]:
    chunk, chunks = [], []
    for line in lines:
        chunk.append(line)
        if len("\n".join(chunk)) > limit:
            chunks.append(chunk[:-1])
            chunk = [line]
    chunks.append(chunk)
    return chunks


# ── Handlers ──────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    u = update.effective_user
    logger.info("Команда /start: user_id=%d (%s)", u.id, u.full_name)
    await update.message.reply_text(
        f"Привет, *{u.first_name}*! 👋\n\n"
        "Я помогу отслеживать калории и считать дефицит.\n\n"
        "📌 *Команды:*\n"
        "/add — добавить приём пищи\n"
        "/workout — добавить тренировку\n"
        "/treadmill — калькулятор беговой дорожки\n"
        "/summary — сводка за сегодня\n"
        "/deficit — дефицит калорий\n"
        "/history — вся история\n"
        "/goal — установить дневную цель\n"
        "/clear — очистить данные за сегодня\n\n"
        "_Или просто используй кнопки меню 👇_",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    logger.debug("Команда /help: user_id=%d", update.effective_user.id)
    await update.message.reply_text(
        "📖 *Как пользоваться ботом:*\n\n"
        "1\\. Нажми *➕ Добавить еду* или /add\n"
        "2\\. Введи название продукта\n"
        "3\\. Введи количество калорий\n\n"
        "*Быстрый ввод еды:* пиши сразу `Овсянка 350`\n\n"
        "🏋️ Нажми *Добавить тренировку* или /workout\n"
        "   Введи название и сожжённые калории\\.\n"
        "   Тренировки вычитаются из суточного потребления\\.\n\n"
        "📉 Кнопка *Дефицит калорий* показывает:\n"
        "  — дефицит за сегодня \\(с учётом тренировок\\)\n"
        "  — дефицит за 7 дней и за всё время\n"
        "  — прогноз похудения\n\n"
        "🎯 Установи цель через кнопку *Установить цель*\n\n"
        "🎨 *Значки калорийности:*\n"
        "🟢 < 100 ккал   🟡 100–299   🟠 300–599   🔴 600+",
        parse_mode="MarkdownV2",
        reply_markup=MAIN_KEYBOARD,
    )


# ── Add food ──────────────────────────────────────────────────────────────────

def _build_food_keyboard(uid: int) -> ReplyKeyboardMarkup:
    """Клавиатура с последними 15 уникальными продуктами + кнопка отмены."""
    recent = db_recent_food(uid, limit=15)
    rows = []
    for i in range(0, len(recent), 2):
        pair = []
        for r in recent[i:i+2]:
            pair.append(KeyboardButton(f"\U0001f4cc {r['name']} \u2022 {r['kcal']} ккал"))
        rows.append(pair)
    rows.append([KeyboardButton("\u274c Отмена")])
    return ReplyKeyboardMarkup(rows, resize_keyboard=True)


async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _sync_user(update)
    uid = update.effective_user.id
    logger.debug("Начало добавления еды: user_id=%d", uid)

    recent = db_recent_food(uid, limit=15)
    if recent:
        keyboard = _build_food_keyboard(uid)
        hint = (
            "Введи название продукта или блюда:\n"
            "_(или сразу «Название Калории», например «Гречка 280»)_\n\n"
            "\u2b07\ufe0f Или выбери из недавних:"
        )
    else:
        keyboard = CANCEL_KEYBOARD
        hint = (
            "Введи название продукта или блюда:\n"
            "_(или сразу «Название Калории», например «Гречка 280»)_"
        )

    await update.message.reply_text(hint, parse_mode="Markdown", reply_markup=keyboard)
    return WAITING_FOOD


async def received_food(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid  = update.effective_user.id

    if text == "❌ Отмена":
        logger.debug("Отмена добавления еды: user_id=%d", uid)
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    # Кнопка из истории: "📌 Название • 350 ккал"
    import re
    history_match = re.match(r"^📌 (.+) • (\d+) ккал$", text)
    if history_match:
        food_name = history_match.group(1)
        kcal      = int(history_match.group(2))
        context.user_data["food_name"] = food_name
        context.user_data["food_kcal"] = kcal
        logger.debug("Быстрый выбор из истории: user_id=%d «%s» %d ккал", uid, food_name, kcal)
        return await _save_entry(update, context)

    # Быстрый ввод: "Гречка 280"
    parts = text.rsplit(" ", 1)
    if len(parts) == 2:
        try:
            kcal = int(parts[1])
            if kcal <= 0:
                raise ValueError
            context.user_data["food_name"] = parts[0]
            context.user_data["food_kcal"] = kcal
            logger.debug("Быстрый ввод: user_id=%d «%s» %d ккал", uid, parts[0], kcal)
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
    uid  = update.effective_user.id

    if text == "❌ Отмена":
        logger.debug("Отмена ввода калорий: user_id=%d", uid)
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    try:
        kcal = int(text)
        if kcal <= 0:
            raise ValueError
    except ValueError:
        logger.warning("Некорректные калории от user_id=%d: «%s»", uid, text)
        await update.message.reply_text("⚠️ Введи целое положительное число:")
        return WAITING_CALORIES

    context.user_data["food_kcal"] = kcal
    return await _save_entry(update, context)


async def _save_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name  = context.user_data["food_name"]
    kcal  = context.user_data["food_kcal"]
    uid   = update.effective_user.id
    badge = _kcal_badge(kcal)

    db_add_entry(uid, name, kcal)

    entries   = db_get_day(uid, _today())
    total     = sum(r["kcal"] for r in entries)
    burned    = db_workout_kcal_day(uid, _today())
    net       = total - burned
    goal      = db_get_goal(uid)
    remaining = goal - net
    bar       = _progress_bar(net, goal)

    logger.info(
        "Итого за день: user_id=%d  total=%d  burned=%d  net=%d  goal=%d",
        uid, total, burned, net, goal,
    )

    msg = (
        f"✅ {badge} *{name}* — {kcal} ккал добавлено!\n\n"
        f"{bar}\n"
        f"Съедено: *{total}* ккал"
        + (f"  |  🔥 Сожжено: *{burned}* ккал" if burned else "") + "\n"
        f"Чистые калории: *{net}* / {goal} ккал\n"
    )
    msg += (
        f"📉 Ещё можно: *{remaining}* ккал"
        if remaining > 0 else
        f"⚠️ Превышение нормы на *{abs(remaining)}* ккал"
    )

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


# ── Add workout ───────────────────────────────────────────────────────────────

async def workout_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _sync_user(update)
    logger.debug("Начало добавления тренировки: user_id=%d", update.effective_user.id)
    await update.message.reply_text(
        "Введи название тренировки:\n"
        "_(или сразу «Название Калории», например «Бег 300»)_",
        parse_mode="Markdown",
        reply_markup=CANCEL_KEYBOARD,
    )
    return WAITING_WORKOUT


async def received_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid  = update.effective_user.id

    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    parts = text.rsplit(" ", 1)
    if len(parts) == 2:
        try:
            kcal = int(parts[1])
            if kcal <= 0:
                raise ValueError
            context.user_data["workout_name"] = parts[0]
            context.user_data["workout_kcal"] = kcal
            return await _save_workout(update, context)
        except ValueError:
            pass

    context.user_data["workout_name"] = text
    await update.message.reply_text(
        f"Сколько калорий сожжено в тренировке *{text}*?",
        parse_mode="Markdown",
        reply_markup=CANCEL_KEYBOARD,
    )
    return WAITING_WORKOUT_KCAL


async def received_workout_kcal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid  = update.effective_user.id

    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    try:
        kcal = int(text)
        if kcal <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Введи целое положительное число:")
        return WAITING_WORKOUT_KCAL

    context.user_data["workout_kcal"] = kcal
    return await _save_workout(update, context)


async def _save_workout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = context.user_data["workout_name"]
    kcal = context.user_data["workout_kcal"]
    uid  = update.effective_user.id

    db_add_workout(uid, name, kcal)

    today    = _today()
    consumed = sum(r["kcal"] for r in db_get_day(uid, today))
    burned   = db_workout_kcal_day(uid, today)
    net      = consumed - burned
    goal     = db_get_goal(uid)
    remaining = goal - net
    bar      = _progress_bar(net, goal)

    logger.info(
        "Тренировка сохранена: user_id=%d  «%s» %d ккал  net=%d",
        uid, name, kcal, net,
    )

    msg = (
        f"🏋️ *{name}* — сожжено *{kcal}* ккал!\n\n"
        f"{bar}\n"
        f"Съедено: *{consumed}* ккал  |  🔥 Сожжено: *{burned}* ккал\n"
        f"Чистые калории: *{net}* / {goal} ккал\n"
    )
    msg += (
        f"📉 Ещё можно: *{remaining}* ккал"
        if remaining > 0 else
        f"⚠️ Превышение нормы на *{abs(remaining)}* ккал"
    )

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


# ── Treadmill calculator ──────────────────────────────────────────────────────

def _calc_treadmill(
    avg_hr: float,
    incline: float,
    speed_kmh: float,
    duration_min: float,
    age: int,
    weight_kg: float,
) -> dict:
    """
    Расчёт по методу ACSM + поправка на ЧСС.
    Источник: calcCallOnThreadmill.py
    """
    speed_m_min   = speed_kmh * 1000 / 60
    met_horizontal = 0.1 * speed_m_min
    met_vertical   = 0.9 * (incline / 100) * speed_m_min
    vo2            = met_horizontal + met_vertical + 3.5
    MET            = vo2 / 3.5

    hr_max   = max(220 - age, avg_hr + 1)
    hr_ratio = avg_hr / hr_max
    hr_factor = max(0.7, min(1.0 + 0.5 * (hr_ratio - 0.5), 1.35))

    cal_per_min = (MET * 3.5 * weight_kg / 200) * hr_factor
    total_kcal  = cal_per_min * duration_min
    distance_km = speed_kmh * (duration_min / 60)

    return {
        "total_kcal":   round(total_kcal, 1),
        "cal_per_min":  round(cal_per_min, 2),
        "MET":          round(MET, 2),
        "hr_factor":    round(hr_factor, 3),
        "distance_km":  round(distance_km, 2),
        "vo2":          round(vo2, 1),
    }


def _parse_positive_float(text: str, min_val: float, max_val: float):
    """None если некорректно, иначе float."""
    try:
        val = float(text.replace(",", "."))
        if min_val <= val <= max_val:
            return val
    except ValueError:
        pass
    return None


async def treadmill_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _sync_user(update)
    logger.debug("Калькулятор дорожки: user_id=%d", update.effective_user.id)
    await update.message.reply_text(
        "🏃 *Калькулятор беговой дорожки*\n\n"
        "Введи среднюю *ЧСС* во время тренировки (уд/мин, 40–220):",
        parse_mode="Markdown",
        reply_markup=CANCEL_KEYBOARD,
    )
    return TREADMILL_HR


async def _treadmill_cancel_check(update: Update) -> bool:
    return update.message.text.strip() == "❌ Отмена"


async def treadmill_hr(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _treadmill_cancel_check(update):
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 40, 220)
    if val is None:
        await update.message.reply_text("⚠️ Введи число от 40 до 220:")
        return TREADMILL_HR
    context.user_data["tm_hr"] = val
    await update.message.reply_text("Уклон дорожки (%, 0–30):", reply_markup=CANCEL_KEYBOARD)
    return TREADMILL_INCLINE


async def treadmill_incline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _treadmill_cancel_check(update):
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 0, 30)
    if val is None:
        await update.message.reply_text("⚠️ Введи число от 0 до 30:")
        return TREADMILL_INCLINE
    context.user_data["tm_incline"] = val
    await update.message.reply_text("Средняя скорость (км/ч, 1–30):", reply_markup=CANCEL_KEYBOARD)
    return TREADMILL_SPEED


async def treadmill_speed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _treadmill_cancel_check(update):
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 1, 30)
    if val is None:
        await update.message.reply_text("⚠️ Введи число от 1 до 30:")
        return TREADMILL_SPEED
    context.user_data["tm_speed"] = val
    await update.message.reply_text("Время тренировки (мин, 1–300):", reply_markup=CANCEL_KEYBOARD)
    return TREADMILL_DURATION


async def treadmill_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _treadmill_cancel_check(update):
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 1, 300)
    if val is None:
        await update.message.reply_text("⚠️ Введи число от 1 до 300:")
        return TREADMILL_DURATION
    context.user_data["tm_duration"] = val
    await update.message.reply_text("Твой возраст (лет, 10–100):", reply_markup=CANCEL_KEYBOARD)
    return TREADMILL_AGE


async def treadmill_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _treadmill_cancel_check(update):
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 10, 100)
    if val is None:
        await update.message.reply_text("⚠️ Введи число от 10 до 100:")
        return TREADMILL_AGE
    context.user_data["tm_age"] = int(val)
    await update.message.reply_text("Масса тела (кг, 30–250):", reply_markup=CANCEL_KEYBOARD)
    return TREADMILL_WEIGHT


async def treadmill_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if await _treadmill_cancel_check(update):
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 30, 250)
    if val is None:
        await update.message.reply_text("⚠️ Введи число от 30 до 250:")
        return TREADMILL_WEIGHT
    context.user_data["tm_weight"] = val

    ud  = context.user_data
    res = _calc_treadmill(
        avg_hr      = ud["tm_hr"],
        incline     = ud["tm_incline"],
        speed_kmh   = ud["tm_speed"],
        duration_min= ud["tm_duration"],
        age         = ud["tm_age"],
        weight_kg   = ud["tm_weight"],
    )
    uid = update.effective_user.id
    logger.info(
        "Калькулятор дорожки: user_id=%d  kcal=%.1f  dist=%.2f km",
        uid, res["total_kcal"], res["distance_km"],
    )

    # Предложение добавить как тренировку
    from telegram import InlineKeyboardMarkup, InlineKeyboardButton
    add_btn = InlineKeyboardMarkup([[
        InlineKeyboardButton(
            f"➕ Добавить {res['total_kcal']} ккал как тренировку",
            callback_data=f"tm_add:{res['total_kcal']}:Беговая дорожка",
        )
    ]])

    msg = (
        "🏃 *Результат расчёта*\n\n"
        f"🛣 Дистанция:        *{res['distance_km']} км*\n"
        f"💧 VO₂:             *{res['vo2']} мл/кг/мин*\n"
        f"⚡ MET:             *{res['MET']}*\n"
        f"❤️ ЧСС-коэффициент: *{res['hr_factor']}*\n"
        f"🔥 Калорий/мин:     *{res['cal_per_min']} ккал/мин*\n"
        "─────────────────────────\n"
        f"✅ *Итого сожжено: {res['total_kcal']} ккал*"
    )
    await update.message.reply_text(
        msg, parse_mode="Markdown",
        reply_markup=add_btn,
    )
    return ConversationHandler.END


async def treadmill_add_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline-кнопка «Добавить как тренировку»."""
    query = update.callback_query
    await query.answer()
    _, kcal_str, name = query.data.split(":", 2)
    kcal = round(float(kcal_str))
    uid  = query.from_user.id
    db_ensure_user(uid, query.from_user.username, query.from_user.full_name)
    db_add_workout(uid, name, kcal)

    today    = _today()
    consumed = sum(r["kcal"] for r in db_get_day(uid, today))
    burned   = db_workout_kcal_day(uid, today)
    net      = consumed - burned
    goal     = db_get_goal(uid)
    remaining = goal - net
    bar      = _progress_bar(net, goal)

    logger.info("Тренировка с дорожки добавлена: user_id=%d  %d ккал", uid, kcal)

    await query.edit_message_text(
        f"✅ *{name}* — {kcal} ккал добавлено в тренировки!\n\n"
        f"{bar}\n"
        f"Чистые калории: *{net}* / {goal} ккал\n"
        + (f"📉 Ещё можно: *{remaining}* ккал" if remaining > 0
           else f"⚠️ Превышение нормы на *{abs(remaining)}* ккал"),
        parse_mode="Markdown",
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    logger.debug("ConversationHandler отменён: user_id=%d", update.effective_user.id)
    await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


# ── Summary ───────────────────────────────────────────────────────────────────

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    uid     = update.effective_user.id
    today   = _today()
    entries = db_get_day(uid, today)
    goal    = db_get_goal(uid)

    logger.info("Сводка запрошена: user_id=%d  записей=%d", uid, len(entries))

    if not entries:
        await update.message.reply_text(
            "Сегодня ещё ничего не добавлено.\nНажми *➕ Добавить еду*!",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    total     = sum(r["kcal"] for r in entries)
    burned    = db_workout_kcal_day(uid, today)
    net       = total - burned
    remaining = goal - net
    bar       = _progress_bar(net, goal)

    lines = [f"📊 *Сводка за {today}*\n"]
    lines.append(bar)
    lines.append(f"*{net}* / {goal} ккал (чистые)\n")
    lines.append("─" * 26)
    lines.append("🍽 *Еда:*")

    for r in entries:
        time_str = r["logged_at"][11:16]
        badge    = _kcal_badge(r["kcal"])
        lines.append(f"  {badge} `{time_str}`  {r['name']} — *{r['kcal']}* ккал")

    workout_rows = db_get_workouts_day(uid, today)
    if workout_rows:
        lines.append("─" * 26)
        lines.append("🏋️ *Тренировки:*")
        for r in workout_rows:
            time_str = r["logged_at"][11:16]
            lines.append(f"  🔥 `{time_str}`  {r['name']} — −*{r['kcal']}* ккал")
        lines.append(f"  Итого сожжено: *{burned}* ккал")

    lines.append("─" * 26)
    lines.append(f"🍽 Съедено: *{total}* ккал  |  🔥 Сожжено: *{burned}* ккал")
    lines.append(
        f"📉 Дефицит за день: *{remaining}* ккал"
        if remaining > 0 else
        f"⚠️ Превышение: *{abs(remaining)}* ккал"
    )

    await update.message.reply_text(
        "\n".join(lines), parse_mode="Markdown", reply_markup=MAIN_KEYBOARD
    )


# ── Deficit ───────────────────────────────────────────────────────────────────

async def deficit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    uid   = update.effective_user.id
    today = _today()
    goal  = db_get_goal(uid)

    logger.info("Дефицит запрошен: user_id=%d  goal=%d", uid, goal)

    all_rows = db_all_days(uid)
    if not all_rows:
        await update.message.reply_text(
            "Нет данных. Сначала добавь еду через *➕ Добавить еду*.",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    today_entries  = db_get_day(uid, today)
    today_consumed = sum(r["kcal"] for r in today_entries)
    today_burned   = db_workout_kcal_day(uid, today)
    today_net      = today_consumed - today_burned
    today_deficit  = goal - today_net

    if today_entries:
        bar = _progress_bar(today_net, goal)
        burned_line = f"  🔥 Сожжено: *{today_burned}* ккал\n" if today_burned else ""
        if today_deficit >= 0:
            today_block = (
                f"📉 *Сегодня* ({today_net} / {goal} ккал)\n"
                f"{bar}\n"
                f"{burned_line}"
                f"Дефицит: *{today_deficit}* ккал  ≈  *{today_deficit / KCAL_PER_KG:.3f}* кг"
            )
        else:
            today_block = (
                f"⚠️ *Сегодня* ({today_net} / {goal} ккал)\n"
                f"{_progress_bar(today_net, goal)}\n"
                f"{burned_line}"
                f"Профицит: *{abs(today_deficit)}* ккал"
            )
    else:
        today_block = "📌 *Сегодня* — данных нет"

    stats_7   = db_deficit_stats(uid, since=_days_ago(7))
    stats_all = db_deficit_stats(uid)

    avg_forecast = (
        stats_all["avg_deficit"] if stats_all["tracked_days"] >= 3
        else stats_7["avg_deficit"]
    )

    logger.debug(
        "Дефицит stats: user_id=%d  7d_avg=%d  all_avg=%d",
        uid, stats_7["avg_deficit"], stats_all["avg_deficit"],
    )

    msg = "\n\n".join([
        "📉 *Анализ дефицита калорий*\n",
        today_block,
        _format_deficit_block(stats_7,   "За последние 7 дней"),
        _format_deficit_block(stats_all, "За всё время"),
        _forecast(avg_forecast),
    ])

    await update.message.reply_text(msg, parse_mode="Markdown", reply_markup=MAIN_KEYBOARD)


# ── History ───────────────────────────────────────────────────────────────────

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    uid  = update.effective_user.id
    goal = db_get_goal(uid)
    rows = db_all_days(uid)

    logger.info("История запрошена: user_id=%d  дней=%d", uid, len(rows))

    if not rows:
        await update.message.reply_text(
            "История пуста — начни добавлять еду!",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    lines = [f"📅 *История за всё время ({len(rows)} дн.)*\n"]
    for r in rows:
        deficit_val = goal - r["total_kcal"]
        if deficit_val > 0:
            status   = f"✅ −{deficit_val}"
            bar_mini = "🟩"
        else:
            status   = f"⚠️ +{abs(deficit_val)}"
            bar_mini = "🟥"
        lines.append(
            f"{bar_mini} *{r['log_date']}*: {r['total_kcal']} ккал  "
            f"{status} ккал  `{r['cnt']} зап.`"
        )

    for i, part in enumerate(_split_message(lines)):
        await update.message.reply_text(
            "\n".join(part),
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD if i == len(_split_message(lines)) - 1 else None,
        )


# ── Goal ──────────────────────────────────────────────────────────────────────

async def set_goal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    uid  = update.effective_user.id
    goal = db_get_goal(uid)
    logger.debug("Просмотр цели: user_id=%d  goal=%d", uid, goal)
    await update.message.reply_text(
        f"Текущая цель: *{goal}* ккал/день\n\n"
        "Введи команду: `/goal <число>`\nНапример: `/goal 1800`",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


async def set_goal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    uid = update.effective_user.id
    if not context.args:
        await set_goal_start(update, context)
        return

    try:
        new_goal = int(context.args[0])
        if new_goal <= 0:
            raise ValueError
    except ValueError:
        logger.warning("Некорректная цель от user_id=%d: %s", uid, context.args)
        await update.message.reply_text("⚠️ Введи число: `/goal 2000`", parse_mode="Markdown")
        return

    db_set_goal(uid, new_goal)
    await update.message.reply_text(
        f"🎯 Цель обновлена: *{new_goal}* ккал/день",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


# ── Clear ─────────────────────────────────────────────────────────────────────

async def clear_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    uid     = update.effective_user.id
    today   = _today()
    deleted = db_clear_day(uid, today)

    if deleted:
        await update.message.reply_text(
            f"🗑 Данные за *{today}* очищены ({deleted} записей).",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
    else:
        await update.message.reply_text(
            "Сегодня нет записей для очистки.",
            reply_markup=MAIN_KEYBOARD,
        )


# ── Error handler ─────────────────────────────────────────────────────────────

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(
        "Необработанное исключение:\n%s",
        context.error,
        exc_info=context.error,
    )


# ── Text router ───────────────────────────────────────────────────────────────

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = update.message.text
    uid  = update.effective_user.id
    logger.debug("Текстовое сообщение: user_id=%d  «%s»", uid, text[:60])

    routes = {
        "➕ Добавить еду":        add_start,
        "🏋️ Добавить тренировку": workout_start,
        "🏃 Беговая дорожка":     treadmill_start,
        "📊 Сводка за день":      summary,
        "📉 Дефицит калорий":     deficit,
        "📅 История":             history,
        "🎯 Установить цель":     set_goal_start,
        "🗑 Очистить день":       clear_day,
        "❓ Помощь":              help_cmd,
    }

    handler = routes.get(text)
    if handler:
        if handler is add_start:
            context.user_data["_conv_state"] = WAITING_FOOD
        if handler is workout_start:
            context.user_data["_conv_state"] = WAITING_WORKOUT
        await handler(update, context)
    else:
        await update.message.reply_text(
            "Используй кнопки меню или /add для добавления еды.",
            reply_markup=MAIN_KEYBOARD,
        )


# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                          Entry point                                        ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

def main() -> None:
    token = os.environ.get("BOT_TOKEN")
    if not token:
        print(
            "\n┌─ ОШИБКА ─────────────────────────────────────┐\n"
            "│  BOT_TOKEN не задан.                         │\n"
            f"│  Открой файл {ENV_FILE} и вставь токен:         │\n"
            "│  BOT_TOKEN=123456789:AAF...                   │\n"
            "└──────────────────────────────────────────────┘\n"
        )
        raise SystemExit(1)

    init_db()

    logger.info("━" * 60)
    logger.info("  🍏 Calorie Bot  |  запуск  |  db=%s", DB_FILE)
    logger.info("━" * 60)

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

    workout_conv = ConversationHandler(
        entry_points=[
            CommandHandler("workout", workout_start),
            MessageHandler(filters.Regex("^🏋️ Добавить тренировку$"), workout_start),
        ],
        states={
            WAITING_WORKOUT:     [MessageHandler(filters.TEXT & ~filters.COMMAND, received_workout)],
            WAITING_WORKOUT_KCAL:[MessageHandler(filters.TEXT & ~filters.COMMAND, received_workout_kcal)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    from telegram.ext import CallbackQueryHandler

    treadmill_conv = ConversationHandler(
        entry_points=[
            CommandHandler("treadmill", treadmill_start),
            MessageHandler(filters.Regex("^🏃 Беговая дорожка$"), treadmill_start),
        ],
        states={
            TREADMILL_HR:       [MessageHandler(filters.TEXT & ~filters.COMMAND, treadmill_hr)],
            TREADMILL_INCLINE:  [MessageHandler(filters.TEXT & ~filters.COMMAND, treadmill_incline)],
            TREADMILL_SPEED:    [MessageHandler(filters.TEXT & ~filters.COMMAND, treadmill_speed)],
            TREADMILL_DURATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, treadmill_duration)],
            TREADMILL_AGE:      [MessageHandler(filters.TEXT & ~filters.COMMAND, treadmill_age)],
            TREADMILL_WEIGHT:   [MessageHandler(filters.TEXT & ~filters.COMMAND, treadmill_weight)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start",      start))
    app.add_handler(CommandHandler("help",       help_cmd))
    app.add_handler(CommandHandler("summary",    summary))
    app.add_handler(CommandHandler("deficit",    deficit))
    app.add_handler(CommandHandler("history",    history))
    app.add_handler(CommandHandler("goal",       set_goal))
    app.add_handler(CommandHandler("clear",      clear_day))
    app.add_handler(conv)
    app.add_handler(workout_conv)
    app.add_handler(treadmill_conv)
    app.add_handler(CallbackQueryHandler(treadmill_add_callback, pattern=r"^tm_add:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    logger.info("Бот слушает обновления...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()