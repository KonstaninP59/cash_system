from django.db import models
from django.contrib.auth.models import User
from datetime import date, datetime
from django.utils import timezone

class Product(models.Model):
    """Модель товара"""
    UNIT_CHOICES = [
        ('pcs', 'шт'),
        ('kg', 'кг'),
    ]
    
    name = models.CharField('Название', max_length=200)
    barcode = models.CharField('Штрих-код', max_length=50, blank=True, null=True, unique=True)
    quantity = models.PositiveIntegerField('Количество', default=0)
    price = models.DecimalField('Цена', max_digits=10, decimal_places=2)
    unit = models.CharField('Единица измерения', max_length=3, choices=UNIT_CHOICES, default='pcs')
    expiration_date = models.DateField('Срок годности')
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)

    class Meta:
        verbose_name = 'Товар'
        verbose_name_plural = 'Товары'
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.get_unit_display()})"

    def is_expired(self):
        return self.expiration_date < date.today()

    def is_expiring_soon(self, days=3):
        if self.is_expired():
            return False
        days_left = (self.expiration_date - date.today()).days
        return 0 <= days_left <= days

    def get_status(self):
        if self.is_expired():
            return 'expired'
        elif self.is_expiring_soon():
            return 'expiring_soon'
        return 'good'

class History(models.Model):
    """Модель истории движений товаров"""
    TYPE_CHOICES = [
        ('receipt', 'Поступление'),
        ('disposal', 'Утилизация'),
        ('sale', 'Продажа'),
    ]

    type = models.CharField('Тип операции', max_length=20, choices=TYPE_CHOICES)
    product = models.ForeignKey(Product, on_delete=models.CASCADE, verbose_name='Товар')
    quantity = models.PositiveIntegerField('Количество')
    total_price = models.DecimalField('Сумма', max_digits=10, decimal_places=2, null=True, blank=True)
    reason = models.CharField('Причина', max_length=200, blank=True, null=True)
    date = models.DateTimeField('Дата операции', auto_now_add=True)
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Пользователь')

    class Meta:
        verbose_name = 'Запись истории'
        verbose_name_plural = 'История'
        ordering = ['-date']

    def __str__(self):
        return f"{self.get_type_display()} - {self.product.name} - {self.quantity} {self.product.get_unit_display()}"

class SalesPlan(models.Model):
    """Модель плана продаж для пользователей"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='sales_plan', verbose_name='Пользователь')
    monthly_target = models.DecimalField('Месячный план', max_digits=10, decimal_places=2, default=0)
    created_at = models.DateTimeField('Дата создания', auto_now_add=True)
    updated_at = models.DateTimeField('Дата обновления', auto_now=True)
    updated_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='plan_updates', verbose_name='Кем обновлено')

    class Meta:
        verbose_name = 'План продаж'
        verbose_name_plural = 'Планы продаж'

    def __str__(self):
        return f"План {self.user.username}: {self.monthly_target} ₽"

    def get_current_month_sales(self):
        """Получить сумму продаж за текущий месяц"""
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        sales = History.objects.filter(
            type='sale',
            user=self.user,
            date__gte=start_of_month
        )
        total = sum(float(sale.total_price or 0) for sale in sales)
        return total

    def get_completion_percentage(self):
        """Процент выполнения плана"""
        if self.monthly_target == 0:
            return 0
        current = self.get_current_month_sales()
        return min(100, round((current / float(self.monthly_target)) * 100, 1))

    def get_remaining_amount(self):
        """Осталось выполнить до плана"""
        if self.monthly_target == 0:
            return 0
        current = self.get_current_month_sales()
        remaining = float(self.monthly_target) - current
        return max(0, remaining)

    def get_daily_average(self):
        """Средняя сумма продаж в день"""
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days_passed = (now - start_of_month).days + 1
        current = self.get_current_month_sales()
        if days_passed == 0:
            return 0
        return round(current / days_passed, 2)
    