from django import forms
from django.contrib.auth.forms import AuthenticationForm
from .models import Product, SalesPlan, Coupon

class LoginForm(AuthenticationForm):
    """Форма входа"""
    username = forms.CharField(
        label='Имя пользователя',
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Введите имя'})
    )
    password = forms.CharField(
        label='Пароль',
        widget=forms.PasswordInput(attrs={'class': 'form-control', 'placeholder': 'Введите пароль'})
    )

class ProductForm(forms.ModelForm):
    """Форма для товара"""
    class Meta:
        model = Product
        fields = ['name', 'barcode', 'category', 'quantity', 'price', 'unit', 'expiration_date']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Название товара'}),
            'barcode': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Штрих-код (необязательно)'}),
            'category': forms.Select(attrs={'class': 'form-control'}),
            'quantity': forms.NumberInput(attrs={'class': 'form-control', 'min': 0}),
            'price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01', 'min': 0}),
            'unit': forms.Select(attrs={'class': 'form-control'}),
            'expiration_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
        }
        labels = {
            'name': 'Название',
            'barcode': 'Штрих-код',
            'category': 'Категория',
            'quantity': 'Количество',
            'price': 'Цена',
            'unit': 'Единица измерения',
            'expiration_date': 'Срок годности',
        }

class BarcodeForm(forms.Form):
    """Форма для добавления по штрих-коду"""
    barcode = forms.CharField(
        label='Штрих-код',
        max_length=50,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': 'Введите или отсканируйте штрих-код',
            'autofocus': True
        })
    )
    quantity = forms.IntegerField(
        label='Количество',
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
    )

class SaleForm(forms.Form):
    """Форма для продажи"""
    product_id = forms.IntegerField(widget=forms.HiddenInput())
    quantity = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control form-control-sm', 'style': 'width: 80px;'})
    )

class DisposalForm(forms.Form):
    """Форма для списания товара"""
    quantity = forms.IntegerField(
        label='Количество для списания',
        min_value=1,
        widget=forms.NumberInput(attrs={'class': 'form-control'})
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
            'valid_from': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'valid_until': forms.DateTimeInput(attrs={'class': 'form-control', 'type': 'datetime-local'}),
            'max_uses': forms.NumberInput(attrs={'class': 'form-control', 'min': 0, 'placeholder': 'Оставьте пустым для безлимита'}),
        }
        labels = {
            'code': 'Код купона',
            'discount_percent': 'Процент скидки',
            'is_active': 'Активен',
            'valid_from': 'Действует с',
            'valid_until': 'Действует до',
            'max_uses': 'Максимальное использований',
        }
        