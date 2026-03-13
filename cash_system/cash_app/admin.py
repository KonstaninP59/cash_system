from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from .models import Product, History, SalesPlan

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
    list_display = ['type', 'product', 'quantity', 'total_price', 'date', 'user']
    list_filter = ['type', 'date']
    search_fields = ['product__name', 'user__username']

class SalesPlanAdmin(admin.ModelAdmin):
    list_display = ['user', 'monthly_target', 'current_sales', 'completion', 'updated_at']
    list_filter = ['user__is_staff']
    search_fields = ['user__username']
    readonly_fields = ['created_at', 'updated_at', 'updated_by']
    
    def current_sales(self, obj):
        return f"{obj.get_current_month_sales():.2f} ₽"
    current_sales.short_description = 'Продано за месяц'
    
    def completion(self, obj):
        percentage = obj.get_completion_percentage()
        if percentage >= 100:
            return f'✅ {percentage}%'
        elif percentage >= 75:
            return f'👍 {percentage}%'
        elif percentage >= 50:
            return f'👌 {percentage}%'
        else:
            return f'⚠️ {percentage}%'
    completion.short_description = 'Выполнение'
    
    def save_model(self, request, obj, form, change):
        if change:  # Если объект редактируется
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)

admin.site.register(Product, ProductAdmin)
admin.site.register(History, HistoryAdmin)
admin.site.register(SalesPlan, SalesPlanAdmin)

class CustomUserAdmin(UserAdmin):
    list_display = ['username', 'email', 'first_name', 'last_name', 'is_staff', 'date_joined']
    list_filter = ['is_staff', 'is_superuser', 'is_active']
    
    def get_plan(self, obj):
        try:
            return f"{obj.sales_plan.monthly_target} ₽"
        except:
            return '—'
    get_plan.short_description = 'План на месяц'

admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)
