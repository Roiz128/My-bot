"""Вход в Telegram по QR-коду (без кода из сообщения).

Запуск: python login_qr.py
Затем в телефоне: Настройки -> Устройства -> Подключить устройство -> навести камеру на QR.

ВАЖНО: SESSION_NAME должен совпадать с именем сессии, которое использует
твой main.py / watcher.py (посмотри в login.py или config.py).
"""
import asyncio
import os

import qrcode
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import PasswordHashInvalidError, SessionPasswordNeededError

load_dotenv()

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_NAME = os.getenv("SESSION_NAME", "watcher")


def show_qr(url: str) -> None:
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make()
    qr.print_ascii(invert=True)


async def main() -> None:
    client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"Уже авторизован как {me.first_name}. Сессия: {SESSION_NAME}.session")
        await client.disconnect()
        return

    qr_login = await client.qr_login()
    while True:
        print("\nОтсканируй QR в приложении Telegram на телефоне:\n")
        show_qr(qr_login.url)
        try:
            await qr_login.wait(timeout=30)
            break
        except asyncio.TimeoutError:
            print("QR истёк, генерирую новый...")
            await qr_login.recreate()
        except SessionPasswordNeededError:
            print("\nНа аккаунте включён облачный пароль (2FA).")
            print("Пароль будет виден на экране. Введи его (или вставь правой кнопкой мыши) и нажми Enter.")
            for _ in range(5):
                pwd = input("Пароль: ").strip()
                if not pwd:
                    print("Пустой пароль, попробуй ещё раз.")
                    continue
                try:
                    await client.sign_in(password=pwd)
                    break
                except PasswordHashInvalidError:
                    print("Неверный пароль, попробуй ещё раз.")
            else:
                print("Слишком много неудачных попыток.")
                await client.disconnect()
                return
            break

    me = await client.get_me()
    print(f"\nГотово! Вошёл как {me.first_name}. Сессия: {SESSION_NAME}.session")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
