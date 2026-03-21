"""
Модуль для работы с Kaspi POS Simulator
"""

import socket
import time

import requests

PAYMENT_CONFIG = {
    'host': '127.0.0.1',
    'port': 8080,
    'timeout': 60,
    'poll_interval': 2,
}


class PaymentTerminal:
    def __init__(self):
        self.base_url = f"http://{PAYMENT_CONFIG['host']}:{PAYMENT_CONFIG['port']}"

    def check_connection(self):
        """Проверка доступности терминала через порт"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((PAYMENT_CONFIG['host'], PAYMENT_CONFIG['port']))
        sock.close()
        return result == 0

    def create_payment(self, amount, description):
        """Создание платежа в эмуляторе"""
        payload = {
            'amount': float(amount),
            'currency': 'KZT',
            'description': description,
        }

        try:
            response = requests.post(
                f"{self.base_url}/api/v1/payments",
                json=payload,
                timeout=10
            )
            response.raise_for_status()
            data = response.json()

            payment_id = data.get('id')
            if not payment_id:
                return {'success': False, 'error': 'Терминал не вернул ID платежа'}

            return {
                'success': True,
                'payment_id': str(payment_id),
                'data': data,
            }

        except requests.RequestException as e:
            return {'success': False, 'error': f'Ошибка создания платежа: {e}'}
        except ValueError:
            return {'success': False, 'error': 'Терминал вернул некорректный JSON'}

    def get_payment_status(self, payment_id):
        """Получение статуса платежа"""
        try:
            response = requests.get(
                f"{self.base_url}/api/v1/payments/{payment_id}",
                timeout=5
            )
            response.raise_for_status()
            data = response.json()

            status = data.get('status')
            if not status:
                return {'success': False, 'error': 'Терминал не вернул статус платежа'}

            return {
                'success': True,
                'status': status,
                'data': data,
            }

        except requests.RequestException as e:
            return {'success': False, 'error': f'Ошибка получения статуса: {e}'}
        except ValueError:
            return {'success': False, 'error': 'Терминал вернул некорректный JSON'}

    def process_payment(self, amount, description):
        """Полный процесс оплаты"""
        if not self.check_connection():
            return {
                'success': False,
                'error': 'Терминал недоступен. Запустите Kaspi POS Simulator на порту 8080.'
            }

        create_result = self.create_payment(amount, description)
        if not create_result['success']:
            return create_result

        payment_id = create_result['payment_id']
        start_time = time.time()

        while time.time() - start_time < PAYMENT_CONFIG['timeout']:
            status_result = self.get_payment_status(payment_id)

            if status_result['success']:
                status = status_result['status']

                if status in ['completed', 'success', 'approved', 'paid']:
                    data = status_result.get('data', {})
                    data.setdefault('payment_id', payment_id)
                    return {
                        'success': True,
                        'payment_id': payment_id,
                        'data': data,
                    }

                if status in ['failed', 'declined', 'cancelled', 'error']:
                    return {'success': False, 'error': f'Оплата отклонена: {status}'}

            time.sleep(PAYMENT_CONFIG['poll_interval'])

        return {'success': False, 'error': 'Превышено время ожидания оплаты'}

def process_terminal_payment(amount, description):
    terminal = PaymentTerminal()
    return terminal.process_payment(amount, description)


def check_terminal_status():
    terminal = PaymentTerminal()
    return terminal.check_connection()


def get_terminal_info():
    return {
        'host': PAYMENT_CONFIG['host'],
        'port': PAYMENT_CONFIG['port'],
        'timeout': PAYMENT_CONFIG['timeout'],
        'poll_interval': PAYMENT_CONFIG['poll_interval'],
        'connected': check_terminal_status(),
    }
