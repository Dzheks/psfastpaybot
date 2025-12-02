# PSFastPayBot

Минимальный рабочий Telegram-бот для продажи PS-подписок и кодов. Подходит для деплоя на бесплатных хостингах (Render, Railway, и т.д.) как background worker.

## Быстрый старт (локально)
1. Скопируйте проект.
2. Создайте виртуальное окружение и установите зависимости:

```bash
python -m venv venv
source venv/bin/activate  # или venv\Scripts\activate на Windows
pip install -r requirements.txt
```

3. Скопируйте `.env.example` в `.env` и заполните `BOT_TOKEN` и `ADMIN_IDS`.

4. Запустите:

```bash
python bot.py
```

## Деплой на Render (пример)
1. Скопируйте проект в репозиторий (GitHub/GitLab).
2. На Render создайте новый **Background Worker** (или сервис типа Worker).
3. В поле `Build Command` укажите:
```
pip install -r requirements.txt
```
В поле `Start Command` укажите:
```
python bot.py
```
4. Добавьте переменные окружения через Render Dashboard: `BOT_TOKEN`, `ADMIN_IDS`, `PAYEE_CARD`.

## Примечания
- Это прототип. Для продакшена подключайте безопасное хранилище ключей и полноценные платёжные провайдеры.
