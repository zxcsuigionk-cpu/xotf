import asyncio
import aiosqlite
import logging
import os
from datetime import datetime, timedelta, timezone

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart, CommandObject
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Настройки берутся из переменных окружения Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
AUTHOR_ID = int(os.getenv("AUTHOR_ID", "0"))

# Путь к БД. По умолчанию database.db, но на Render мы переопределим его через переменную окружения
DB_PATH = os.getenv("DB_PATH", "database.db")

logging.basicConfig(level=logging.INFO)
router = Router()

bot: Bot = None


class RegStates(StatesGroup):
    waiting_for_nickname = State()


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                internal_id INTEGER UNIQUE,
                project_nick TEXT,
                username TEXT,
                tg_name TEXT,
                tendons INTEGER DEFAULT 0,
                shards INTEGER DEFAULT 0,
                rank INTEGER DEFAULT 1,
                last_daily TEXT,
                joined_at TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS promos (
                code TEXT PRIMARY KEY,
                tendons INTEGER,
                shards INTEGER
            )
        """)
        await db.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('next_id', '1000')")
        await db.commit()


async def get_next_internal_id(db):
    await db.execute("UPDATE meta SET value = CAST(value AS INTEGER) + 1 WHERE key='next_id'")
    async with db.execute("SELECT value FROM meta WHERE key='next_id'") as cur:
        row = await cur.fetchone()
    return int(row[0]) - 1


async def get_user_by_internal_id(internal_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE internal_id=?", (internal_id,)) as cur:
            return await cur.fetchone()


async def get_user_by_tg_id(tg_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT * FROM users WHERE user_id=?", (tg_id,)) as cur:
            return await cur.fetchone()


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Действие отменено.")


@router.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    await state.clear()
    user = await get_user_by_tg_id(message.from_user.id)
    if user:
        await message.answer(
            f"👋 Ты уже зарегистрирован!\n"
            f"Твой ID: <code>{user[1]}</code>\n"
            f"Ник: <b>{user[2]}</b>\n"
            f"Ранг: <b>{user[7]}</b>"
        )
        return

    await state.set_state(RegStates.waiting_for_nickname)
    await message.answer("👋 Добро пожаловать в отряд!\n\nПожалуйста, напиши свой <b>ник в проекте</b>:")


@router.message(RegStates.waiting_for_nickname)
async def process_nickname(message: Message, state: FSMContext):
    if not message.text:
        await message.answer("Пожалуйста, отправь ник текстом.")
        return

    nickname = message.text.strip()
    if len(nickname) < 2 or len(nickname) > 30:
        await message.answer("Ник должен быть от 2 до 30 символов. Попробуй ещё раз:")
        return

    existing = await get_user_by_tg_id(message.from_user.id)
    if existing:
        await state.clear()
        await message.answer("Ты уже зарегистрирован.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        internal_id = await get_next_internal_id(db)
        await db.execute("""
            INSERT INTO users (user_id, internal_id, project_nick, username, tg_name, joined_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            message.from_user.id,
            internal_id,
            nickname,
            message.from_user.username,
            message.from_user.full_name,
            datetime.now(timezone.utc).isoformat()
        ))
        await db.commit()

    await state.clear()

    await message.answer(
        f"✅ Регистрация завершена!\n\n"
        f"Твой игровой ID: <code>{internal_id}</code>\n"
        f"Ник: <b>{nickname}</b>\n"
        f"Ранг: <b>1</b>\n\n"
        f"Используй /profile для просмотра баланса."
    )

    if AUTHOR_ID != 0 and bot:
        try:
            await bot.send_message(
                AUTHOR_ID,
                f"🆕 <b>Новый участник!</b>\n🎭 Ник: <b>{nickname}</b>\n🆔 ID: <code>{internal_id}</code>\n📱 TG: @{message.from_user.username or 'нет'}"
            )
        except Exception:
            pass


@router.message(Command("profile", "профиль", "me"))
async def cmd_profile(message: Message):
    user = await get_user_by_tg_id(message.from_user.id)
    if not user:
        await message.answer("Сначала напиши /start и зарегистрируйся.")
        return

    await message.answer(
        f"🎭 Ник: <b>{user[2]}</b>\n"
        f"🆔 ID: <code>{user[1]}</code>\n"
        f"🏅 Ранг: <b>{user[7]}</b>\n\n"
        f"🩸 Ржавые сухожилия: <b>{user[5]}</b>\n"
        f"💎 Осколки прошлого: <b>{user[6]}</b>"
    )


@router.message(Command("daily", "бонус"))
async def cmd_daily(message: Message):
    user = await get_user_by_tg_id(message.from_user.id)
    if not user:
        await message.answer("Сначала напиши /start.")
        return

    now = datetime.now(timezone.utc)
    last = user[8]

    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
        except ValueError:
            last_dt = None

        if last_dt and now - last_dt < timedelta(hours=24):
            left = timedelta(hours=24) - (now - last_dt)
            hours = int(left.total_seconds() // 3600)
            minutes = int((left.total_seconds() % 3600) // 60)
            await message.answer(f"⏳ Ты уже получал бонус. Следующий через {hours}ч {minutes}мин.")
            return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET tendons = tendons + 1, last_daily = ? WHERE user_id = ?",
            (now.isoformat(), message.from_user.id)
        )
        await db.commit()

    await message.answer("🎁 Ежедневный бонус получен!\n🩸 +1 Ржавое сухожилие.")


@router.message(Command("top", "топ"))
async def cmd_top(message: Message):
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("""
            SELECT project_nick, tendons, shards, rank FROM users
            ORDER BY (tendons + shards) DESC LIMIT 5
        """) as cur:
            rows = await cur.fetchall()

    if not rows:
        await message.answer("Пока нет участников.")
        return

    text = "🏆 <b>ТОП-5 участников:</b>\n\n"
    for i, (name, t, s, r) in enumerate(rows, 1):
        text += f"{i}. <b>{name}</b> (Ранг {r}) — 🩸 {t} 💎 {s}\n"

    await message.answer(text)


def get_shop_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬆️ Повысить ранг (6 🩸, 2 💎)", callback_data="buy_rank")]
    ])


@router.message(Command("shop", "магазин"))
async def cmd_shop(message: Message):
    user = await get_user_by_tg_id(message.from_user.id)
    if not user:
        await message.answer("Сначала напиши /start.")
        return

    await message.answer(
        f"🛒 <b>Магазин</b>\n\n"
        f"Твой баланс: 🩸 {user[5]} | 💎 {user[6]}\n"
        f"Твой текущий ранг: <b>{user[7]}</b>\n\n"
        f"Доступные товары:\n"
        f"⬆️ Повышение ранга — <b>6 🩸</b> и <b>2 💎</b>",
        reply_markup=get_shop_menu()
    )


@router.callback_query(F.data == "buy_rank")
async def process_buy_rank(callback: CallbackQuery):
    user = await get_user_by_tg_id(callback.from_user.id)
    if not user:
        await callback.answer("Ошибка: сначала напиши /start.", show_alert=True)
        return

    if user[5] < 6 or user[6] < 2:
        await callback.answer("❌ Недостаточно средств! Нужно 6 🩸 и 2 💎.", show_alert=True)
        return

    new_rank = user[7] + 1

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET tendons = tendons - 6, shards = shards - 2, rank = ? WHERE user_id = ?",
            (new_rank, callback.from_user.id)
        )
        await db.commit()

    await callback.message.edit_text(
        f"🎉 Успешно куплено!\n\n"
        f"Твой новый ранг: <b>{new_rank}</b>\n"
        f"Списано: 6 🩸 и 2 💎\n\n"
        f"Остаток: 🩸 {user[5] - 6} | 💎 {user[6] - 2}"
    )
    await callback.answer()

    if AUTHOR_ID != 0 and bot:
        try:
            await bot.send_message(
                AUTHOR_ID,
                f"🛍 <b>Покупка!</b>\n"
                f"👤 {user[2]} (ID {user[1]})\n"
                f"⬆️ Повысил ранг до {new_rank} за 6 🩸 и 2 💎."
            )
        except Exception:
            pass


@router.message(Command("promo", "промокод"))
async def cmd_promo(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Используй: <code>/promo [код]</code>")
        return

    code = command.args.strip().upper()
    user = await get_user_by_tg_id(message.from_user.id)
    if not user:
        await message.answer("Сначала напиши /start.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute("SELECT tendons, shards FROM promos WHERE code=?", (code,)) as cur:
            promo = await cur.fetchone()

        if not promo:
            await message.answer("❌ Неверный или недействительный промокод.")
            return

        t_rew, s_rew = promo
        await db.execute(
            "UPDATE users SET tendons = tendons + ?, shards = shards + ? WHERE user_id = ?",
            (t_rew, s_rew, message.from_user.id)
        )
        await db.execute("DELETE FROM promos WHERE code=?", (code,))
        await db.commit()

    await message.answer(f"🎁 Промокод активирован!\n🩸 +{t_rew} | 💎 +{s_rew}")


@router.message(Command("collect"), F.from_user.id == AUTHOR_ID)
async def cmd_admin_collect(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Используй: <code>/collect [тип] [ID1] [ID2]...</code>")
        return

    args = command.args.split()
    collect_type = args[0].lower()
    ids_str = args[1:]

    if not ids_str:
        await message.answer("Укажи хотя бы один ID.")
        return

    rewards = {
        "joint":  (2, 0, "Совместный сбор"),
        "solo":   (2, 0, "Самостоятельный сбор"),
        "patrol": (1, 1, "Обход"),
        "train":  (2, 0, "Тренировка"),
        "event":  (2, 1, "Ивент"),
        "games":  (2, 0, "Игры"),
        "extra":  (2, 0, "Доп. мероприятия")
    }

    if collect_type not in rewards:
        await message.answer("Неизвестный тип.")
        return

    t_reward, s_reward, name = rewards[collect_type]
    success_count = 0
    failed_ids = []

    for int_id in ids_str:
        try:
            iid = int(int_id)
        except ValueError:
            failed_ids.append(int_id)
            continue

        user = await get_user_by_internal_id(iid)
        if not user:
            failed_ids.append(int_id)
            continue

        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET tendons = tendons + ?, shards = shards + ? WHERE internal_id = ?",
                (t_reward, s_reward, iid)
            )
            await db.commit()

        try:
            msg_text = f"🔔 <b>Начисление за сбор!</b>\nТип: <b>{name}</b>\n🩸 +{t_reward}\n"
            if s_reward > 0:
                msg_text += f"💎 +{s_reward}\n"
            await bot.send_message(user[0], msg_text)
            success_count += 1
        except Exception:
            failed_ids.append(f"{int_id} (заблокировал)")

    report = f"✅ <b>Сбор завершён: {name}</b>\nНачислено: {success_count}\n"
    if failed_ids:
        report += f"❌ Ошибки: {', '.join(failed_ids)}"

    await message.answer(report)


@router.message(Command("give"), F.from_user.id == AUTHOR_ID)
async def cmd_admin_give(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Используй: <code>/give [ID] [сухожилия] [осколки]</code>")
        return

    try:
        args = command.args.split()
        iid = int(args[0])
        t_amount = int(args[1]) if len(args) > 1 else 0
        s_amount = int(args[2]) if len(args) > 2 else 0
    except (ValueError, IndexError):
        await message.answer("Ошибка ввода.")
        return

    if t_amount < 0 or s_amount < 0:
        await message.answer("Суммы не могут быть отрицательными.")
        return

    user = await get_user_by_internal_id(iid)
    if not user:
        await message.answer(f"ID {iid} не найден.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET tendons = tendons + ?, shards = shards + ? WHERE internal_id = ?",
            (t_amount, s_amount, iid)
        )
        await db.commit()

    try:
        msg = "🔔 <b>Ручное начисление!</b>\n"
        if t_amount:
            msg += f"🩸 +{t_amount}\n"
        if s_amount:
            msg += f"💎 +{s_amount}\n"
        await bot.send_message(user[0], msg)
        status = "✅ Уведомление отправлено."
    except Exception:
        status = "⚠️ Не удалось отправить."

    await message.answer(f"Выдано <b>{user[2]}</b>.\n{status}")


@router.message(Command("setmoney"), F.from_user.id == AUTHOR_ID)
async def cmd_admin_setmoney(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Используй: <code>/setmoney [ID] [сухожилия] [осколки]</code>")
        return

    try:
        args = command.args.split()
        iid = int(args[0])
        t_amount = int(args[1]) if len(args) > 1 else 0
        s_amount = int(args[2]) if len(args) > 2 else 0
    except (ValueError, IndexError):
        await message.answer("Ошибка ввода.")
        return

    if t_amount < 0 or s_amount < 0:
        await message.answer("Суммы не могут быть отрицательными.")
        return

    user = await get_user_by_internal_id(iid)
    if not user:
        await message.answer(f"ID {iid} не найден.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET tendons = ?, shards = ? WHERE internal_id = ?",
            (t_amount, s_amount, iid)
        )
        await db.commit()

    try:
        msg = "⚙️ <b>Изменение баланса!</b>\nАдминистратор установил тебе:\n"
        msg += f"🩸 {t_amount}\n"
        msg += f"💎 {s_amount}\n"
        await bot.send_message(user[0], msg)
        status = "✅ Уведомление отправлено."
    except Exception:
        status = "⚠️ Не удалось отправить."

    await message.answer(f"Баланс <b>{user[2]}</b> обновлен.\n{status}")


@router.message(Command("setrank"), F.from_user.id == AUTHOR_ID)
async def cmd_admin_setrank(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Используй: <code>/setrank [ID] [ранг]</code>")
        return

    try:
        args = command.args.split()
        iid = int(args[0])
        new_rank = int(args[1])
    except (ValueError, IndexError):
        await message.answer("Ошибка ввода. Пример: <code>/setrank 1000 5</code>")
        return

    if new_rank < 1:
        await message.answer("Ранг должен быть не меньше 1.")
        return

    user = await get_user_by_internal_id(iid)
    if not user:
        await message.answer(f"ID {iid} не найден.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("UPDATE users SET rank = ? WHERE internal_id = ?", (new_rank, iid))
        await db.commit()

    try:
        await bot.send_message(
            user[0],
            f"⚙️ <b>Изменение ранга!</b>\nАдминистратор установил тебе <b>{new_rank}</b> ранг."
        )
        status = "✅ Уведомление отправлено."
    except Exception:
        status = "⚠️ Не удалось отправить."

    await message.answer(f"Ранг <b>{user[2]}</b> изменен на <b>{new_rank}</b>.\n{status}")


@router.message(Command("createpromo"), F.from_user.id == AUTHOR_ID)
async def cmd_admin_createpromo(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Используй: <code>/createpromo [код] [сухожилия] [осколки]</code>")
        return

    try:
        args = command.args.split()
        code = args[0].upper()
        t_rew = int(args[1])
        s_rew = int(args[2])
    except (ValueError, IndexError):
        await message.answer("Ошибка ввода.")
        return

    if t_rew < 0 or s_rew < 0:
        await message.answer("Награды не могут быть отрицательными.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        try:
            await db.execute("INSERT INTO promos (code, tendons, shards) VALUES (?, ?, ?)", (code, t_rew, s_rew))
            await db.commit()
            await message.answer(f"✅ Промокод <b>{code}</b> создан!\nВыдает: 🩸 {t_rew} | 💎 {s_rew}")
        except Exception:
            await message.answer("❌ Такой промокод уже существует.")


@router.message(Command("info"), F.from_user.id == AUTHOR_ID)
async def cmd_admin_info(message: Message, command: CommandObject):
    if not command.args:
        await message.answer("Используй: <code>/info [ID]</code>")
        return

    try:
        iid = int(command.args.strip())
    except ValueError:
        await message.answer("ID должен быть числом.")
        return

    user = await get_user_by_internal_id(iid)
    if not user:
        await message.answer("Пользователь не найден.")
        return

    joined = user[9][:10] if user[9] else "неизвестно"

    await message.answer(
        f"📋 <b>Инфо:</b>\n"
        f"🎭 Ник: <b>{user[2]}</b>\n"
        f"🆔 ID: <code>{user[1]}</code>\n"
        f"🏅 Ранг: <b>{user[7]}</b>\n"
        f"🩸 Сухожилия: <b>{user[5]}</b>\n"
        f"💎 Осколки: <b>{user[6]}</b>\n"
        f"📅 Регистрация: {joined}"
    )


async def main():
    global bot
    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    dp.include_router(router)

    await init_db()
    logging.info("Бот запущен")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
