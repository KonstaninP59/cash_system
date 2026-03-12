# Кассовая система

## Для запуска действия по шагам, терминал:
1.      python -m venv venv
2.      venv\Scripts\activate
3.      python -m pip install --upgrade pip
4.      pip install -r requirements.txt
5.      cd .\cash_system\
6.      python manage.py makemigrations cash_app
7.      python manage.py migrate
8. ... (создаем админа)      python manage.py createsuperuser   
9.      python manage.py runserver  