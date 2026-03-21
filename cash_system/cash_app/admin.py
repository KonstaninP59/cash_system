from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.contrib.auth.models import User
from django.utils.html import format_html

from .models import Category, Coupon, History, Product, SalesPlan


class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'description', 'created_at']
    search_fields = ['name']


class ProductAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'quantity', 'price', 'unit', 'expiration_date', 'get_status', 'has_qr']
    list_filter = ['category', 'expiration_date', 'unit']
    search_fields = ['name']
    readonly_fields = ['qr_uuid', 'qr_code_preview']

    def get_status(self, obj):
        if obj.is_expired():
            return 'Просрочен'
        elif obj.is_expiring_soon():
            return 'Скоро истекает'
        return 'Нормальный'
    get_status.short_description = 'Статус'

    def has_qr(self, obj):
        return bool(obj.qr_code)
    has_qr.boolean = True
    has_qr.short_description = 'QR-код'

    def qr_code_preview(self, obj):
        if obj.qr_code:
            return format_html('<img src="{}" style="max-height: 100px;" />', obj.qr_code.url)
        return "Нет QR-кода"
    qr_code_preview.short_description = 'Превью QR-кода'

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not obj.qr_code:
            obj.generate_qr_code()
            obj.save(update_fields=['qr_code'])


class CouponAdmin(admin.ModelAdmin):
    list_display = ['code', 'discount_percent', 'is_active', 'valid_from', 'valid_until', 'used_count', 'max_uses']
    list_filter = ['is_active', 'created_at']
    search_fields = ['code']
    readonly_fields = ['used_count', 'created_at', 'updated_at', 'created_by']

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class HistoryAdmin(admin.ModelAdmin):
    list_display = ['type', 'product', 'quantity', 'total_price', 'coupon', 'date', 'user']
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
        if change:
            obj.updated_by = request.user
        super().save_model(request, obj, form, change)


class CustomUserAdmin(UserAdmin):
    list_display = ['username', 'email', 'first_name', 'last_name', 'is_staff', 'is_active', 'date_joined']
    list_filter = ['is_staff', 'is_superuser', 'is_active']
    search_fields = ['username', 'email', 'first_name', 'last_name']


admin.site.register(Category, CategoryAdmin)
admin.site.register(Product, ProductAdmin)
admin.site.register(Coupon, CouponAdmin)
admin.site.register(History, HistoryAdmin)
admin.site.register(SalesPlan, SalesPlanAdmin)

admin.site.unregister(User)
admin.site.register(User, CustomUserAdmin)
