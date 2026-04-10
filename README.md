# Rusty Lake Steam discount tracker

Небольшой Python-скрипт для VDS: проверяет цену `Rusty Lake Bundle` в Steam и шлёт уведомление в Telegram через вашего бота, если цена изменилась или сейчас действует большая скидка.

## Что умеет

- следит за сборником `Rusty Lake Bundle` по Steam bundle id `3669`
- хранит прошлое состояние в `data/rustylake_state.json`
- отправляет уведомление в Telegram при изменении цены/скидки
- умеет отдельно уведомлять о крупной скидке через порог `MIN_DISCOUNT_PERCENT`
- может отправить тестовое сообщение в Telegram
- не требует внешних Python-библиотек

## Что нужно

- Python 3.10+
- Telegram-бот от `@BotFather`
- `chat_id`, куда бот будет писать

## Настройка

1. Скопируйте пример конфига:

```bash
cp .env.example .env
```

2. Заполните `.env`:

```env
TELEGRAM_BOT_TOKEN=ваш_токен
TELEGRAM_CHAT_ID=ваш_chat_id
MIN_DISCOUNT_PERCENT=50
```

3. При необходимости поменяйте страну магазина:

- `STEAM_CC=us` для доллара
- `STEAM_CC=kz` для Казахстана
- `STEAM_CC=pl` для Польши

Важно: цена в Steam зависит от `cc` и иногда от доступности магазина для региона. Для стабильных уведомлений лучше явно задать одну страну.

## Как получить `chat_id`

Самый простой вариант:

1. Напишите вашему боту любое сообщение.
2. Откройте в браузере:

```text
https://api.telegram.org/bot<ВАШ_ТОКЕН>/getUpdates
```

3. Найдите поле `"chat":{"id": ... }`.

## Ручной запуск

Проверка Telegram:

```bash
python3 steam_rustylake_tracker.py --send-test-message
```

Обычная проверка:

```bash
python3 steam_rustylake_tracker.py
```

Принудительно отправить текущее состояние:

```bash
python3 steam_rustylake_tracker.py --force-notify
```

## Как работает логика уведомлений

- если это первый запуск, скрипт просто запоминает цену
- если `NOTIFY_ON_FIRST_RUN=true`, он пришлёт стартовое сообщение сразу
- при следующих запусках отправляет сообщение, когда изменилась цена или скидка
- даже если `NOTIFY_ON_ANY_CHANGE=false`, он всё равно напишет, когда изменение совпало с большой скидкой по `MIN_DISCOUNT_PERCENT`
- ошибки парсинга/сети тоже могут отправляться в Telegram, если `NOTIFY_ON_ERRORS=true`

## Запуск на VDS через cron

Пример проверки каждые 30 минут:

```cron
*/30 * * * * cd /opt/rusty_parser && /usr/bin/python3 steam_rustylake_tracker.py >> /var/log/rustylake_tracker.log 2>&1
```

Если хотите почти realtime, можно запускать раз в 10 минут:

```cron
*/10 * * * * cd /opt/rusty_parser && /usr/bin/python3 steam_rustylake_tracker.py >> /var/log/rustylake_tracker.log 2>&1
```

## Полезные переменные

- `STEAM_BUNDLE_ID` - id сборника в Steam
- `STEAM_BUNDLE_NAME` - имя для уведомлений
- `STEAM_CC` - регион магазина
- `STEAM_LANG` - язык страницы Steam
- `TELEGRAM_BOT_TOKEN` - токен бота
- `TELEGRAM_CHAT_ID` - чат для отправки
- `MIN_DISCOUNT_PERCENT` - порог "большой скидки"
- `NOTIFY_ON_ANY_CHANGE` - слать при любом изменении цены
- `NOTIFY_ON_FIRST_RUN` - слать ли сообщение на самом первом запуске
- `NOTIFY_ON_ERRORS` - слать ли ошибки в Telegram
- `REQUEST_TIMEOUT` - таймаут HTTP-запросов
- `STATE_FILE` - путь к JSON-файлу состояния