from decimal import Decimal

from django import forms
from django.contrib.auth.forms import AuthenticationForm

from .models import Coupon, Product, SalesPlan, StoreAddress


class LoginForm(AuthenticationForm):
    """Форма входа"""
    username = forms.CharField(
        label='Имя пользователя',
        widget=forms.TextInput(attrs={'class':'form-control', 'placeholder': 'Введите имя'})
    )
    password = forms.CharField(
        label='Пароль',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Введите пароль'})
    )


class ProductForm(forms.ModelForm):
    """Форма для товара"""
    
    class Meta:
        model = Product
        fields = ['name', 'category', 'base_price', 'price', 'cost_price', 'unit', 'expiration_date', 'is_composite']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Название товара'}),
            'category': forms.Select(attrs={'class': 'form-control'}),
            'base_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'cost_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': '0'}),
            'unit': forms.Select(attrs={'class': 'form-control'}),
            'expiration_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'is_composite': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
        }
        labels = {
            'name': 'Название',
            'category': 'Категория',
            'base_price': 'Базовая цена (для расчета в прайс-листах)',
            'price': 'Цена продажи',
            'cost_price': 'Себестоимость',
            'unit': 'Единица измерения',
            'expiration_date': 'Срок годности',
            'is_composite': 'Составное блюдо (готовится из ингредиентов)',
        }


class StoreProductForm(forms.Form):
    """Форма для добавления товара на склад"""
    store = forms.ModelChoiceField(
        label='Склад',
        queryset=StoreAddress.objects.filter(is_active=True),
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    product = forms.ModelChoiceField(
        label='Товар',
        queryset=Product.objects.all(),
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    quantity = forms.DecimalField(
        label='Количество',
        min_value=0,
        decimal_places=3,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0'})
    )


class DisposalForm(forms.Form):
    """Форма для списания товара"""
    quantity = forms.DecimalField(
        label='Количество для списания',
        min_value=Decimal('0.001'),
        decimal_places=3,
        max_digits=10,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'step': '0.001', 'min': '0.001'})
    )
    reason = forms.CharField(
        label='Причина списания',
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Например: просрочка, брак'})
    )


class SalesPlanForm(forms.ModelForm):
    """Форма для плана продаж"""
    class Meta:
        model = SalesPlan
        fields = ['monthly_target']
        widgets = {
            'monthly_target': forms.NumberInput(attrs={
                'class': 'form-control',
                'step': '0.01',
                'min': '0',
                'placeholder': 'Сумма плана на месяц'
            }),
        }
        labels = {
            'monthly_target': 'Месячный план (₽)',
        }


class CouponForm(forms.ModelForm):
    """Форма для купона"""

    class Meta:
        model = Coupon
        fields = ['code', 'discount_percent', 'is_active', 'valid_from', 'valid_until', 'max_uses']
        widgets = {
            'code': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Например: SUMMER2024'}),
            'discount_percent': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'max': 100}),
            'is_active': forms.CheckboxInput(attrs={'class': 'form-check-input'}),
            'valid_from': forms.DateTimeInput(
                format='%Y-%m-%dT%H:%M',
                attrs={'class': 'form-control', 'type': 'datetime-local'}
            ),
            'valid_until': forms.DateTimeInput(
                format='%Y-%m-%dT%H:%M',
                attrs={'class': 'form-control', 'type': 'datetime-local'}
            ),
            'max_uses': forms.NumberInput(
                attrs={'class': 'form-control', 'min': 1, 'placeholder': 'Оставьте пустым для безлимита'}
            ),
        }
        labels = {
            'code': 'Код купона',
            'discount_percent': 'Процент скидки',
            'is_active': 'Активен',
            'valid_from': 'Действует с',
            'valid_until': 'Действует до',
            'max_uses': 'Максимальное количество использований',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['valid_from'].input_formats = ['%Y-%m-%dT%H:%M']
        self.fields['valid_until'].input_formats = ['%Y-%m-%dT%H:%M']

    def clean(self):
        cleaned_data = super().clean()
        valid_from = cleaned_data.get('valid_from')
        valid_until = cleaned_data.get('valid_until')
        max_uses = cleaned_data.get('max_uses')
        discount_percent = cleaned_data.get('discount_percent')

        if valid_from and valid_until and valid_until <= valid_from:
            self.add_error('valid_until', 'Дата окончания должна быть позже даты начала.')

        if discount_percent is not None and not (0 <= discount_percent <= 100):
            self.add_error('discount_percent', 'Скидка должна быть от 0 до 100.')

        if max_uses is not None and max_uses < 1:
            self.add_error('max_uses', 'Максимальное количество использований должно быть больше 0.')

        return cleaned_data
