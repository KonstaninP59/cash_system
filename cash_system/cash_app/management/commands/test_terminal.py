"""
Команда для тестирования подключения к терминалу
Запуск: python manage.py test_terminal
"""

from django.core.management.base import BaseCommand
from cash_app.payment import check_terminal_status, get_terminal_info


class Command(BaseCommand):
    help = 'Тестирование подключения к платежному терминалу'

    def handle(self, *args, **options):
        self.stdout.write('Проверка подключения к терминалу...')

        info = get_terminal_info()
        self.stdout.write(f"Хост: {info['host']}")
        self.stdout.write(f"Порт: {info['port']}")
        self.stdout.write(f"Таймаут: {info['timeout']} сек.")
        self.stdout.write(f"Интервал опроса: {info['poll_interval']} сек.")

        if check_terminal_status():
            self.stdout.write(self.style.SUCCESS('Терминал доступен'))
        else:
            self.stdout.write(self.style.ERROR('Терминал не доступен'))
