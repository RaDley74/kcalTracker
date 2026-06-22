import os
import sys
import sqlite3
import logging
import textwrap
import base64
import json
import time
import urllib.request
import urllib.error
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
            f.write("\n# Ключ OpenRouter для анализа фото еды (бесплатно)\n")
            f.write("# Получи на https://openrouter.ai/keys\n")
            f.write("OPENROUTER_API_KEY=\n")
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

    # ── Консоль (DEBUG+) — полный лог всех действий ─────────────────────────
    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.DEBUG)
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


# ── Logging helpers ───────────────────────────────────────────────────────────

def _log_user_msg(update, extra: str = "") -> None:
    """Логирует входящее сообщение пользователя."""
    u    = update.effective_user
    text = ""
    if update.message:
        if update.message.text:
            text = f"«{update.message.text[:80]}»"
        elif update.message.photo:
            text = "[фото]"
        elif update.message.document:
            text = f"[файл: {update.message.document.file_name}]"
        else:
            text = "[медиа]"
    name = u.full_name or u.username or str(u.id)
    suffix = f"  {extra}" if extra else ""
    logger.debug("→ USER  id=%-12s %-18s %s%s", u.id, name, text, suffix)


def _log_bot_reply(uid, text: str, extra: str = "") -> None:
    """Логирует исходящий ответ бота (первые 120 символов)."""
    preview = text.replace("\n", " ").strip()[:120]
    suffix  = f"  [{extra}]" if extra else ""
    logger.debug("← BOT   id=%-12s «%s»%s", uid, preview, suffix)


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
    """Последние уникальные продукты пользователя (уникальные по имени и калорийности)."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT name, kcal
               FROM food_log
               WHERE user_id = ?
               GROUP BY name, kcal
               ORDER BY MAX(logged_at) DESC
               LIMIT ?""",
            (user_id, limit),
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


def db_get_day_with_ids(user_id: int, day: str) -> list[sqlite3.Row]:
    """Еда за день с id записей."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, kcal, logged_at FROM food_log "
            "WHERE user_id=? AND log_date=? ORDER BY logged_at",
            (user_id, day),
        ).fetchall()


def db_get_workouts_day_with_ids(user_id: int, day: str) -> list[sqlite3.Row]:
    """Тренировки за день с id записей."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT id, name, kcal, logged_at FROM workout_log "
            "WHERE user_id=? AND log_date=? ORDER BY logged_at",
            (user_id, day),
        ).fetchall()


def db_delete_food(user_id: int, entry_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM food_log WHERE id=? AND user_id=?", (entry_id, user_id)
        )
    deleted = cur.rowcount > 0
    if deleted:
        logger.info("Запись еды удалена: user_id=%d  id=%d", user_id, entry_id)
    return deleted


def db_delete_workout(user_id: int, entry_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM workout_log WHERE id=? AND user_id=?", (entry_id, user_id)
        )
    deleted = cur.rowcount > 0
    if deleted:
        logger.info("Запись тренировки удалена: user_id=%d  id=%d", user_id, entry_id)
    return deleted


def db_add_entry_for_date(user_id: int, name: str, kcal: int, day: str) -> None:
    """Добавить запись еды за произвольную дату."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO food_log(user_id, name, kcal, logged_at, log_date) VALUES(?,?,?,?,?)",
            (user_id, name, kcal, now, day),
        )
    logger.info("Запись добавлена (дата=%s): user_id=%d  «%s» %d ккал", day, user_id, name, kcal)


def db_add_workout_for_date(user_id: int, name: str, kcal: int, day: str) -> None:
    """Добавить тренировку за произвольную дату."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO workout_log(user_id, name, kcal, logged_at, log_date) VALUES(?,?,?,?,?)",
            (user_id, name, kcal, now, day),
        )
    logger.info("Тренировка добавлена (дата=%s): user_id=%d  «%s» %d ккал", day, user_id, name, kcal)


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
    PHOTO_CONFIRM_KCAL, PHOTO_CAPTION,
    EDIT_SELECT_DATE, EDIT_SELECT_ACTION, EDIT_SELECT_ENTRY,
    EDIT_ADD_FOOD_NAME, EDIT_ADD_FOOD_KCAL,
    EDIT_ADD_WORKOUT_NAME, EDIT_ADD_WORKOUT_KCAL,
) = range(19)

# ── Keyboards ─────────────────────────────────────────────────────────────────

MAIN_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton("➕ Добавить еду"),      KeyboardButton("🏋️ Добавить тренировку")],
        [KeyboardButton("📷 Фото еды → калории"), KeyboardButton("🏃 Беговая дорожка")],
        [KeyboardButton("📊 Сводка за день"),     KeyboardButton("📉 Дефицит калорий")],
        [KeyboardButton("📅 История"),            KeyboardButton("🎯 Установить цель")],
        [KeyboardButton("✏️ Редактировать день"), KeyboardButton("🗑 Очистить день")]
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
    _log_user_msg(update, extra="/start")
    logger.info("Команда /start: user_id=%d (%s)", u.id, u.full_name)
    _msg = (
        f"Привет, *{u.first_name}*! 👋\n\n"
        "Я помогу отслеживать калории и считать дефицит.\n\n"
        "📌 *Команды:*\n/add, /photo, /workout, /treadmill, /summary, /deficit, /history, /goal, /clear"
    )
    _log_bot_reply(u.id, _msg, extra="start-welcome")
    await update.message.reply_text(
        f"Привет, *{u.first_name}*! 👋\n\n"
        "Я помогу отслеживать калории и считать дефицит.\n\n"
        "📌 *Команды:*\n"
        "/add — добавить приём пищи\n"
        "/photo — 📷 сфоткай еду, я сам посчитаю калории\n"
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
    _log_user_msg(update, extra="/help")
    logger.debug("Команда /help: user_id=%d", update.effective_user.id)
    _log_bot_reply(update.effective_user.id, "📖 Справка по командам бота", extra="help")
    await update.message.reply_text(
        "📖 *Как пользоваться ботом:*\n\n"
        "1\\. Нажми *➕ Добавить еду* или /add\n"
        "2\\. Введи название продукта\n"
        "3\\. Введи количество калорий\n\n"
        "*Быстрый ввод еды:* пиши сразу `Овсянка 350`\n\n"
        "*Ввод списком:* пришли несколько блюд, каждое с новой строки:\n"
        "`Завтрак 289`\n"
        "`Ролл лаваш 420`\n"
        "`Куриная грудка 180г 200`\n\n"
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
    recent = db_recent_food(uid, limit=16)
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
    _log_user_msg(update, extra="➕ добавить еду — старт")
    logger.debug("Начало добавления еды: user_id=%d", uid)

    recent = db_recent_food(uid, limit=16)
    if recent:
        keyboard = _build_food_keyboard(uid)
        hint = (
            "Введи название продукта или блюда:\n"
            "_(или сразу «Название Калории», например «Гречка 280»)_\n"
            "_(или списком — каждое блюдо на новой строке)_\n\n"
            "\u2b07\ufe0f Или выбери из недавних:"
        )
    else:
        keyboard = CANCEL_KEYBOARD
        hint = (
            "Введи название продукта или блюда:\n"
            "_(или сразу «Название Калории», например «Гречка 280»)_\n"
            "_(или списком — каждое блюдо на новой строке)_"
        )

    await update.message.reply_text(hint, parse_mode="Markdown", reply_markup=keyboard)
    return WAITING_FOOD


def _parse_food_line(line: str) -> tuple[str, int] | None:
    """Разбирает строку вида «Гречка 50г 170» -> ('Гречка 50г', 170). None, если не похоже."""
    line = line.strip()
    if not line:
        return None
    parts = line.rsplit(" ", 1)
    if len(parts) != 2:
        return None
    name = parts[0].strip()
    if not name:
        return None
    try:
        kcal = int(parts[1])
    except ValueError:
        return None
    if kcal <= 0:
        return None
    return name, kcal


async def received_food(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid  = update.effective_user.id

    if text == "❌ Отмена":
        logger.debug("Отмена добавления еды: user_id=%d", uid)
        _log_user_msg(update, extra="отмена добавления еды")
        _log_bot_reply(uid, "Отменено.", extra="cancel")
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    # Список из нескольких строк: каждая строка — отдельный продукт.
    raw_lines = [l for l in text.splitlines() if l.strip()]
    if len(raw_lines) > 1:
        parsed  = [_parse_food_line(l) for l in raw_lines]
        bad_idx = [i for i, p in enumerate(parsed) if p is None]
        if bad_idx:
            bad_lines = "\n".join(f"• {raw_lines[i]}" for i in bad_idx)
            logger.warning("Список еды: некорректные строки от user_id=%d:\n%s", uid, bad_lines)
            _log_user_msg(update, extra=f"список еды с ошибками: {bad_lines!r}")
            await update.message.reply_text(
                "⚠️ Не смог распознать калории в этих строках:\n"
                f"{bad_lines}\n\n"
                "Каждая строка должна заканчиваться числом калорий, например:\n"
                "`Гречка 50г 170`\n\n"
                "Исправь и отправь список заново, или введи одно блюдо.",
                parse_mode="Markdown",
                reply_markup=CANCEL_KEYBOARD,
            )
            return WAITING_FOOD

        entries = [p for p in parsed if p is not None]
        logger.debug("Список еды: user_id=%d  %d позиций", uid, len(entries))
        _log_user_msg(update, extra=f"список еды: {len(entries)} позиций")
        return await _save_entries(update, context, entries)

    # Кнопка из истории: "📌 Название • 350 ккал"
    import re
    history_match = re.match(r"^📌 (.+) • (\d+) ккал$", text)
    if history_match:
        food_name = history_match.group(1)
        kcal      = int(history_match.group(2))
        context.user_data["food_name"] = food_name
        context.user_data["food_kcal"] = kcal
        logger.debug("Быстрый выбор из истории: user_id=%d «%s» %d ккал", uid, food_name, kcal)
        _log_user_msg(update, extra=f"выбор из истории: {food_name} {kcal} ккал")
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
            _log_user_msg(update, extra=f"быстрый ввод: {parts[0]} {kcal} ккал")
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
        _log_user_msg(update, extra=f"некорректные калории: {text!r}")
        _log_bot_reply(uid, "⚠️ Введи целое положительное число:", extra="validation-error")
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
    _log_bot_reply(uid, f"✅ {name} — {kcal} ккал добавлено | итого={total} сожжено={burned} net={net}/{goal}", extra="food-saved")

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


async def _save_entries(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    entries: list[tuple[str, int]],
) -> int:
    """Сохраняет сразу несколько позиций еды и показывает общую сводку."""
    uid = update.effective_user.id

    for name, kcal in entries:
        db_add_entry(uid, name, kcal)

    day_entries = db_get_day(uid, _today())
    total       = sum(r["kcal"] for r in day_entries)
    burned      = db_workout_kcal_day(uid, _today())
    net         = total - burned
    goal        = db_get_goal(uid)
    remaining   = goal - net
    bar         = _progress_bar(net, goal)

    added_kcal = sum(kcal for _, kcal in entries)
    logger.info(
        "Список еды сохранён: user_id=%d  позиций=%d  добавлено=%d ккал  total=%d  net=%d/%d",
        uid, len(entries), added_kcal, total, net, goal,
    )
    _log_bot_reply(
        uid,
        f"✅ список из {len(entries)} позиций добавлен ({added_kcal} ккал) | итого={total} net={net}/{goal}",
        extra="food-list-saved",
    )

    lines = "\n".join(f"{_kcal_badge(kcal)} {name} — {kcal} ккал" for name, kcal in entries)
    msg = (
        f"✅ Добавлено {len(entries)} позиций ({added_kcal} ккал):\n\n"
        f"{lines}\n\n"
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
    _log_user_msg(update, extra="🏋️ тренировка — старт")
    logger.debug("Начало добавления тренировки: user_id=%d", update.effective_user.id)
    _log_bot_reply(update.effective_user.id, "Введи название тренировки:", extra="workout-prompt")
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
        _log_user_msg(update, extra="отмена тренировки")
        _log_bot_reply(uid, "Отменено.", extra="cancel")
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
            logger.debug("Быстрый ввод тренировки: user_id=%d «%s» %d ккал", uid, parts[0], kcal)
            _log_user_msg(update, extra=f"быстрый ввод тренировки: {parts[0]} {kcal} ккал")
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
        _log_user_msg(update, extra="отмена ввода ккал тренировки")
        _log_bot_reply(uid, "Отменено.", extra="cancel")
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    try:
        kcal = int(text)
        if kcal <= 0:
            raise ValueError
    except ValueError:
        _log_user_msg(update, extra=f"некорректные ккал тренировки: {text!r}")
        _log_bot_reply(uid, "⚠️ Введи целое положительное число:", extra="validation-error")
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
    _log_bot_reply(uid, f"🏋️ {name} — сожжено {kcal} ккал | net={net}/{goal}", extra="workout-saved")

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
    _log_user_msg(update, extra="🏃 беговая дорожка — старт")
    logger.debug("Калькулятор дорожки: user_id=%d", update.effective_user.id)
    _log_bot_reply(update.effective_user.id, "Введи среднюю ЧСС:", extra="treadmill-hr-prompt")
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
    uid = update.effective_user.id
    if await _treadmill_cancel_check(update):
        _log_user_msg(update, extra="отмена на шаге ЧСС")
        _log_bot_reply(uid, "Отменено.", extra="cancel")
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 40, 220)
    if val is None:
        _log_user_msg(update, extra=f"некорректная ЧСС: {update.message.text!r}")
        _log_bot_reply(uid, "⚠️ Введи число от 40 до 220:", extra="validation-error")
        await update.message.reply_text("⚠️ Введи число от 40 до 220:")
        return TREADMILL_HR
    context.user_data["tm_hr"] = val
    logger.debug("Дорожка ЧСС: user_id=%d  hr=%.0f", uid, val)
    _log_user_msg(update, extra=f"ЧСС={val}")
    _log_bot_reply(uid, "Уклон дорожки (%, 0–30):", extra="treadmill-incline-prompt")
    await update.message.reply_text("Уклон дорожки (%, 0–30):", reply_markup=CANCEL_KEYBOARD)
    return TREADMILL_INCLINE


async def treadmill_incline(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if await _treadmill_cancel_check(update):
        _log_user_msg(update, extra="отмена на шаге уклон")
        _log_bot_reply(uid, "Отменено.", extra="cancel")
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 0, 30)
    if val is None:
        _log_user_msg(update, extra=f"некорректный уклон: {update.message.text!r}")
        _log_bot_reply(uid, "⚠️ Введи число от 0 до 30:", extra="validation-error")
        await update.message.reply_text("⚠️ Введи число от 0 до 30:")
        return TREADMILL_INCLINE
    context.user_data["tm_incline"] = val
    logger.debug("Дорожка уклон: user_id=%d  incline=%.1f%%", uid, val)
    _log_user_msg(update, extra=f"уклон={val}%")
    _log_bot_reply(uid, "Средняя скорость (км/ч, 1–30):", extra="treadmill-speed-prompt")
    await update.message.reply_text("Средняя скорость (км/ч, 1–30):", reply_markup=CANCEL_KEYBOARD)
    return TREADMILL_SPEED


async def treadmill_speed(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if await _treadmill_cancel_check(update):
        _log_user_msg(update, extra="отмена на шаге скорость")
        _log_bot_reply(uid, "Отменено.", extra="cancel")
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 1, 30)
    if val is None:
        _log_user_msg(update, extra=f"некорректная скорость: {update.message.text!r}")
        _log_bot_reply(uid, "⚠️ Введи число от 1 до 30:", extra="validation-error")
        await update.message.reply_text("⚠️ Введи число от 1 до 30:")
        return TREADMILL_SPEED
    context.user_data["tm_speed"] = val
    logger.debug("Дорожка скорость: user_id=%d  speed=%.1f км/ч", uid, val)
    _log_user_msg(update, extra=f"скорость={val} км/ч")
    _log_bot_reply(uid, "Время тренировки (мин, 1–300):", extra="treadmill-duration-prompt")
    await update.message.reply_text("Время тренировки (мин, 1–300):", reply_markup=CANCEL_KEYBOARD)
    return TREADMILL_DURATION


async def treadmill_duration(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if await _treadmill_cancel_check(update):
        _log_user_msg(update, extra="отмена на шаге длительность")
        _log_bot_reply(uid, "Отменено.", extra="cancel")
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 1, 300)
    if val is None:
        _log_user_msg(update, extra=f"некорректная длительность: {update.message.text!r}")
        _log_bot_reply(uid, "⚠️ Введи число от 1 до 300:", extra="validation-error")
        await update.message.reply_text("⚠️ Введи число от 1 до 300:")
        return TREADMILL_DURATION
    context.user_data["tm_duration"] = val
    logger.debug("Дорожка длительность: user_id=%d  duration=%.0f мин", uid, val)
    _log_user_msg(update, extra=f"длительность={val} мин")
    _log_bot_reply(uid, "Твой возраст (лет, 10–100):", extra="treadmill-age-prompt")
    await update.message.reply_text("Твой возраст (лет, 10–100):", reply_markup=CANCEL_KEYBOARD)
    return TREADMILL_AGE


async def treadmill_age(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if await _treadmill_cancel_check(update):
        _log_user_msg(update, extra="отмена на шаге возраст")
        _log_bot_reply(uid, "Отменено.", extra="cancel")
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 10, 100)
    if val is None:
        _log_user_msg(update, extra=f"некорректный возраст: {update.message.text!r}")
        _log_bot_reply(uid, "⚠️ Введи число от 10 до 100:", extra="validation-error")
        await update.message.reply_text("⚠️ Введи число от 10 до 100:")
        return TREADMILL_AGE
    context.user_data["tm_age"] = int(val)
    logger.debug("Дорожка возраст: user_id=%d  age=%d", uid, int(val))
    _log_user_msg(update, extra=f"возраст={int(val)}")
    _log_bot_reply(uid, "Масса тела (кг, 30–250):", extra="treadmill-weight-prompt")
    await update.message.reply_text("Масса тела (кг, 30–250):", reply_markup=CANCEL_KEYBOARD)
    return TREADMILL_WEIGHT


async def treadmill_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    if await _treadmill_cancel_check(update):
        _log_user_msg(update, extra="отмена на шаге вес")
        _log_bot_reply(uid, "Отменено.", extra="cancel")
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END
    val = _parse_positive_float(update.message.text, 30, 250)
    if val is None:
        _log_user_msg(update, extra=f"некорректный вес: {update.message.text!r}")
        _log_bot_reply(uid, "⚠️ Введи число от 30 до 250:", extra="validation-error")
        await update.message.reply_text("⚠️ Введи число от 30 до 250:")
        return TREADMILL_WEIGHT
    context.user_data["tm_weight"] = val
    logger.debug("Дорожка вес: user_id=%d  weight=%.1f кг", uid, val)
    _log_user_msg(update, extra=f"вес={val} кг")

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
    _log_bot_reply(uid, f"🏃 Итого сожжено: {res['total_kcal']} ккал | дистанция={res['distance_km']} км", extra="treadmill-result")

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
    _log_bot_reply(uid, f"✅ {name} — {kcal} ккал добавлено в тренировки! net={net}/{goal}", extra="treadmill-workout-saved")

    await query.edit_message_text(
        f"✅ *{name}* — {kcal} ккал добавлено в тренировки!\n\n"
        f"{bar}\n"
        f"Чистые калории: *{net}* / {goal} ккал\n"
        + (f"📉 Ещё можно: *{remaining}* ккал" if remaining > 0
           else f"⚠️ Превышение нормы на *{abs(remaining)}* ккал"),
        parse_mode="Markdown",
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    uid = update.effective_user.id
    _log_user_msg(update, extra="/cancel")
    logger.debug("ConversationHandler отменён: user_id=%d", uid)
    _log_bot_reply(uid, "Отменено.", extra="cancel")
    await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
    return ConversationHandler.END


# ── Summary ───────────────────────────────────────────────────────────────────

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    uid     = update.effective_user.id
    today   = _today()
    entries = db_get_day(uid, today)
    goal    = db_get_goal(uid)

    _log_user_msg(update, extra="📊 сводка за день")
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

    _log_user_msg(update, extra="📉 дефицит калорий")
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

    _log_user_msg(update, extra="📅 история")
    logger.info("История запрошена: user_id=%d  дней=%d", uid, len(rows))

    if not rows:
        await update.message.reply_text(
            "История пуста — начни добавлять еду!",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    lines = [f"📅 *История за всё время ({len(rows)} дн.)*\n"]
    for r in rows:
        workout_kcal = db_workout_kcal_day(uid, r["log_date"])
        net_kcal     = r["total_kcal"] - workout_kcal
        deficit_val  = goal - net_kcal
        if deficit_val > 0:
            status   = f"✅ −{deficit_val}"
            bar_mini = "🟩"
        else:
            status   = f"⚠️ +{abs(deficit_val)}"
            bar_mini = "🟥"

        lines.append(
            f"{bar_mini} *{r['log_date']}*: {net_kcal} ккал  {status} ккал"
        )

        # Еда
        food = db_get_day(uid, r["log_date"])
        for entry in food:
            t = entry["logged_at"][11:16]  # HH:MM
            badge = _kcal_badge(entry["kcal"])
            lines.append(f"  {badge} `{t}` {entry['name']} — {entry['kcal']} ккал")

        # Тренировки
        workouts = db_get_workouts_day(uid, r["log_date"])
        for w in workouts:
            t = w["logged_at"][11:16]
            lines.append(f"  🔥 `{t}` {w['name']} — −{w['kcal']} ккал")

        lines.append("")  # пустая строка между днями

    parts = _split_message(lines)
    for i, part in enumerate(parts):
        await update.message.reply_text(
            "\n".join(part),
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD if i == len(parts) - 1 else None,
        )


# ── Goal ──────────────────────────────────────────────────────────────────────

async def set_goal_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    uid  = update.effective_user.id
    goal = db_get_goal(uid)
    _log_user_msg(update, extra=f"🎯 установить цель (текущая={goal})")
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
        _log_user_msg(update, extra=f"некорректная цель: {context.args}")
        _log_bot_reply(uid, "⚠️ Введи число: /goal 2000", extra="validation-error")
        await update.message.reply_text("⚠️ Введи число: `/goal 2000`", parse_mode="Markdown")
        return

    db_set_goal(uid, new_goal)
    _log_user_msg(update, extra=f"/goal {new_goal}")
    _log_bot_reply(uid, f"🎯 Цель обновлена: {new_goal} ккал/день", extra="goal-updated")
    await update.message.reply_text(
        f"🎯 Цель обновлена: *{new_goal}* ккал/день",
        parse_mode="Markdown",
        reply_markup=MAIN_KEYBOARD,
    )


# ── Clear ─────────────────────────────────────────────────────────────────────

async def clear_day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _sync_user(update)
    uid     = update.effective_user.id
    _log_user_msg(update, extra="🗑 очистить день")
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


# ── Edit past days ────────────────────────────────────────────────────────────

def _edit_day_summary(uid: int, day: str) -> str:
    """Текстовая сводка дня для экрана редактирования."""
    food = db_get_day_with_ids(uid, day)
    workouts = db_get_workouts_day_with_ids(uid, day)
    lines = [f"✏️ *Редактирование: {day}*\n"]
    if food:
        lines.append("🍽 *Еда:*")
        for i, r in enumerate(food, 1):
            badge = _kcal_badge(r["kcal"])
            lines.append(f"  {i}. {badge} {r['name']} — {r['kcal']} ккал")
    else:
        lines.append("🍽 *Еда:* пусто")
    if workouts:
        lines.append("🏋️ *Тренировки:*")
        for i, r in enumerate(workouts, 1):
            lines.append(f"  {i}. 🔥 {r['name']} — {r['kcal']} ккал")
    else:
        lines.append("🏋️ *Тренировки:* пусто")
    total_food = sum(r["kcal"] for r in food)
    total_burn = sum(r["kcal"] for r in workouts)
    lines.append(f"\n🍽 Итого еды: *{total_food}* ккал  |  🔥 Сожжено: *{total_burn}* ккал")
    return "\n".join(lines)


def _edit_action_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton("📝 Добавить еду (в этот день)"),       KeyboardButton("📝 Добавить тренировку (в этот день)")],
            [KeyboardButton("🗑 Удалить запись еды"),  KeyboardButton("🗑 Удалить тренировку")],
            [KeyboardButton("❌ Отмена")],
        ],
        resize_keyboard=True,
    )


async def edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    _sync_user(update)
    uid  = update.effective_user.id
    _log_user_msg(update, extra="✏️ редактировать день — старт")

    # Показываем последние 14 календарных дней (включая дни без записей)
    today = date.today()
    recent_dates = [(today - timedelta(days=i)).isoformat() for i in range(14)]
    date_buttons = []
    for i in range(0, len(recent_dates), 2):
        pair = [KeyboardButton(d) for d in recent_dates[i:i+2]]
        date_buttons.append(pair)
    date_buttons.append([KeyboardButton("❌ Отмена")])

    await update.message.reply_text(
        "📅 Выбери дату для редактирования:",
        reply_markup=ReplyKeyboardMarkup(date_buttons, resize_keyboard=True),
    )
    return EDIT_SELECT_DATE


async def edit_select_date(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid  = update.effective_user.id

    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    import re
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        await update.message.reply_text("⚠️ Выбери дату из списка.")
        return EDIT_SELECT_DATE

    context.user_data["edit_date"] = text
    logger.debug("Редактирование даты: user_id=%d  date=%s", uid, text)

    summary_text = _edit_day_summary(uid, text)
    await update.message.reply_text(
        summary_text + "\n\nЧто хочешь сделать?",
        parse_mode="Markdown",
        reply_markup=_edit_action_keyboard(),
    )
    return EDIT_SELECT_ACTION


async def edit_select_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid  = update.effective_user.id
    day  = context.user_data.get("edit_date", _today())

    if text == "❌ Отмена":
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    if text == "📝 Добавить еду (в этот день)":
        context.user_data["edit_adding"] = "food"
        await update.message.reply_text(
            f"Введи название еды для *{day}*:\n"
            "_(или сразу «Название Калории», например «Гречка 280»)_",
            parse_mode="Markdown",
            reply_markup=CANCEL_KEYBOARD,
        )
        return EDIT_ADD_FOOD_NAME

    if text == "📝 Добавить тренировку (в этот день)":
        context.user_data["edit_adding"] = "workout"
        await update.message.reply_text(
            f"Введи название тренировки для *{day}*:\n"
            "_(или сразу «Название Калории», например «Бег 300»)_",
            parse_mode="Markdown",
            reply_markup=CANCEL_KEYBOARD,
        )
        return EDIT_ADD_WORKOUT_NAME

    if text == "🗑 Удалить запись еды":
        food = db_get_day_with_ids(uid, day)
        if not food:
            await update.message.reply_text(
                "Нет записей еды за этот день.",
                reply_markup=_edit_action_keyboard(),
            )
            return EDIT_SELECT_ACTION
        context.user_data["edit_delete_type"] = "food"
        buttons = [
            [KeyboardButton(f"🍽 {r['name']} — {r['kcal']} ккал [id:{r['id']}]")]
            for r in food
        ]
        buttons.append([KeyboardButton("❌ Отмена")])
        await update.message.reply_text(
            "Выбери запись для удаления:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
        )
        return EDIT_SELECT_ENTRY

    if text == "🗑 Удалить тренировку":
        workouts = db_get_workouts_day_with_ids(uid, day)
        if not workouts:
            await update.message.reply_text(
                "Нет тренировок за этот день.",
                reply_markup=_edit_action_keyboard(),
            )
            return EDIT_SELECT_ACTION
        context.user_data["edit_delete_type"] = "workout"
        buttons = [
            [KeyboardButton(f"🏋️ {r['name']} — {r['kcal']} ккал [id:{r['id']}]")]
            for r in workouts
        ]
        buttons.append([KeyboardButton("❌ Отмена")])
        await update.message.reply_text(
            "Выбери тренировку для удаления:",
            reply_markup=ReplyKeyboardMarkup(buttons, resize_keyboard=True),
        )
        return EDIT_SELECT_ENTRY

    await update.message.reply_text("Используй кнопки.", reply_markup=_edit_action_keyboard())
    return EDIT_SELECT_ACTION


async def edit_select_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Обрабатывает выбор записи для удаления."""
    import re
    text = update.message.text.strip()
    uid  = update.effective_user.id
    day  = context.user_data.get("edit_date", _today())
    dtype = context.user_data.get("edit_delete_type", "food")

    if text == "❌ Отмена":
        summary_text = _edit_day_summary(uid, day)
        await update.message.reply_text(
            summary_text + "\n\nЧто хочешь сделать?",
            parse_mode="Markdown",
            reply_markup=_edit_action_keyboard(),
        )
        return EDIT_SELECT_ACTION

    m = re.search(r"\[id:(\d+)\]", text)
    if not m:
        await update.message.reply_text("⚠️ Выбери запись из списка.")
        return EDIT_SELECT_ENTRY

    entry_id = int(m.group(1))
    if dtype == "food":
        deleted = db_delete_food(uid, entry_id)
        what = "Запись еды"
    else:
        deleted = db_delete_workout(uid, entry_id)
        what = "Тренировка"

    if deleted:
        logger.info("Удалена запись id=%d тип=%s user_id=%d дата=%s", entry_id, dtype, uid, day)
        summary_text = _edit_day_summary(uid, day)
        await update.message.reply_text(
            f"✅ {what} удалена.\n\n" + summary_text + "\n\nЧто ещё хочешь сделать?",
            parse_mode="Markdown",
            reply_markup=_edit_action_keyboard(),
        )
    else:
        await update.message.reply_text("⚠️ Не удалось удалить запись.", reply_markup=_edit_action_keyboard())

    return EDIT_SELECT_ACTION


async def edit_add_food_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid  = update.effective_user.id
    day  = context.user_data.get("edit_date", _today())

    if text == "❌ Отмена":
        summary_text = _edit_day_summary(uid, day)
        await update.message.reply_text(
            summary_text + "\n\nЧто хочешь сделать?",
            parse_mode="Markdown",
            reply_markup=_edit_action_keyboard(),
        )
        return EDIT_SELECT_ACTION

    # Быстрый ввод "Название 300"
    parts = text.rsplit(" ", 1)
    if len(parts) == 2:
        try:
            kcal = int(parts[1])
            if kcal > 0:
                db_add_entry_for_date(uid, parts[0], kcal, day)
                summary_text = _edit_day_summary(uid, day)
                await update.message.reply_text(
                    f"✅ Добавлено: *{parts[0]}* — {kcal} ккал\n\n" + summary_text + "\n\nЧто ещё хочешь сделать?",
                    parse_mode="Markdown",
                    reply_markup=_edit_action_keyboard(),
                )
                return EDIT_SELECT_ACTION
        except ValueError:
            pass

    context.user_data["edit_new_name"] = text
    await update.message.reply_text(
        f"Сколько калорий в *{text}*?",
        parse_mode="Markdown",
        reply_markup=CANCEL_KEYBOARD,
    )
    return EDIT_ADD_FOOD_KCAL


async def edit_add_food_kcal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid  = update.effective_user.id
    day  = context.user_data.get("edit_date", _today())

    if text == "❌ Отмена":
        summary_text = _edit_day_summary(uid, day)
        await update.message.reply_text(
            summary_text + "\n\nЧто хочешь сделать?",
            parse_mode="Markdown",
            reply_markup=_edit_action_keyboard(),
        )
        return EDIT_SELECT_ACTION

    try:
        kcal = int(text)
        if kcal <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Введи целое положительное число:")
        return EDIT_ADD_FOOD_KCAL

    name = context.user_data.get("edit_new_name", "Блюдо")
    db_add_entry_for_date(uid, name, kcal, day)
    summary_text = _edit_day_summary(uid, day)
    await update.message.reply_text(
        f"✅ Добавлено: *{name}* — {kcal} ккал\n\n" + summary_text + "\n\nЧто ещё хочешь сделать?",
        parse_mode="Markdown",
        reply_markup=_edit_action_keyboard(),
    )
    return EDIT_SELECT_ACTION


async def edit_add_workout_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid  = update.effective_user.id
    day  = context.user_data.get("edit_date", _today())

    if text == "❌ Отмена":
        summary_text = _edit_day_summary(uid, day)
        await update.message.reply_text(
            summary_text + "\n\nЧто хочешь сделать?",
            parse_mode="Markdown",
            reply_markup=_edit_action_keyboard(),
        )
        return EDIT_SELECT_ACTION

    parts = text.rsplit(" ", 1)
    if len(parts) == 2:
        try:
            kcal = int(parts[1])
            if kcal > 0:
                db_add_workout_for_date(uid, parts[0], kcal, day)
                summary_text = _edit_day_summary(uid, day)
                await update.message.reply_text(
                    f"✅ Добавлена тренировка: *{parts[0]}* — {kcal} ккал\n\n" + summary_text + "\n\nЧто ещё хочешь сделать?",
                    parse_mode="Markdown",
                    reply_markup=_edit_action_keyboard(),
                )
                return EDIT_SELECT_ACTION
        except ValueError:
            pass

    context.user_data["edit_new_name"] = text
    await update.message.reply_text(
        f"Сколько калорий сожжено в *{text}*?",
        parse_mode="Markdown",
        reply_markup=CANCEL_KEYBOARD,
    )
    return EDIT_ADD_WORKOUT_KCAL


async def edit_add_workout_kcal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    uid  = update.effective_user.id
    day  = context.user_data.get("edit_date", _today())

    if text == "❌ Отмена":
        summary_text = _edit_day_summary(uid, day)
        await update.message.reply_text(
            summary_text + "\n\nЧто хочешь сделать?",
            parse_mode="Markdown",
            reply_markup=_edit_action_keyboard(),
        )
        return EDIT_SELECT_ACTION

    try:
        kcal = int(text)
        if kcal <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⚠️ Введи целое положительное число:")
        return EDIT_ADD_WORKOUT_KCAL

    name = context.user_data.get("edit_new_name", "Тренировка")
    db_add_workout_for_date(uid, name, kcal, day)
    summary_text = _edit_day_summary(uid, day)
    await update.message.reply_text(
        f"✅ Добавлена тренировка: *{name}* — {kcal} ккал\n\n" + summary_text + "\n\nЧто ещё хочешь сделать?",
        parse_mode="Markdown",
        reply_markup=_edit_action_keyboard(),
    )
    return EDIT_SELECT_ACTION


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
        "➕ Добавить еду":           add_start,
        "🏋️ Добавить тренировку":   workout_start,
        "📷 Фото еды → калории":    photo_start,
        "🏃 Беговая дорожка":       treadmill_start,
        "📊 Сводка за день":        summary,
        "📉 Дефицит калорий":       deficit,
        "📅 История":               history,
        "🎯 Установить цель":       set_goal_start,
        "✏️ Редактировать день":    edit_start,
        "🗑 Очистить день":         clear_day,
        "❓ Помощь":                help_cmd,
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

# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║                     Photo → Calories  (OpenRouter / Gemini Flash free)      ║
# ╚══════════════════════════════════════════════════════════════════════════════╝

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Список моделей по приоритету качества — все бесплатные, с поддержкой Vision.
# При 429/недоступности одной — автоматически переходим к следующей.
OPENROUTER_MODELS = [                      # Vision, альтернативный провайдер
    "nvidia/nemotron-nano-12b-v2-vl:free",            # запасной — проверенный рабочий
]

PHOTO_SYSTEM = (
    "Ты диетолог-нутрициолог. Пользователь присылает фото еды.\n\n"
    "ПРАВИЛА (соблюдай строго):\n"
    "1. Если пользователь указал состав блюда — используй ТОЛЬКО его данные как источник правды о составе. "
    "Фото служит лишь для оценки размера порции, но НЕ для определения состава. "
    "Никогда не заменяй и не переименовывай продукты, названные пользователем, на то, что ты видишь на фото.\n"
    "2. Если состав не указан — определи блюдо по фото самостоятельно.\n"
    "3. Рассчитай калории максимально точно исходя из граммовки. "
    "Если вес не указан — оцени по фото.\n"
    "4. Отвечай ТОЛЬКО валидным JSON-объектом (не массивом) без markdown-обёрток:\n"
    '{"name": "<название на русском строго по составу пользователя>", '
    '"kcal": <целое число — сумма калорий всех компонентов>, '
    '"note": "<расчёт: продукт (вес, ккал) + продукт (вес, ккал) = итого>"}'
)


def _call_openrouter_model(
    model: str,
    b64: str,
    mime: str,
    user_text: str,
    api_key: str,
    retries: int = 2,
    retry_delay: float = 3.0,
) -> dict | None:
    """Один запрос к конкретной модели с retry при сетевых ошибках и обрезанном JSON.
    Возвращает распарсенный dict или None. При 429 возвращает None сразу (без retry).
    """
    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": PHOTO_SYSTEM},
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                    {"type": "text", "text": user_text},
                ],
            },
        ],
        "max_tokens": 600,
    }).encode()

    req = urllib.request.Request(
        OPENROUTER_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/calorie-bot",
            "X-Title": "Calorie Telegram Bot",
        },
        method="POST",
    )

    for attempt in range(1, retries + 1):
        data = None
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            if e.code == 429:
                # Rate limit — эту модель пропускаем, смысла повторять нет
                logger.warning("OpenRouter 429 [%s] — пропускаем модель: %s", model, body[:120])
                return None
            logger.warning("OpenRouter HTTP %d [%s] (попытка %d/%d): %s",
                           e.code, model, attempt, retries, body[:200])
            if attempt < retries:
                time.sleep(retry_delay)
            continue
        except Exception as exc:
            logger.warning("OpenRouter сеть [%s] (попытка %d/%d): %s",
                           model, attempt, retries, exc)
            if attempt < retries:
                time.sleep(retry_delay)
            continue

        try:
            content = data["choices"][0]["message"]["content"].strip()
            finish_reason = data["choices"][0].get("finish_reason", "")
            if finish_reason == "length":
                logger.warning("OpenRouter обрезанный ответ (finish_reason=length) [%s] попытка %d/%d",
                               model, attempt, retries)
                if attempt < retries:
                    time.sleep(retry_delay)
                continue
            content = content.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed  = json.loads(content)

            if isinstance(parsed, list):
                if not parsed:
                    raise ValueError("Пустой список блюд")
                if len(parsed) == 1:
                    result = parsed[0]
                else:
                    total_kcal = sum(int(item.get("kcal", 0)) for item in parsed)
                    names      = ", ".join(item.get("name", "?") for item in parsed)
                    notes      = "; ".join(item.get("note", "") for item in parsed if item.get("note"))
                    result     = {"name": names, "kcal": total_kcal, "note": notes}
            else:
                result = parsed

            result["kcal"] = int(result["kcal"])
            return result

        except Exception as exc:
            logger.warning("Не удалось разобрать ответ [%s] попытка %d/%d: %s",
                           model, attempt, retries, exc)
            if attempt < retries:
                time.sleep(retry_delay)

    return None


def _analyze_photo_openrouter(
    image_bytes: bytes,
    mime: str = "image/jpeg",
    extra_info: str = "",
) -> dict | None:
    """Отправляет фото в OpenRouter, перебирая модели из OPENROUTER_MODELS.

    Стратегия:
    - 429 (rate limit) → сразу следующая модель
    - Сетевая ошибка / плохой JSON → до 2 повторов на той же модели, потом следующая
    - Если все модели недоступны → None
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        logger.warning("OPENROUTER_API_KEY не задан, анализ фото пропущен")
        return None

    b64 = base64.b64encode(image_bytes).decode()

    user_text = "Определи блюда на фото и оцени калории."
    if extra_info:
        user_text += (
            f"\n\n⚠️ СОСТАВ УКАЗАН ПОЛЬЗОВАТЕЛЕМ (высший приоритет, не игнорировать): {extra_info}\n"
            "Используй ИМЕННО эти продукты для названия и расчёта калорий. "
            "Фото — только для оценки размера порции если вес не указан."
        )

    for i, model in enumerate(OPENROUTER_MODELS, 1):
        logger.debug("OpenRouter: пробуем модель %d/%d — %s", i, len(OPENROUTER_MODELS), model)
        result = _call_openrouter_model(model, b64, mime, user_text, api_key)
        if result is not None:
            if i > 1:
                logger.info("OpenRouter: успех на модели %d/%d — %s", i, len(OPENROUTER_MODELS), model)
            else:
                logger.debug("OpenRouter: успех — %s", model)
            return result
        logger.warning("OpenRouter: модель %s не дала результат, пробуем следующую…", model)

    logger.error("OpenRouter: все %d моделей исчерпаны, анализ фото не удался", len(OPENROUTER_MODELS))
    return None


# ── Photo handlers ─────────────────────────────────────────────────────────────

async def photo_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Просим пользователя прислать фото."""
    _sync_user(update)
    uid = update.effective_user.id

    if not os.environ.get("OPENROUTER_API_KEY"):
        await update.message.reply_text(
            "⚠️ Функция анализа фото не настроена.\n\n"
            "Добавь в файл `.env`:\n"
            "`OPENROUTER_API_KEY=sk-or-...`\n\n"
            "Бесплатный ключ: https://openrouter.ai/keys",
            parse_mode="Markdown",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    logger.debug("Начало анализа фото: user_id=%d", uid)
    _log_user_msg(update, extra="📷 фото еды — старт")
    _log_bot_reply(uid, "📷 Пришли фото блюда — я определю состав и оценю калории.", extra="photo-prompt")
    await update.message.reply_text(
        "📷 *Пришли фото блюда* — я определю состав и оценю калории.\n\n"
        "_Лучшее качество: вид сверху, хорошее освещение, порция целиком._",
        parse_mode="Markdown",
        reply_markup=CANCEL_KEYBOARD,
    )
    return PHOTO_CAPTION


async def photo_got_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получили фото — сохраняем и спрашиваем уточнение состава."""
    uid = update.effective_user.id

    if not update.message.photo:
        await update.message.reply_text(
            "Пожалуйста, пришли именно *фото* (не файл).",
            parse_mode="Markdown",
        )
        return PHOTO_CAPTION

    # Сохраняем file_id — скачаем позже, после уточнения
    _log_user_msg(update, extra=f"📷 фото получено file_id={update.message.photo[-1].file_id[:20]}…")
    logger.debug("Фото получено: user_id=%d  file_id=%s", uid, update.message.photo[-1].file_id[:20])
    context.user_data["photo_file_id"] = update.message.photo[-1].file_id
    # Если пользователь прислал подпись прямо к фото — сразу используем
    caption = (update.message.caption or "").strip()
    if caption:
        context.user_data["photo_extra"] = caption

    skip_keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("➡️ Пропустить — анализируй без уточнений")],
         [KeyboardButton("❌ Отмена")]],
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "✍️ *Можешь уточнить состав блюда* — это повысит точность оценки.\n\n"
        "Например: _«гречка 200г, куриная грудка 150г, масло 1 ч.л.»_\n\n"
        "Или нажми *Пропустить*, если хочешь чтобы бот определил сам.",
        parse_mode="Markdown",
        reply_markup=skip_keyboard,
    )
    return PHOTO_CONFIRM_KCAL


async def photo_got_caption(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Получили уточнение состава (или пропуск) — или обрабатываем подтверждение после анализа."""
    uid  = update.effective_user.id
    text = update.message.text.strip()

    if text == "❌ Отмена":
        context.user_data.clear()
        await update.message.reply_text("Отменено.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    # ── После анализа: подтверждение / ручной ввод ────────────────────────────
    if context.user_data.get("_photo_analyzed"):

        if text.startswith("✅ Сохранить"):
            name = context.user_data.get("photo_name", "Блюдо с фото")
            kcal = context.user_data.get("photo_kcal", 0)
            db_add_entry(uid, name, kcal)
            badge = _kcal_badge(kcal)
            logger.info("Фото-запись сохранена: user_id=%d «%s» %d ккал", uid, name, kcal)
            _log_user_msg(update, extra=f"✅ подтверждение сохранения фото: {name} {kcal} ккал")
            _log_bot_reply(uid, f"✅ Записано: {name} — {kcal} ккал", extra="photo-saved")
            context.user_data.clear()
            await update.message.reply_text(
                f"✅ Записано: {badge} *{name}* — *{kcal}* ккал",
                parse_mode="Markdown",
                reply_markup=MAIN_KEYBOARD,
            )
            return ConversationHandler.END

        if text == "✏️ Указать калории вручную":
            name = context.user_data.get("photo_name", "Блюдо с фото")
            context.user_data["_photo_manual"] = True
            await update.message.reply_text(
                f"Сколько калорий в *{name}*?",
                parse_mode="Markdown",
                reply_markup=CANCEL_KEYBOARD,
            )
            return PHOTO_CONFIRM_KCAL

        if context.user_data.get("_photo_manual"):
            try:
                kcal = int(text)
                if kcal <= 0:
                    raise ValueError
            except ValueError:
                await update.message.reply_text("⚠️ Введи целое положительное число:")
                return PHOTO_CONFIRM_KCAL
            name = context.user_data.get("photo_name", "Блюдо с фото")
            db_add_entry(uid, name, kcal)
            badge = _kcal_badge(kcal)
            logger.info("Фото-запись (ручная) сохранена: user_id=%d «%s» %d ккал", uid, name, kcal)
            _log_user_msg(update, extra=f"✏️ ручные ккал к фото: {name} {kcal} ккал")
            _log_bot_reply(uid, f"✅ Записано (ручная): {name} — {kcal} ккал", extra="photo-manual-saved")
            context.user_data.clear()
            await update.message.reply_text(
                f"✅ Записано: {badge} *{name}* — *{kcal}* ккал",
                parse_mode="Markdown",
                reply_markup=MAIN_KEYBOARD,
            )
            return ConversationHandler.END

        await update.message.reply_text("Используй кнопки выше.")
        return PHOTO_CONFIRM_KCAL

    # ── Первый вызов: уточнение состава → запускаем анализ ────────────────────
    if text != "➡️ Пропустить — анализируй без уточнений":
        context.user_data["photo_extra"] = text

    extra   = context.user_data.get("photo_extra", "")
    file_id = context.user_data.get("photo_file_id")

    if not file_id:
        await update.message.reply_text("Что-то пошло не так, пришли фото заново.", reply_markup=MAIN_KEYBOARD)
        return ConversationHandler.END

    if extra:
        logger.debug("Фото уточнение: user_id=%d  extra=%s", uid, extra[:60])
        _log_user_msg(update, extra=f"уточнение состава: {extra[:60]}")
        _log_bot_reply(uid, f"🔍 Анализирую с учётом: {extra[:40]}…", extra="photo-analyzing")
        await update.message.reply_text(f"🔍 Анализирую с учётом: _{extra}_…", parse_mode="Markdown")
    else:
        logger.debug("Фото без уточнений: user_id=%d", uid)
        _log_user_msg(update, extra="пропустить уточнение")
        _log_bot_reply(uid, "🔍 Анализирую фото, подожди секунду…", extra="photo-analyzing")
        await update.message.reply_text("🔍 Анализирую фото, подожди секунду…")

    photo_file  = await context.bot.get_file(file_id)
    image_bytes = await photo_file.download_as_bytearray()

    result = _analyze_photo_openrouter(bytes(image_bytes), extra_info=extra)

    if result is None:
        await update.message.reply_text(
            "😔 Не удалось проанализировать фото. Попробуй позже или добавь еду вручную.",
            reply_markup=MAIN_KEYBOARD,
        )
        return ConversationHandler.END

    name = result.get("name", "Блюдо с фото")
    kcal = result.get("kcal", 0)
    note = result.get("note", "")

    context.user_data["photo_name"]     = name
    context.user_data["photo_kcal"]     = kcal
    context.user_data["_photo_analyzed"] = True

    badge = _kcal_badge(kcal)
    logger.info("Фото проанализировано: user_id=%d  «%s» %d ккал", uid, name, kcal)
    _log_bot_reply(uid, f"🍽 {name} — оценка: {kcal} ккал | note: {note[:60]}", extra="photo-result")
    confirm_keyboard = ReplyKeyboardMarkup(
        [
            [KeyboardButton(f"✅ Сохранить {kcal} ккал")],
            [KeyboardButton("✏️ Указать калории вручную")],
            [KeyboardButton("❌ Отмена")],
        ],
        resize_keyboard=True,
    )
    extra_line = f"\n📝 _Учтено: {extra}_\n" if extra else ""
    await update.message.reply_text(
        f"🍽 *{name}*\n"
        f"{badge} Оценка: *{kcal} ккал*\n"
        f"{extra_line}\n"
        f"💬 _{note}_\n\n"
        "Сохранить эту запись?",
        parse_mode="Markdown",
        reply_markup=confirm_keyboard,
    )
    return PHOTO_CONFIRM_KCAL


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

    or_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not or_key:
        logger.warning(
            "OPENROUTER_API_KEY не задан — анализ фото еды будет недоступен. "
            "Получи бесплатный ключ на https://openrouter.ai/keys и добавь в %s", ENV_FILE
        )

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

    edit_conv = ConversationHandler(
        entry_points=[
            CommandHandler("edit", edit_start),
            MessageHandler(filters.Regex("^✏️ Редактировать день$"), edit_start),
        ],
        states={
            EDIT_SELECT_DATE:       [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_select_date)],
            EDIT_SELECT_ACTION:     [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_select_action)],
            EDIT_SELECT_ENTRY:      [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_select_entry)],
            EDIT_ADD_FOOD_NAME:     [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_add_food_name)],
            EDIT_ADD_FOOD_KCAL:     [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_add_food_kcal)],
            EDIT_ADD_WORKOUT_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_add_workout_name)],
            EDIT_ADD_WORKOUT_KCAL:  [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_add_workout_kcal)],
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

    photo_conv = ConversationHandler(
        entry_points=[
            CommandHandler("photo", photo_start),
            MessageHandler(filters.Regex("^📷 Фото еды → калории$"), photo_start),
        ],
        states={
            # Ждём фото (с необязательной подписью)
            PHOTO_CAPTION: [
                MessageHandler(filters.PHOTO, photo_got_image),
            ],
            # Ждём уточнение состава → потом подтверждение/сохранение
            PHOTO_CONFIRM_KCAL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, photo_got_caption),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(conv)
    app.add_handler(workout_conv)
    app.add_handler(treadmill_conv)
    app.add_handler(photo_conv)
    app.add_handler(edit_conv)
    app.add_handler(CallbackQueryHandler(treadmill_add_callback, pattern=r"^tm_add:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))
    app.add_error_handler(error_handler)

    logger.info("Бот слушает обновления...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()