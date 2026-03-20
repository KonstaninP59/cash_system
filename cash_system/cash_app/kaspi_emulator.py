"""
Простой эмулятор Kaspi POS с правильными эндпоинтами
Запуск: python cash_app/kaspi_emulator.py
"""

from http.server import HTTPServer, BaseHTTPRequestHandler
import json
import time
import threading

payments = {}
counter = 0

class KaspiHandler(BaseHTTPRequestHandler):
    
    def do_GET(self):
        # Статус сервера
        if self.path == '/api/v1/status':
            self._send_json({'status': 'ok', 'version': '1.0'})
        
        # Статус платежа
        elif self.path.startswith('/api/v1/payments/'):
            pid = self.path.split('/')[-1]
            data = payments.get(pid, {'status': 'not_found'})
            self._send_json(data)
        
        else:
            self.send_response(404)
            self.end_headers()
            self._send_json({'error': 'Not found'})
    
    def do_POST(self):
        global counter
        
        # Создание платежа
        if self.path == '/api/v1/payments':
            length = int(self.headers['Content-Length'])
            data = json.loads(self.rfile.read(length))
            
            counter += 1
            pid = f"PAY-{counter}"
            
            payments[pid] = {
                'id': pid,
                'status': 'pending',
                'amount': data.get('amount', 0),
                'description': data.get('description', ''),
                'created_at': time.strftime('%Y-%m-%d %H:%M:%S')
            }
            
            print(f"\n{'='*50}")
            print(f"💳 НОВЫЙ ПЛАТЕЖ: {pid}")
            print(f"   Сумма: {data.get('amount', 0)} ₸")
            print(f"   Описание: {data.get('description', '')}")
            print(f"{'='*50}")
            
            # Имитация обработки
            def process():
                print(f"⏳ Обработка платежа {pid}...")
                time.sleep(3)
                payments[pid]['status'] = 'completed'
                payments[pid]['completed_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
                print(f"✅ ПЛАТЕЖ {pid} УСПЕШНО ЗАВЕРШЕН!")
                print(f"   Сумма: {data.get('amount', 0)} ₸")
                print(f"{'='*50}\n")
            
            threading.Thread(target=process).start()
            
            self._send_json({
                'id': pid,
                'status': 'pending',
                'amount': data.get('amount')
            }, 201)
        
        else:
            self.send_response(404)
            self.end_headers()
    
    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def log_message(self, format, *args):
        pass


def run_emulator():
    server = HTTPServer(('127.0.0.1', 8080), KaspiHandler)
    print("=" * 60)
    print("🏦 Kaspi POS Simulator (Рабочий эмулятор)")
    print("=" * 60)
    print(f"Сервер запущен на http://127.0.0.1:8080")
    print("")
    print("Доступные эндпоинты:")
    print("  GET  /api/v1/status           - статус сервера")
    print("  POST /api/v1/payments         - создать платеж")
    print("  GET  /api/v1/payments/{id}    - статус платежа")
    print("")
    print("Ожидание платежей...")
    print("Для остановки нажмите Ctrl+C")
    print("=" * 60)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑 Остановка сервера...")
        server.shutdown()


if __name__ == '__main__':
    run_emulator()
    