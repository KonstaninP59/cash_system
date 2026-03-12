from django.utils import timezone

def current_year(request):
    """Контекстный процессор для текущего года"""
    return {'current_year': timezone.now().year}
