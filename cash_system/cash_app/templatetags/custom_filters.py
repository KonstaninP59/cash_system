from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """
    Получить элемент из словаря по ключу.
    Использование: {{ dictionary|get_item:key }}
    """
    if dictionary is None:
        return None
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None

@register.filter
def attr(obj, attr_name):
    """
    Получить атрибут объекта.
    Использование: {{ object|attr:"attribute_name" }}
    """
    if obj is None:
        return None
    return getattr(obj, attr_name, None)

@register.filter
def get_item_attr(dictionary, key_attr):
    """
    Получить элемент из словаря, где ключ - это атрибут объекта.
    Использование: {{ dictionary|get_item_attr:object.id }}
    """
    if dictionary is None:
        return None
    key = str(key_attr) if hasattr(key_attr, '__str__') else key_attr
    return dictionary.get(key)

@register.filter
def format_quantity(value, unit):
    """
    Форматирует количество в зависимости от единицы измерения.
    Использование: {{ value|format_quantity:unit }}
    """
    try:
        value = float(value)
    except (TypeError, ValueError):
        return value
    
    if unit == 'kg':
        # Для кг - показываем до 3 знаков, но без лишних нулей
        formatted = f"{value:.3f}".rstrip('0').rstrip('.')
        return formatted
    else:
        # Для штучных - целое число
        return str(int(value))

@register.filter
def multiply(value, arg):
    """
    Умножает значение на аргумент.
    Использование: {{ value|multiply:arg }}
    """
    try:
        return float(value) * float(arg)
    except (TypeError, ValueError):
        return 0

@register.filter
def divide(value, arg):
    """
    Делит значение на аргумент.
    Использование: {{ value|divide:arg }}
    """
    try:
        if float(arg) == 0:
            return 0
        return float(value) / float(arg)
    except (TypeError, ValueError):
        return 0

@register.filter
def subtract(value, arg):
    """
    Вычитает аргумент из значения.
    Использование: {{ value|subtract:arg }}
    """
    try:
        return float(value) - float(arg)
    except (TypeError, ValueError):
        return 0
    