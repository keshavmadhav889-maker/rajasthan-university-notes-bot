# Rajasthan University Notes Bot

Telegram-only notes/PDF sales bot for Rajasthan University.

## Planned features
- B.Sc., B.Com, M.Com, M.Sc, M.A, B.A
- B.Sc PCM/PCB streams
- Semester 1-6
- Dynamic subjects and products
- Telegram Stars payments (XTR)
- Automatic PDF delivery after successful payment
- My Purchases
- Telegram admin panel
- User/order/sales management

Secrets are configured through environment variables and are never stored in the repository.

## Current deployment notes (2026)

This bot is designed as a Telegram-only Python bot using long polling and Telegram Stars for digital PDF sales.

### Secrets
Set these environment variables on the host:
- `TELEGRAM_BOT_TOKEN`
- `ADMIN_CHAT_ID`
- `ADMIN_USERNAME`
- `CHANNEL_USERNAME`
- `DATABASE_PATH`
- `BUNDLE_DISCOUNT_PERCENT`

Do not commit real tokens or IDs to GitHub.

### Entry point
`bot.py`

### Local/VM start
`python bot.py`

### Current UX
- Course → B.Sc. stream → semester → subject
- B.Com, M.Com, M.Sc, M.A and B.A menus
- Latest Notes
- Featured Notes
- Keyword Search
- My Purchases
- Join Channel
- Help
- Request Notes
- Complete Semester Pack with configurable bundle discount
- Telegram Stars invoice → pre-checkout verification → successful-payment PDF delivery
- Admin-only Telegram panel
- Users, products, subject catalog, orders, sales and broadcast tools
- Order filters: pending, paid, failed, today and this month
- Top-selling notes metric

### Important hosting note
The bot uses long polling, so it needs a host that permits a continuously running process. A free web service that sleeps when idle is not equivalent to 24/7 bot hosting.

