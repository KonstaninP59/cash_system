from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import Product, History

class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'quantity', 'price', 'expiration_date', 'get_status']
    list_filter = ['expiration_date']
    search_fields = ['name']

    def get_status(self, obj):
        if obj.is_expired():
            return 'Просрочен'
        elif obj.is_expiring_soon():
            return 'Скоро истекает'
        return 'Нормальный'
    get_status.short_description = 'Статус'

class HistoryAdmin(admin.ModelAdmin):
    list_display = ['type', 'product', 'quantity', 'date', 'user']
    list_filter = ['type', 'date']
    search_fields = ['product__name']

admin.site.register(Product, ProductAdmin)
admin.site.register(History, HistoryAdmin)

# Расширяем стандартного пользователя
class CustomUserAdmin(UserAdmin):
    list_display = ['username', 'email', 'first_name', 'last_name', 'is_staff', 'date_joined']
    list_filter = ['is_staff', 'is_superuser', 'is_active']

admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)
