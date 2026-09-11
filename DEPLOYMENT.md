# Деплой

## 1. Секреты

Создайте случайный `WEBHOOK_SECRET` длиной 32+ символа. Добавьте в настройках хостинга:

- `TELEGRAM_BOT_TOKEN` — токен @BotFather;
- `WEBHOOK_SECRET` — секрет для Telegram webhook и endpoint настройки;
- `PUBLIC_BASE_URL` — публичный HTTPS-адрес сервиса;
- `POLLING_MODE=false`.

## 2. Запуск

После публикации проверьте `/health`, затем вызовите:

```bash
curl -X POST "$PUBLIC_BASE_URL/setup-webhook" -H "X-Setup-Secret: $WEBHOOK_SECRET"
```

Проверьте состояние:

```bash
curl "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/getWebhookInfo"
```

## 3. Безопасность

Токен не должен быть в репозитории, скриншотах, Reels или логах. Так как токен уже был отправлен в чат, после первого теста рекомендуется перевыпустить его через @BotFather и обновить секрет на хостинге.
