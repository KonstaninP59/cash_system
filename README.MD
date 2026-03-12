# Кассовая система

## Для запуска действия по шагам, терминал:
1.   python -m venv venv
2.   venv\Scripts\activate
3.   python -m pip install --upgrade pip
     pip install -r requirements.txt

4.   cd .\cash_system\
5.   python manage.py migrate
6.   python manage.py createsuperuser   
... (создаем админа)
7.   python manage.py runserver  