# VoiceTransmite

Веб-сервис для транскрибации аудио в текст через OpenAI Whisper.

## Быстрый старт

### 1. Установка зависимостей

```bash
cd backend
pip install -r requirements.txt
```

### 2. Настройка .env

```bash
cp .env.example .env
# Открой .env и вставь OPENAI_API_KEY
```

### 3. Запуск

```bash
cd backend
python main.py
```

Открой http://localhost:8000

## Структура проекта

```
voice_transmite/
├── backend/
│   ├── main.py          # FastAPI приложение
│   ├── database.py      # Модели SQLite
│   ├── auth.py          # JWT авторизация
│   ├── usage.py         # Учёт лимитов
│   └── requirements.txt
├── frontend/
│   ├── index.html       # Лендинг
│   ├── app.html         # Приложение
│   ├── style.css
│   ├── auth.js
│   └── app.js
└── .env.example
```

## Тарифы

| Тариф    | Лимит         | Цена    |
|----------|---------------|---------|
| free     | 30 мин/мес    | 0 ₽     |
| standard | 300 мин/мес   | 199 ₽   |
| pro      | Безлимит      | 499 ₽   |

## Деплой на Railway

1. Зарегистрируйся на railway.app
2. New Project → Deploy from GitHub
3. Добавь переменные окружения из .env
4. Start command: `cd backend && uvicorn main:app --host 0.0.0.0 --port $PORT`
