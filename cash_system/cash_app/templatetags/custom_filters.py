from django import template

register = template.Library()

@register.filter
def get_item(dictionary, key):
    """Получить элемент из словаря по ключу"""
    if dictionary is None:
        return None
    return dictionary.get(key)

@register.filter
def attr(obj, attr_name):
    """Получить атрибут объекта"""
    if obj is None:
        return None
    return getattr(obj, attr_name, None)
