import asyncio
import sqlite3
import random
from datetime import datetime
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

TOKEN = "YOUR_BOT_TOKEN_HERE"


# БД
class DB:
    def __init__(self):
        self.conn = sqlite3.connect("words.db", check_same_thread=False)
        self.cur = self.conn.cursor()
        self.create_tables()

    def create_tables(self):
        self.cur.execute("""CREATE TABLE IF NOT EXISTS words (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            word TEXT,
            translation TEXT,
            times_learned INTEGER DEFAULT 0,
            added_date TEXT
        )""")

        self.cur.execute("""CREATE TABLE IF NOT EXISTS stats (
            user_id INTEGER PRIMARY KEY,
            tests_passed INTEGER DEFAULT 0,
            correct INTEGER DEFAULT 0
        )""")
        self.conn.commit()

    def add_word(self, uid, word, trans):
        date = datetime.now().strftime("%Y-%m-%d %H:%M")
        self.cur.execute("INSERT INTO words (user_id, word, translation, added_date) VALUES (?, ?, ?, ?)",
                         (uid, word, trans, date))
        self.conn.commit()

    def get_words(self, uid):
        self.cur.execute("SELECT * FROM words WHERE user_id=?", (uid,))
        return self.cur.fetchall()

    def get_random(self, uid, count=5):
        self.cur.execute("SELECT * FROM words WHERE user_id=? ORDER BY RANDOM() LIMIT ?", (uid, count))
        return self.cur.fetchall()

    def inc_learned(self, wid):
        self.cur.execute("UPDATE words SET times_learned = times_learned + 1 WHERE id=?", (wid,))
        self.conn.commit()

    def get_stats(self, uid):
        self.cur.execute("SELECT * FROM stats WHERE user_id=?", (uid,))
        res = self.cur.fetchone()
        if not res:
            return (uid, 0, 0)
        return res

    def update_stats(self, uid, correct):
        self.cur.execute("""INSERT OR REPLACE INTO stats (user_id, tests_passed, correct) 
                           VALUES (?, 
                                   COALESCE((SELECT tests_passed FROM stats WHERE user_id=?), 0) + 1,
                                   COALESCE((SELECT correct FROM stats WHERE user_id=?), 0) + ?)""",
                         (uid, uid, uid, 1 if correct else 0))
        self.conn.commit()


db = DB()


# States
class Form(StatesGroup):
    word = State()
    translation = State()


class Testing(StatesGroup):
    active = State()


bot = Bot(token=TOKEN)
dp = Dispatcher(storage=MemoryStorage())


# клавиатуры
def menu():
    kb = [
        [InlineKeyboardButton(text="Добавить слово", callback_data="add")],
        [InlineKeyboardButton(text="Мои слова", callback_data="list")],
        [InlineKeyboardButton(text="Тест", callback_data="test")],
        [InlineKeyboardButton(text="Статистика", callback_data="stats")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)


def back():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Назад", callback_data="menu")]])


@dp.message(Command("start"))
async def start(msg: Message):
    await msg.answer(f"Привет, {msg.from_user.first_name}!\n\nЯ бот для изучения слов. Выбери действие:",
                     reply_markup=menu())


@dp.callback_query(F.data == "menu")
async def go_menu(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("Главное меню:", reply_markup=menu())


@dp.callback_query(F.data == "add")
async def add_start(call: CallbackQuery, state: FSMContext):
    await call.message.edit_text("Напиши слово:")
    await state.set_state(Form.word)


@dp.message(Form.word)
async def word_received(msg: Message, state: FSMContext):
    await state.update_data(word=msg.text)
    await msg.answer("Теперь напиши перевод:")
    await state.set_state(Form.translation)


@dp.message(Form.translation)
async def trans_received(msg: Message, state: FSMContext):
    data = await state.get_data()
    w = data['word']
    t = msg.text

    db.add_word(msg.from_user.id, w, t)

    await msg.answer(f"Слово добавлено!\n\n{w} - {t}", reply_markup=menu())
    await state.clear()


@dp.callback_query(F.data == "list")
async def show_list(call: CallbackQuery):
    words = db.get_words(call.from_user.id)

    if not words:
        await call.message.edit_text("У тебя пока нет слов", reply_markup=back())
        return

    txt = "Твои слова:\n\n"
    for w in words[:20]:
        txt += f"{w[2]} - {w[3]} (повторено: {w[4]})\n"

    await call.message.edit_text(txt, reply_markup=back())


@dp.callback_query(F.data == "stats")
async def show_stats(call: CallbackQuery):
    words = db.get_words(call.from_user.id)
    st = db.get_stats(call.from_user.id)

    total = len(words)
    tests = st[1]
    correct = st[2]

    percent = round(correct / tests * 100, 1) if tests > 0 else 0

    txt = f"Статистика:\n\nВсего слов: {total}\nТестов пройдено: {tests}\nПравильных ответов: {correct}\nТочность: {percent}%"

    await call.message.edit_text(txt, reply_markup=back())


@dp.callback_query(F.data == "test")
async def start_test(call: CallbackQuery, state: FSMContext):
    words = db.get_random(call.from_user.id, 5)

    if len(words) < 2:
        await call.message.edit_text("Добавь хотя бы 2 слова для теста", reply_markup=back())
        return

    await state.set_state(Testing.active)
    await state.update_data(words=words, current=0, score=0)

    await next_q(call.message, state)


async def next_q(msg: Message, state: FSMContext):
    data = await state.get_data()
    words = data['words']
    cur = data['current']

    if cur >= len(words):
        score = data['score']
        total = len(words)

        txt = f"Тест завершен!\n\nПравильных: {score}/{total}"
        await msg.edit_text(txt, reply_markup=menu())
        await state.clear()
        return

    w = words[cur]
    wid, uid, word, correct_trans, _, _ = w

    all_w = db.get_words(uid)
    variants = [x[3] for x in all_w if x[0] != wid]
    random.shuffle(variants)

    options = [correct_trans] + variants[:3]
    random.shuffle(options)

    correct_idx = options.index(correct_trans)

    await state.update_data(wid=wid, correct_idx=correct_idx)

    kb = []
    for i, opt in enumerate(options):
        kb.append([InlineKeyboardButton(text=opt, callback_data=f"ans_{i}")])

    await msg.edit_text(f"Вопрос {cur + 1}/{len(words)}\n\n{word}",
                        reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))


@dp.callback_query(F.data.startswith("ans_"))
async def check_answer(call: CallbackQuery, state: FSMContext):
    idx = int(call.data.split("_")[1])
    data = await state.get_data()
    correct_idx = data['correct_idx']

    is_correct = idx == correct_idx

    if is_correct:
        await call.answer("Правильно!")
        await state.update_data(score=data['score'] + 1)
        db.inc_learned(data['wid'])
        db.update_stats(call.from_user.id, True)
    else:
        await call.answer("Неправильно")
        db.update_stats(call.from_user.id, False)

    await state.update_data(current=data['current'] + 1)
    await next_q(call.message, state)


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())