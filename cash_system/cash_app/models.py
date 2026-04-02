import uuid
from datetime import date
from decimal import Decimal
from io import BytesIO

import qrcode
from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.db import models
from django.db.models import Sum
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone


class Category(models.Model):
    """Модель категории товаров"""
    name = models.CharField('Название категории', max_length=100)
    description = models.TextField('Описание', blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = 'Категория'
        verbose_name_plural = 'Категории'
        ordering = ['name']

    def __str__(self):
        return self.name


class StoreAddress(models.Model):
    """Модель адреса склада (магазина)"""
    name = models.CharField('Название', max_length=100)
    address = models.TextField('Адрес')
    city = models.CharField('Город', max_length=100, blank=True)
    phone = models.CharField('Телефон', max_length=20, blank=True)
    is_active = models.BooleanField('Активен', default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Адрес склада'
        verbose_name_plural = 'Адреса складов'
        ordering = ['name']
    
    def __str__(self):
        return self.name


class Product(models.Model):
    """Модель товара (без количества - количество хранится в StoreProduct)"""
    UNIT_CHOICES = [
        ('pcs', 'шт'),
        ('kg', 'кг'),
    ]

    name = models.CharField('Название', max_length=200)
    qr_code = models.ImageField('QR-код', upload_to='qrcodes/', blank=True, null=True)
    qr_uuid = models.UUIDField('UUID для QR', default=uuid.uuid4, editable=False, unique=True)
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Категория',
        related_name='products'
    )
    base_price = models.DecimalField('Базовая цена', max_digits=10, decimal_places=2, default=0)
    price = models.DecimalField('Цена продажи', max_digits=10, decimal_places=2)
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

    def save(self, *args, **kwargs):
        qr_missing_before_save = not bool(self.qr_code)
        super().save(*args, **kwargs)
        
        # Генерируем QR после первого сохранения
        if qr_missing_before_save and not self.qr_code:
            self.generate_qr_code()
            super().save(update_fields=['qr_code'])

    def generate_qr_code(self):
        """Генерация QR-кода для товара"""
        qr_data = f"product:{self.qr_uuid}"
        
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=10,
            border=4,
        )
        qr.add_data(qr_data)
        qr.make(fit=True)
        
        img = qr.make_image(fill_color="black", back_color="white")
        
        buffer = BytesIO()
        img.save(buffer, format='PNG')
        filename = f'qr_{self.pk}_{self.qr_uuid}.png'
        self.qr_code.save(filename, ContentFile(buffer.getvalue()), save=False)

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

    def get_qr_url(self):
        return self.qr_code.url if self.qr_code else None


class StoreProduct(models.Model):
    """Модель товара на конкретном складе"""
    store = models.ForeignKey(StoreAddress, on_delete=models.CASCADE, related_name='store_products', verbose_name='Склад')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='store_products', verbose_name='Товар')
    quantity = models.DecimalField('Количество', max_digits=10, decimal_places=3, default=0)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Товар на складе'
        verbose_name_plural = 'Товары на складах'
        unique_together = ['store', 'product']
    
    def __str__(self):
        return f"{self.store.name} - {self.product.name}: {self.quantity}"


# Сигнал для автоматического создания записей о товарах на всех складах
@receiver(post_save, sender=Product)
def create_store_products(sender, instance, created, **kwargs):
    """При создании нового товара добавляем его на все активные склады с нулевым остатком"""
    if created:
        stores = StoreAddress.objects.filter(is_active=True)
        for store in stores:
            StoreProduct.objects.get_or_create(
                store=store,
                product=instance,
                defaults={'quantity': 0}
            )


@receiver(post_save, sender=StoreAddress)
def create_products_for_new_store(sender, instance, created, **kwargs):
    """При создании нового склада добавляем на него все существующие товары"""
    if created:
        products = Product.objects.all()
        for product in products:
            StoreProduct.objects.get_or_create(
                store=instance,
                product=product,
                defaults={'quantity': 0}
            )


class UserProfile(models.Model):
    """Профиль пользователя"""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile', verbose_name='Пользователь')
    store = models.ForeignKey(StoreAddress, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Привязанный склад')
    position = models.CharField('Должность', max_length=100, blank=True, default='Кассир')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Профиль пользователя'
        verbose_name_plural = 'Профили пользователей'
    
    def __str__(self):
        return f"{self.user.username} - {self.store.name if self.store else 'Не привязан'}"


class Coupon(models.Model):
    """Модель купона на скидку"""
    code = models.CharField('Код купона', max_length=50, unique=True)
    discount_percent = models.PositiveIntegerField('Скидка %', help_text='Процент скидки от 0 до 100')
    is_active = models.BooleanField('Активен', default=True)
    valid_from = models.DateTimeField('Действует с', null=True, blank=True)
    valid_until = models.DateTimeField('Действует до', null=True, blank=True)
    max_uses = models.PositiveIntegerField('Максимальное количество использований', null=True, blank=True)
    used_count = models.PositiveIntegerField('Использовано раз', default=0)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, verbose_name='Кем создан')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Купон'
        verbose_name_plural = 'Купоны'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} ({self.discount_percent}%)"

    def is_valid(self):
        if not self.is_active:
            return False
        now = timezone.now()
        if self.valid_from and now < self.valid_from:
            return False
        if self.valid_until and now > self.valid_until:
            return False
        if self.max_uses is not None and self.used_count >= self.max_uses:
            return False
        return True

    def apply_discount(self, total):
        if self.is_valid():
            total = Decimal(str(total))
            return total * (Decimal('100') - Decimal(str(self.discount_percent))) / Decimal('100')
        return Decimal(str(total))


class History(models.Model):
    """Модель истории движений товаров"""
    TYPE_CHOICES = [
        ('receipt', 'Поступление'),
        ('disposal', 'Утилизация'),
        ('sale', 'Продажа'),
    ]

    type = models.CharField('Тип операции', max_length=20, choices=TYPE_CHOICES)
    product = models.ForeignKey(Product, on_delete=models.PROTECT, verbose_name='Товар')
    store = models.ForeignKey(StoreAddress, on_delete=models.SET_NULL, null=True, blank=True, verbose_name='Склад')
    quantity = models.DecimalField('Количество', max_digits=10, decimal_places=3)
    total_price = models.DecimalField('Сумма', max_digits=10, decimal_places=2, null=True, blank=True)
    reason = models.CharField('Причина', max_length=200, blank=True, null=True)
    coupon = models.ForeignKey(
        Coupon,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name='Применённый купон'
    )
    sale_group = models.UUIDField('Группа продажи', null=True, blank=True, editable=False)
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
    updated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='plan_updates',
        verbose_name='Кем обновлено'
    )

    class Meta:
        verbose_name = 'План продаж'
        verbose_name_plural = 'Планы продаж'

    def __str__(self):
        return f"План {self.user.username}: {self.monthly_target} ₽"

    def get_current_month_sales(self):
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        total = History.objects.filter(
            type='sale',
            user=self.user,
            date__gte=start_of_month
        ).aggregate(total=Sum('total_price'))['total'] or Decimal('0')
        return total

    def get_completion_percentage(self):
        if self.monthly_target == 0:
            return 0
        current = self.get_current_month_sales()
        return round(float((current / self.monthly_target) * 100), 1)

    def get_remaining_amount(self):
        if self.monthly_target == 0:
            return Decimal('0')
        current = self.get_current_month_sales()
        remaining = self.monthly_target - current
        return remaining if remaining > 0 else Decimal('0')

    def get_daily_average(self):
        now = timezone.now()
        start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        days_passed = (now - start_of_month).days + 1
        current = self.get_current_month_sales()
        if days_passed <= 0:
            return 0
        return round(float(current / days_passed), 2)


class PriceList(models.Model):
    """Модель прайс-листа (ценовой категории)"""
    name = models.CharField('Название', max_length=100)
    description = models.TextField('Описание', blank=True)
    multiplier = models.DecimalField('Множитель цены', max_digits=5, decimal_places=2, default=1.00)
    is_active = models.BooleanField('Активен', default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Прайс-лист'
        verbose_name_plural = 'Прайс-листы'
        ordering = ['name']
    
    def __str__(self):
        return f"{self.name} (x{self.multiplier})"


class PriceListItem(models.Model):
    """Модель товара в прайс-листе"""
    price_list = models.ForeignKey(PriceList, on_delete=models.CASCADE, related_name='items', verbose_name='Прайс-лист')
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='price_list_items', verbose_name='Товар')
    custom_price = models.DecimalField('Своя цена', max_digits=10, decimal_places=2, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Товар в прайс-листе'
        verbose_name_plural = 'Товары в прайс-листах'
        unique_together = ['price_list', 'product']
    
    def __str__(self):
        return f"{self.price_list.name} - {self.product.name}"
    
    def get_price(self):
        """Получить цену товара в этом прайс-листе"""
        if self.custom_price is not None:
            return self.custom_price
        return self.product.base_price * self.price_list.multiplier
    