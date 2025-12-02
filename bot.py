"""
PSFastPayBot - minimal, ready-to-deploy Telegram bot (polling) prototype
Requirements: Python 3.8+, aiogram, aiosqlite, python-dotenv, qrcode, pillow, aiohttp
This is a working prototype intended for deployment on free hosts that support long-running processes
(e.g., Render background worker, Railway service).

IMPORTANT: replace placeholders in .env with real values before launch.
"""
import os
import logging
import io
import qrcode
from datetime import datetime
import aiosqlite
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv('BOT_TOKEN')
ADMIN_IDS = [int(x) for x in os.getenv('ADMIN_IDS','').split(',') if x.strip()]

if not BOT_TOKEN:
    raise RuntimeError('BOT_TOKEN not set in environment variables')

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

DB_PATH = os.getenv('DB_PATH', 'psfastpay.db')

CATALOG = [
    {'id': 'ps_plus_essential', 'title': 'PS Plus Essential', 'variants': ['1 мес','3 мес','12 мес'], 'base_price_usd': 5},
    {'id': 'ps_plus_extra', 'title': 'PS Plus Extra', 'variants': ['1 мес','3 мес','12 мес'], 'base_price_usd': 10},
    {'id': 'giftcard', 'title': 'PSN Gift Card (code)', 'variants': ['$10','$20','$50'], 'base_price_usd': None},
]
REGIONS = ['Турция','Польша','США']

class OrderStates(StatesGroup):
    choosing_product = State()
    choosing_region = State()
    confirming = State()
    choosing_payment = State()
    waiting_manual_payment_proof = State()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            product_id TEXT,
            product_title TEXT,
            variant TEXT,
            region TEXT,
            price_usd REAL,
            price_display TEXT,
            currency TEXT,
            status TEXT,
            created_at TEXT,
            payment_method TEXT,
            payment_proof TEXT
        )""")
        await db.execute("""CREATE TABLE IF NOT EXISTS gift_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE,
            denomination TEXT,
            region TEXT,
            used INTEGER DEFAULT 0,
            added_at TEXT
        )""")
        await db.commit()

def main_menu_kb():
    kb = InlineKeyboardMarkup(row_width=2)
    kb.add(InlineKeyboardButton('🛒 Каталог/Купить', callback_data='catalog'))
    kb.add(InlineKeyboardButton('💰 Оплата (Инфо)', callback_data='payments_info'))
    kb.add(InlineKeyboardButton('⚙️ Настройки/Заказы', callback_data='settings'))
    kb.add(InlineKeyboardButton('❓ Помощь/Поддержка', callback_data='help'))
    return kb

def catalog_kb():
    kb = InlineKeyboardMarkup()
    for item in CATALOG:
        kb.add(InlineKeyboardButton(item['title'], callback_data=f"product:{item['id']}"))
    kb.add(InlineKeyboardButton('Назад', callback_data='back_main'))
    return kb

@dp.message_handler(commands=['start'])
async def cmd_start(message: types.Message):
    await message.answer('Привет! Я — PSFastPayBot. Выбери действие:', reply_markup=main_menu_kb())

@dp.callback_query_handler(lambda c: c.data == 'catalog')
async def cb_catalog(query: types.CallbackQuery):
    await query.message.edit_text('Каталог товаров:', reply_markup=catalog_kb())

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('product:'))
async def cb_product(query: types.CallbackQuery, state: FSMContext):
    product_id = query.data.split(':',1)[1]
    product = next((p for p in CATALOG if p['id']==product_id), None)
    if not product:
        await query.answer('Товар не найден')
        return
    await state.update_data(product=product)
    kb = InlineKeyboardMarkup()
    for v in product['variants']:
        kb.add(InlineKeyboardButton(v, callback_data=f'variant:{v}'))
    kb.add(InlineKeyboardButton('Отмена', callback_data='back_main'))
    await OrderStates.choosing_product.set()
    await query.message.edit_text(f"Выбрано: {product['title']}\nВыберите опцию:", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('variant:'), state=OrderStates.choosing_product)
async def cb_variant(query: types.CallbackQuery, state: FSMContext):
    variant = query.data.split(':',1)[1]
    await state.update_data(variant=variant)
    kb = InlineKeyboardMarkup()
    for r in REGIONS:
        kb.add(InlineKeyboardButton(r, callback_data=f'region:{r}'))
    kb.add(InlineKeyboardButton('Отмена', callback_data='back_main'))
    await OrderStates.choosing_region.set()
    await query.message.edit_text('Выберите регион PSN-аккаунта:', reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('region:'), state=OrderStates.choosing_region)
async def cb_region(query: types.CallbackQuery, state: FSMContext):
    region = query.data.split(':',1)[1]
    data = await state.get_data()
    product = data.get('product')
    variant = data.get('variant')
    if product.get('base_price_usd'):
        mult = 1
        if '3' in variant:
            mult = 2.8
        elif '12' in variant:
            mult = 10
        price_usd = product['base_price_usd'] * mult
    else:
        denom = variant.replace('$','')
        price_usd = float(denom)
    # simple conversion placeholder
    price_display = f"{round(price_usd*100,2)} RUB"
    await state.update_data(price_usd=price_usd, price_display=price_display, currency='RUB', region=region)
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton('Перейти к оплате', callback_data='to_payment'))
    kb.add(InlineKeyboardButton('Отмена', callback_data='back_main'))
    await OrderStates.confirming.set()
    await query.message.edit_text(f"Сводка заказа:\n\nТовар: {product['title']} ({variant})\nРегион: {region}\nЦена: {price_display}", reply_markup=kb)

async def create_order_db_entry(order):
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute('''INSERT INTO orders(user_id,username,product_id,product_title,variant,region,price_usd,price_display,currency,status,created_at,payment_method)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''', (order['user_id'], order.get('username'), order['product_id'], order['product_title'], order.get('variant'), order.get('region'), order.get('price_usd'), order.get('price_display'), order.get('currency'), 'pending', order.get('created_at'), order.get('payment_method')))
        await db.commit()
        return cur.lastrowid

@dp.callback_query_handler(lambda c: c.data == 'to_payment', state=OrderStates.confirming)
async def cb_to_payment(query: types.CallbackQuery, state: FSMContext):
    data = await state.get_data()
    kb = InlineKeyboardMarkup()
    kb.add(InlineKeyboardButton('Сбер/Tinkoff (ручной)', callback_data='pay:bank'))
    kb.add(InlineKeyboardButton('USDT (крипто)', callback_data='pay:usdt'))
    kb.add(InlineKeyboardButton('Telegram Stars/Invoices', callback_data='pay:telegram'))
    kb.add(InlineKeyboardButton('TON', callback_data='pay:ton'))
    kb.add(InlineKeyboardButton('Отмена', callback_data='back_main'))
    await OrderStates.choosing_payment.set()
    await query.message.edit_text('Выберите способ оплаты:', reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data and c.data.startswith('pay:'), state=OrderStates.choosing_payment)
async def cb_pay_method(query: types.CallbackQuery, state: FSMContext):
    method = query.data.split(':',1)[1]
    data = await state.get_data()
    order = {
        'user_id': query.from_user.id,
        'username': query.from_user.username,
        'product_id': data['product']['id'],
        'product_title': data['product']['title'],
        'variant': data['variant'],
        'region': data['region'],
        'price_usd': data['price_usd'],
        'price_display': data['price_display'],
        'currency': data['currency'],
        'created_at': datetime.utcnow().isoformat(),
        'payment_method': method
    }
    order_id = await create_order_db_entry(order)
    if method == 'bank':
        card_number = os.getenv('PAYEE_CARD', '4276 0000 0000 0000')
        payload = f"PAYTO:PS Fast Pay;CARD:{card_number};AMOUNT:{data['price_display']}"
        img = qrcode.make(payload)
        bio = io.BytesIO()
        img.save(bio, format='PNG'); bio.seek(0)
        await bot.send_photo(query.from_user.id, photo=InputFile(bio, filename='qr.png'), caption=f"Оплатите {data['price_display']} на карту {card_number}\nПосле оплаты пришлите скриншот с номером заказа #{order_id}")
        await bot.send_message(query.from_user.id, f"Номер заказа: #{order_id}")
    else:
        await bot.send_message(query.from_user.id, f"Создан заказ #{order_id}. Инструкция для метода {method} будет выслана менеджером.")
    await state.finish()

@dp.message_handler(content_types=types.ContentType.PHOTO)
async def handle_photo(message: types.Message):
    caption = message.caption or ''
    import re
    m = re.search(r'#(\d+)', caption)
    if not m:
        await message.reply('Не найден номер заказа. Укажите #<id> в подписи.')
        return
    order_id = int(m.group(1))
    file_id = message.photo[-1].file_id
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE orders SET payment_proof = ?, status = ? WHERE id = ?', (file_id, 'manual_submitted', order_id))
        await db.commit()
    for admin in ADMIN_IDS:
        try:
            await bot.send_message(admin, f"Платёжное подтверждение для заказа #{order_id} от @{message.from_user.username or message.from_user.full_name}. Подтвердите командой: /confirm {order_id}")
        except Exception:
            pass
    await message.reply('Платёжное подтверждение отправлено менеджеру. Ожидайте проверки.')

@dp.message_handler(commands=['orders'])
async def cmd_orders(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply('Доступ запрещён')
        return
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute('SELECT id,product_title,variant,price_display,status,created_at FROM orders ORDER BY id DESC LIMIT 50')
        rows = await cur.fetchall()
    if not rows:
        await message.reply('Заказов нет')
        return
    text = '\n'.join([f"#{r[0]} — {r[1]} {r[2]} — {r[3]} — {r[4]} — {r[5]}" for r in rows])
    await message.reply(text)

@dp.message_handler(commands=['confirm'])
async def cmd_confirm(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.reply('Доступ запрещён')
        return
    parts = message.text.split()
    if len(parts)<2:
        await message.reply('Использование: /confirm <order_id>')
        return
    order_id = int(parts[1])
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('UPDATE orders SET status = ? WHERE id = ?', ('paid', order_id))
        await db.commit()
        cur = await db.execute('SELECT user_id FROM orders WHERE id = ?', (order_id,))
        row = await cur.fetchone()
    if row:
        try:
            await bot.send_message(row[0], f"Заказ #{order_id} оплачен и будет доставлен в ближайшее время.")
        except Exception:
            pass
    await message.reply(f'Заказ #{order_id} помечен как оплаченный.')

if __name__ == '__main__':
    import asyncio
    loop = asyncio.get_event_loop()
    loop.run_until_complete(init_db())
    executor.start_polling(dp, skip_updates=True)
