import calendar
import json
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from io import BytesIO

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F, Q, Sum
from django.db.models.deletion import ProtectedError
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .forms import CouponForm, DisposalForm, LoginForm, ProductForm, SalesPlanForm
from .models import (
    Category, Coupon, History, Product, SalesPlan, 
    PriceList, PriceListItem, StoreAddress, StoreProduct, UserProfile,
    SalarySettings, SalaryCalculation, Recipe, RecipeIngredient
)
from .payment import check_terminal_status, process_terminal_payment


def is_admin(user):
    """Проверка, является ли пользователь администратором"""
    return user.is_staff or user.is_superuser


def is_cashier(user):
    """Проверка, является ли пользователь кассиром (обычный пользователь)"""
    return not user.is_staff and not user.is_superuser


def get_user_store(user):
    """Получить склад, к которому привязан пользователь"""
    try:
        if hasattr(user, 'profile') and user.profile:
            return user.profile.store
    except UserProfile.DoesNotExist:
        pass
    return None


def parse_decimal_quantity(value):
    """
    Преобразует значение в Decimal, корректно обрабатывая запятые и точки
    """
    if value is None:
        raise ValueError('Количество не указано')
    
    # Преобразуем в строку и заменяем запятую на точку
    str_value = str(value).strip().replace(',', '.')
    
    # Проверяем, что это число
    if not str_value:
        raise ValueError('Количество не может быть пустым')
    
    try:
        # Пробуем преобразовать в Decimal
        result = Decimal(str_value)
        return result
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'Неверный формат количества: "{value}"')


def validate_quantity_for_product(product, quantity):
    """
    Проверяет количество товара на корректность
    """
    if quantity <= 0:
        raise ValueError('Количество должно быть больше 0')

    if product.unit == 'kg':
        # Для кг минимальное количество 0.001
        if quantity < Decimal('0.001'):
            raise ValueError(f'Минимальное количество для "{product.name}" — 0,001 кг')
    elif product.unit == 'pcs':
        # Для штучных количество должно быть целым
        if quantity != quantity.to_integral_value():
            raise ValueError(f'Для товара "{product.name}" количество должно быть целым числом')


def login_view(request):
    """Вход в систему"""
    if request.user.is_authenticated:
        return redirect('product_list')
    
    if request.method == 'POST':
        form = LoginForm(data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            return redirect('product_list')
    else:
        form = LoginForm()
    
    return render(request, 'cash_app/login.html', {'form': form})


def logout_view(request):
    """Выход из системы"""
    logout(request)
    return redirect('login')


@login_required
def index(request):
    """Главная страница"""
    if is_admin(request.user):
        return redirect('product_list')
    else:
        return redirect('sale')


@login_required
def product_list(request):
    """Список товаров с фильтрацией по статусу и поиском (для админа - все склады)"""
    if is_cashier(request.user):
        return redirect('sale')
    
    # Получаем выбранный склад из сессии
    selected_store_id = request.session.get('selected_store')
    selected_store = None
    if selected_store_id:
        try:
            selected_store = StoreAddress.objects.get(pk=selected_store_id, is_active=True)
        except StoreAddress.DoesNotExist:
            selected_store = None
    
    filter_type = request.GET.get('filter', 'all')
    search_query = request.GET.get('search', '').strip()
    
    # Базовый запрос товаров
    all_products = Product.objects.all()
    
    # Создаем список товаров с количеством
    products_with_quantity = []
    
    for product in all_products:
        # Получаем количество в зависимости от выбранного склада
        if selected_store:
            store_product = StoreProduct.objects.filter(store=selected_store, product=product).first()
            quantity = store_product.quantity if store_product else 0
        else:
            total_quantity = StoreProduct.objects.filter(product=product).aggregate(total=Sum('quantity'))['total'] or 0
            quantity = total_quantity
        
        # Добавляем количество к объекту товара
        product.current_quantity = quantity
        products_with_quantity.append(product)
    
    # Применяем поиск по названию
    if search_query:
        search_query_lower = search_query.lower()
        filtered_by_search = []
        for product in products_with_quantity:
            name_lower = product.name.lower() if product.name else ''
            if search_query_lower in name_lower:
                filtered_by_search.append(product)
        products_with_quantity = filtered_by_search
        search_title = f'Результаты поиска: "{search_query}"'
    else:
        search_title = None
    
    # Применяем фильтр по статусу
    if filter_type == 'expired':
        products_with_quantity = [p for p in products_with_quantity if p.is_expired()]
        filter_title = "Просроченные товары"
    elif filter_type == 'expiring_soon':
        products_with_quantity = [p for p in products_with_quantity if p.is_expiring_soon()]
        filter_title = "Товары с истекающим сроком годности"
    else:
        filter_type = 'all'
        filter_title = "Все товары"
    
    # Подсчет статистики (для всех товаров, без учета фильтрации)
    total_products = 0
    expired_count = 0
    expiring_soon_count = 0
    
    for product in all_products:
        if selected_store:
            store_product = StoreProduct.objects.filter(store=selected_store, product=product).first()
            quantity = store_product.quantity if store_product else 0
        else:
            quantity = StoreProduct.objects.filter(product=product).aggregate(total=Sum('quantity'))['total'] or 0
        
        if quantity > 0:
            total_products += 1
            if product.is_expired():
                expired_count += 1
            elif product.is_expiring_soon():
                expiring_soon_count += 1
    
    stores = StoreAddress.objects.filter(is_active=True)
    
    context = {
        'products': products_with_quantity,
        'total_products': total_products,
        'expired_count': expired_count,
        'expiring_soon_count': expiring_soon_count,
        'current_filter': filter_type,
        'filter_title': filter_title,
        'search_query': search_query,
        'search_title': search_title,
        'stores': stores,
        'selected_store': selected_store,
    }
    return render(request, 'cash_app/product_list.html', context)


@login_required
@user_passes_test(is_admin)
def product_create(request):
    """Создание товара (только для администратора)"""
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            # Временно отключаем сигналы, чтобы избежать дублирования
            from django.db.models.signals import post_save
            from .models import create_store_products
            
            post_save.disconnect(create_store_products, sender=Product)
            
            product = form.save()
            
            # Включаем сигналы обратно
            post_save.connect(create_store_products, sender=Product)
            
            # Получаем выбранный склад из POST
            store_id = request.POST.get('store_id')
            quantity_str = request.POST.get('quantity', '0').replace(',', '.')
            
            try:
                quantity = Decimal(quantity_str)
            except:
                quantity = Decimal('0')
            
            # Добавляем товар на выбранный склад
            if store_id:
                try:
                    store = StoreAddress.objects.get(pk=store_id, is_active=True)
                    # Используем update_or_create вместо create
                    store_product, created = StoreProduct.objects.update_or_create(
                        store=store,
                        product=product,
                        defaults={'quantity': quantity}
                    )
                    
                    # Создаем запись в истории о поступлении
                    if quantity > 0:
                        History.objects.create(
                            type='receipt',
                            product=product,
                            store=store,
                            quantity=quantity,
                            user=request.user
                        )
                    
                    if created:
                        messages.success(request, f'Товар "{product.name}" добавлен на склад "{store.name}" в количестве {quantity} {product.get_unit_display()}')
                    else:
                        messages.success(request, f'Количество товара "{product.name}" на складе "{store.name}" обновлено до {quantity} {product.get_unit_display()}')
                except StoreAddress.DoesNotExist:
                    messages.warning(request, 'Выбранный склад не найден. Товар создан без привязки к складу.')
            else:
                messages.warning(request, 'Склад не выбран. Товар создан без привязки к складу.')
            
            # Генерация QR-кода
            if not product.qr_code:
                product.generate_qr_code()
                product.save()
            
            messages.success(request, f'Товар "{product.name}" успешно добавлен!')
            return redirect('product_list')
    else:
        form = ProductForm()
    
    context = {
        'form': form,
        'title': 'Добавление товара',
        'stores': StoreAddress.objects.filter(is_active=True),
        'product': None,
        'current_store_id': None,
        'current_quantity': 0,
    }
    return render(request, 'cash_app/product_form.html', context)


@login_required
@user_passes_test(is_admin)
def product_update(request, pk):
    """Редактирование товара (только для администратора)"""
    product = get_object_or_404(Product, pk=pk)
    
    # Получаем текущую привязку к складу
    current_store_product = StoreProduct.objects.filter(product=product).first()
    current_store_id = current_store_product.store.id if current_store_product else None
    current_quantity = current_store_product.quantity if current_store_product else 0
    
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            product = form.save()
            
            # Получаем выбранный склад и количество из POST
            store_id = request.POST.get('store_id')
            quantity_str = request.POST.get('quantity', '0').replace(',', '.')
            
            try:
                quantity = Decimal(quantity_str)
            except:
                quantity = Decimal('0')
            
            # Обновляем привязку к складу
            if store_id:
                try:
                    store = StoreAddress.objects.get(pk=store_id, is_active=True)
                    old_quantity = current_quantity
                    
                    store_product, created = StoreProduct.objects.update_or_create(
                        store=store,
                        product=product,
                        defaults={'quantity': quantity}
                    )
                    
                    # Если количество изменилось, создаем запись в истории
                    if quantity != old_quantity:
                        if quantity > old_quantity:
                            History.objects.create(
                                type='receipt',
                                product=product,
                                store=store,
                                quantity=quantity - old_quantity,
                                user=request.user
                            )
                        else:
                            History.objects.create(
                                type='disposal',
                                product=product,
                                store=store,
                                quantity=old_quantity - quantity,
                                user=request.user,
                                reason='Корректировка остатков'
                            )
                    
                    messages.success(request, f'Товар "{product.name}" обновлен на складе "{store.name}"')
                except StoreAddress.DoesNotExist:
                    messages.warning(request, 'Выбранный склад не найден')
            else:
                messages.warning(request, 'Склад не выбран')
            
            messages.success(request, f'Товар "{product.name}" успешно обновлен!')
            return redirect('product_list')
    else:
        form = ProductForm(instance=product)
    
    context = {
        'form': form,
        'title': 'Редактирование товара',
        'product': product,
        'stores': StoreAddress.objects.filter(is_active=True),
        'current_store_id': current_store_id,
        'current_quantity': current_quantity,
    }
    return render(request, 'cash_app/product_form.html', context)


@login_required
@user_passes_test(is_admin)
def product_delete(request, pk):
    """Удаление товара со всех складов (только для администратора)"""
    product = get_object_or_404(Product, pk=pk)

    if request.method == 'POST':
        try:
            # Удаляем товар из прайс-листов
            PriceListItem.objects.filter(product=product).delete()
            
            # Удаляем все записи о товаре на складах
            StoreProduct.objects.filter(product=product).delete()
            
            # Удаляем записи в истории (если нужно - используйте PROTECT или CASCADE)
            History.objects.filter(product=product).delete()
            
            # Удаляем сам товар
            product.delete()
            
            messages.success(request, f'Товар "{product.name}" успешно удален со всех складов и из прайс-листов!')
        except ProtectedError:
            messages.error(
                request,
                'Нельзя удалить товар, по которому уже есть история операций. '
                'Сначала обнулите остаток и оставьте товар в системе для сохранения отчетности.'
            )
        return redirect('product_list')

    return render(request, 'cash_app/product_confirm_delete.html', {'product': product})


@login_required
@user_passes_test(is_admin)
def product_disposal(request, pk):
    """Списание товара (только для администратора)"""
    product = get_object_or_404(Product, pk=pk)

    if not product.is_expired():
        messages.error(request, 'Можно списывать только просроченные товары!')
        return redirect('product_list')

    if request.method == 'POST':
        form = DisposalForm(request.POST)
        if form.is_valid():
            quantity = form.cleaned_data['quantity']
            reason = form.cleaned_data['reason']
            
            # Получаем выбранный склад
            store_id = request.POST.get('store_id')
            store = None
            if store_id:
                try:
                    store = StoreAddress.objects.get(pk=store_id)
                except StoreAddress.DoesNotExist:
                    messages.error(request, 'Выбранный склад не найден')
                    return redirect('product_disposal', pk=product.pk)
            else:
                messages.error(request, 'Выберите склад для списания')
                return redirect('product_disposal', pk=product.pk)

            try:
                validate_quantity_for_product(product, quantity)
            except ValueError as e:
                messages.error(request, str(e))
                return redirect('product_disposal', pk=product.pk)

            # Проверяем наличие на выбранном складе
            store_product = StoreProduct.objects.filter(store=store, product=product).first()
            if not store_product or store_product.quantity < quantity:
                available = store_product.quantity if store_product else 0
                messages.error(request, f'Недостаточно товара на складе "{store.name}"! Доступно: {available} {product.get_unit_display()}')
                return redirect('product_disposal', pk=product.pk)
            
            # Списываем товар
            store_product.quantity -= quantity
            store_product.save()

            # Создаем запись в истории
            History.objects.create(
                type='disposal',
                product=product,
                store=store,
                quantity=quantity,
                user=request.user,
                reason=reason
            )

            messages.success(request, f'Списано {quantity} {product.get_unit_display()} товара "{product.name}" со склада "{store.name}"')
            return redirect('product_list')
    else:
        form = DisposalForm(initial={'quantity': 0})

    # Получаем остатки на складах для отображения
    store_products = StoreProduct.objects.filter(product=product).select_related('store')
    
    context = {
        'product': product,
        'form': form,
        'stores': StoreAddress.objects.filter(is_active=True),
        'store_products': store_products,
    }
    return render(request, 'cash_app/product_disposal.html', context)


@login_required
def history_list(request):
    """История движений товаров с группировкой по датам и продажам"""
    type_filter = request.GET.get('type')
    product_filter = request.GET.get('product')
    store_filter = request.GET.get('store')
    date_group = request.GET.get('date_group', 'by_date')
    
    base_qs = History.objects.all().select_related('product', 'user', 'coupon', 'store')
    
    # Кассиры видят только свои продажи
    if is_cashier(request.user):
        base_qs = base_qs.filter(type='sale', user=request.user)
    else:
        # Фильтр по типу для администратора
        if type_filter:
            if type_filter in ['receipt', 'disposal']:
                base_qs = base_qs.filter(type=type_filter)
            elif type_filter == 'sale':
                base_qs = base_qs.filter(type='sale')
    
    # Фильтр по товару (для всех)
    if product_filter:
        base_qs = base_qs.filter(product_id=product_filter)
    
    # Фильтр по складу (только для администратора)
    if store_filter and not is_cashier(request.user):
        base_qs = base_qs.filter(store_id=store_filter)
    
    # Группируем продажи по sale_group
    sales = base_qs.filter(type='sale').order_by('-date')
    other = base_qs.exclude(type='sale').order_by('-date')
    
    # Группируем продажи в один чек
    grouped_sales = {}
    for sale in sales:
        if sale.sale_group not in grouped_sales:
            grouped_sales[sale.sale_group] = {
                'type': 'sale_group',
                'sale_group': sale.sale_group,
                'date': sale.date,
                'user': sale.user,
                'coupon': sale.coupon,
                'store': sale.store,
                'items': [],
                'total': 0,
                'quantity_total': 0,
            }
        grouped_sales[sale.sale_group]['items'].append(sale)
        grouped_sales[sale.sale_group]['total'] += float(sale.total_price or 0)
        grouped_sales[sale.sale_group]['quantity_total'] += float(sale.quantity)
    
    grouped_sales_list = list(grouped_sales.values())
    grouped_sales_list.sort(key=lambda x: x['date'], reverse=True)
    
    # Объединяем все записи
    all_entries = list(other) + grouped_sales_list
    all_entries.sort(key=lambda x: x.date if hasattr(x, 'date') else x['date'], reverse=True)
    
    # Группировка по датам
    entries_by_date = {}
    for entry in all_entries:
        date_key = entry.date.strftime('%d.%m.%Y') if hasattr(entry, 'date') else entry['date'].strftime('%d.%m.%Y')
        if date_key not in entries_by_date:
            entries_by_date[date_key] = []
        entries_by_date[date_key].append(entry)
    
    context = {
        'entries_by_date': entries_by_date,
        'type_choices': History.TYPE_CHOICES,
        'products': Product.objects.all(),
        'stores': StoreAddress.objects.filter(is_active=True),
        'current_type': type_filter,
        'current_product': product_filter,
        'current_store': store_filter,
        'is_cashier': is_cashier(request.user),
        'date_group': date_group,
    }
    return render(request, 'cash_app/history_list.html', context)


@login_required
def sale_view(request):
    """Продажа товаров (только для кассиров)"""
    # Администраторы не имеют доступа к продажам
    if is_admin(request.user):
        messages.error(request, 'Администраторы не имеют доступа к разделу продаж')
        return redirect('dashboard')

    # Получаем склад пользователя
    user_store = get_user_store(request.user)
    if not user_store:
        messages.error(request, 'Ваш аккаунт не привязан к складу. Обратитесь к администратору.')
        return redirect('dashboard')
    
    if request.method == 'POST':
        cart = request.session.get('cart', {})
        
        if not cart:
            messages.warning(request, 'Корзина пуста!')
            return redirect('sale')
        
        # Получаем купон
        applied_coupon_id = request.session.get('applied_coupon')
        coupon = None
        discount_factor = 1.0
        
        if applied_coupon_id:
            try:
                coupon = Coupon.objects.get(pk=applied_coupon_id)
                if coupon.is_valid():
                    discount_factor = (100 - coupon.discount_percent) / 100
                else:
                    coupon = None
                    del request.session['applied_coupon']
            except:
                del request.session['applied_coupon']
        
        try:
            sale_items = []
            total_without_discount = Decimal('0.00')
            sale_group_id = uuid.uuid4()
            
            for product_id, item in cart.items():
                product = Product.objects.get(pk=product_id)
                quantity = Decimal(str(item['quantity']).replace(',', '.'))
                
                # Проверка минимального количества для кг
                if product.unit == 'kg' and quantity < Decimal('0.001'):
                    raise ValueError(f'Минимальное количество для "{product.name}" - 0,001 кг')
                
                # Проверка наличия на складе
                store_product = StoreProduct.objects.filter(store=user_store, product=product).first()
                available_quantity = store_product.quantity if store_product else 0
                
                if float(available_quantity) < float(quantity):
                    raise ValueError(f'Недостаточно "{product.name}" на складе')
                
                # Проверка на просрочку
                if product.expiration_date < date.today():
                    raise ValueError(f'Товар "{product.name}" просрочен')
                
                item_price = Decimal(str(item['price']))
                item_total = item_price * quantity
                total_without_discount += item_total
                
                sale_items.append({
                    'product_id': product.id,
                    'product_name': product.name,
                    'quantity': float(quantity) if product.unit == 'kg' else int(quantity),
                    'price': float(item_price.quantize(Decimal('0.01'))),
                    'total': float((item_total * Decimal(str(discount_factor))).quantize(Decimal('0.01'))),
                    'unit': product.get_unit_display(),
                })
            
            total_with_discount = (total_without_discount * Decimal(str(discount_factor))).quantize(Decimal('0.01'))
            discount_amount = (total_without_discount - total_with_discount).quantize(Decimal('0.01')) if coupon else Decimal('0.00')   
            
            # Сохраняем в сессию
            request.session['pending_sale'] = {
                'sale_items': sale_items,
                'total_without_discount': round(float(total_without_discount), 2),
                'total_with_discount': float(total_with_discount),
                'discount_amount': float(discount_amount),
                'coupon_id': coupon.id if coupon else None,
                'sale_group_id': str(sale_group_id),
            }
            
            request.session['pre_receipt'] = {
                'items': [
                    {'name': item['product_name'], 'quantity': item['quantity'],
                     'price': item['price'], 'total': item['total'], 'unit': item['unit']}
                    for item in sale_items
                ],
                'subtotal': round(float(total_without_discount), 2),
                'discount': float(discount_amount),
                'total': float(total_with_discount),
                'coupon_code': coupon.code if coupon else None,
                'date': timezone.now().strftime('%d.%m.%Y %H:%M'),
                'cashier': request.user.username,
            }
            request.session.modified = True
            
            messages.info(request, f'Сумма к оплате: {total_with_discount:.2f} ₽')
            return redirect('payment_page')
            
        except Exception as e:
            messages.error(request, f'Ошибка: {str(e)}')
            return redirect('sale')
    
    # GET часть - отображение страницы продажи
    # Получаем активные прайс-листы
    active_price_lists = PriceList.objects.filter(is_active=True)
    
    # Получаем выбранный прайс-лист из сессии
    selected_price_list_id = request.session.get('selected_price_list')
    selected_price_list = None
    available_products = []
    
    if selected_price_list_id:
        try:
            selected_price_list = PriceList.objects.get(pk=selected_price_list_id, is_active=True)
            
            # Получаем все товары из выбранного прайс-листа
            price_list_items = selected_price_list.items.select_related('product', 'product__category').all()
            
            # Получаем все товары на складе пользователя
            store_products = {sp.product_id: sp.quantity for sp in StoreProduct.objects.filter(store=user_store)}
            
            for item in price_list_items:
                product = item.product
                
                # Проверяем наличие на складе
                quantity = store_products.get(product.id, 0)
                
                # Проверяем наличие и срок годности
                if quantity > 0 and product.expiration_date >= date.today():
                    product.display_price = item.get_price()
                    product.current_quantity = quantity
                    available_products.append(product)
                    
        except PriceList.DoesNotExist:
            selected_price_list = None
    
    # Группируем товары по категориям
    categories_with_products = {}
    for product in available_products:
        cat = product.category
        if cat not in categories_with_products:
            categories_with_products[cat] = []
        categories_with_products[cat].append(product)
    
    # Получаем состояние свёрнутых категорий из сессии
    collapsed_categories = request.session.get('collapsed_categories', [])
    
    # Получаем товары в корзине
    cart = request.session.get('cart', {})
    cart_items = []
    total = Decimal('0.00')
    
    for product_id, item in cart.items():
        try:
            product = Product.objects.get(pk=product_id)
            quantity = Decimal(str(item['quantity']).replace(',', '.'))
            subtotal = Decimal(str(item['price'])) * quantity
            total += subtotal
            
            cart_items.append({
                'product': product,
                'quantity': float(quantity) if product.unit == 'kg' else int(quantity),
                'subtotal': float(subtotal)
            })
        except Product.DoesNotExist:
            continue
    
    total = total.quantize(Decimal('0.01'))
    
    # Получаем доступные купоны
    now = timezone.now()
    available_coupons = Coupon.objects.filter(is_active=True).filter(
        Q(valid_from__isnull=True) | Q(valid_from__lte=now)
    ).filter(
        Q(valid_until__isnull=True) | Q(valid_until__gte=now)
    ).filter(
        Q(max_uses__isnull=True) | Q(used_count__lt=F('max_uses'))
    )
    
    # Проверяем примененный купон
    applied_coupon_id = request.session.get('applied_coupon')
    applied_coupon = None
    discount_amount = Decimal('0.00')
    total_with_discount = total
    
    if applied_coupon_id:
        try:
            applied_coupon = Coupon.objects.get(pk=applied_coupon_id)
            if applied_coupon.is_valid():
                total_with_discount = applied_coupon.apply_discount(total).quantize(Decimal('0.01'))
                discount_amount = (total - total_with_discount).quantize(Decimal('0.01'))
            else:
                del request.session['applied_coupon']
                request.session.modified = True
        except Coupon.DoesNotExist:
            del request.session['applied_coupon']
            request.session.modified = True
    
    context = {
        'categories': categories_with_products,
        'collapsed_categories': [str(cat_id) for cat_id in collapsed_categories],
        'cart_items': cart_items,
        'total': float(total),
        'total_with_discount': float(total_with_discount),
        'discount_amount': float(discount_amount),
        'available_coupons': available_coupons,
        'applied_coupon': applied_coupon,
        'active_price_lists': active_price_lists,
        'selected_price_list': selected_price_list,
        'user_store': user_store,
        'is_cashier': is_cashier(request.user),
    }
    return render(request, 'cash_app/sale.html', context)


@login_required
def add_to_cart(request):
    """Добавление товара в корзину (поддержка составных блюд)"""
    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        product = get_object_or_404(Product, pk=product_id)

        try:
            quantity = parse_decimal_quantity(request.POST.get('quantity', 1))
            validate_quantity_for_product(product, quantity)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('sale')

        # Для составных блюд проверяем наличие ингредиентов
        if product.is_composite:
            try:
                recipe = Recipe.objects.get(product=product)
                required_ingredients = recipe.get_required_ingredients()
                
                for ing_id, ing_data in required_ingredients.items():
                    required_qty = ing_data['quantity'] * float(quantity)
                    
                    # Получаем склад пользователя
                    user_store = get_user_store(request.user)
                    store_product = StoreProduct.objects.filter(store=user_store, product_id=ing_id).first()
                    available_qty = store_product.quantity if store_product else 0
                    
                    if available_qty < required_qty:
                        messages.error(request, f'Недостаточно ингредиента "{ing_data["product"].name}" для приготовления "{product.name}". Требуется: {required_qty} {ing_data["unit"]}')
                        return redirect('sale')
            except Recipe.DoesNotExist:
                messages.error(request, f'Для блюда "{product.name}" не настроен рецепт')
                return redirect('sale')
        else:
            # Обычный товар - проверяем наличие на складе
            user_store = get_user_store(request.user)
            store_product = StoreProduct.objects.filter(store=user_store, product=product).first()
            available_qty = store_product.quantity if store_product else 0
            
            if available_qty < quantity:
                messages.error(request, f'Недостаточно товара "{product.name}" на складе')
                return redirect('sale')
        
        # Проверка на просрочку
        if product.expiration_date < date.today():
            messages.error(request, f'Товар "{product.name}" просрочен и не может быть продан')
            return redirect('sale')

        # Получаем текущую корзину
        cart = request.session.get('cart', {})
        product_id_str = str(product.id)

        # Получаем текущее количество товара в корзине
        current_quantity = Decimal('0.00')
        if product_id_str in cart:
            current_quantity = parse_decimal_quantity(cart[product_id_str]['quantity'])
        
        new_quantity = current_quantity + quantity
        cart[product_id_str] = {
            'quantity': float(new_quantity) if product.unit == 'kg' else int(new_quantity),
            'price': str(product.display_price if hasattr(product, 'display_price') else product.price),
            'unit': product.unit,
            'is_composite': product.is_composite,
        }

        request.session['cart'] = cart
        request.session.modified = True

        # Форматируем количество для отображения
        if product.unit == 'kg':
            qty_display = f"{float(quantity):.3f}"
        else:
            qty_display = f"{int(quantity)}"
        
        messages.success(request, f'Товар "{product.name}" добавлен в корзину ({qty_display} {product.get_unit_display()})')

    return redirect('sale')


@login_required
def remove_from_cart(request, product_id):
    """Удаление товара из корзины"""
    cart = request.session.get('cart', {})
    
    if str(product_id) in cart:
        del cart[str(product_id)]
        request.session['cart'] = cart
        request.session.modified = True
        messages.success(request, 'Товар удален из корзины')
    
    return redirect('sale')


@login_required
def clear_cart(request):
    """Очистка корзины"""
    request.session['cart'] = {}
    if 'applied_coupon' in request.session:
        del request.session['applied_coupon']
    request.session.modified = True
    messages.success(request, 'Корзина очищена')
    return redirect('sale')


@login_required
def receipt_view(request):
    """Просмотр чека"""
    receipt = request.session.get('last_receipt')
    if not receipt:
        messages.warning(request, 'Нет данных для отображения чека')
        return redirect('sale')
    
    return render(request, 'cash_app/receipt.html', {'receipt': receipt})


@login_required
def receipt_pdf(request):
    """Генерация PDF чека"""
    receipt = request.session.get('last_receipt')
    if not receipt:
        messages.warning(request, 'Нет данных для генерации чека')
        return redirect('sale')

    buffer = BytesIO()
    
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=40,
        leftMargin=40,
        topMargin=40,
        bottomMargin=40,
    )
    
    # Используем встроенные шрифты reportlab
    try:
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        import platform
        import os
        
        font_paths = []
        
        if platform.system() == 'Windows':
            font_paths = [
                'C:/Windows/Fonts/arial.ttf',
                'C:/Windows/Fonts/times.ttf',
                'C:/Windows/Fonts/calibri.ttf',
            ]
        elif platform.system() == 'Linux':
            font_paths = [
                '/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf',
                '/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf',
                '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
            ]
        elif platform.system() == 'Darwin':
            font_paths = [
                '/System/Library/Fonts/Arial.ttf',
                '/System/Library/Fonts/Times.ttf',
            ]
        
        regular_font = 'Helvetica'
        bold_font = 'Helvetica-Bold'
        
        for font_path in font_paths:
            if os.path.exists(font_path):
                try:
                    pdfmetrics.registerFont(TTFont('CustomFont', font_path))
                    regular_font = 'CustomFont'
                    bold_font = 'CustomFont'
                    break
                except:
                    continue
                    
    except:
        regular_font = 'Helvetica'
        bold_font = 'Helvetica-Bold'
    
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontName=bold_font,
        fontSize=18,
        alignment=1,
        spaceAfter=20,
        textColor=colors.HexColor('#2c3e50')
    )
    
    info_style = ParagraphStyle(
        'Info',
        parent=styles['Normal'],
        fontName=regular_font,
        fontSize=10,
        leading=14,
        spaceAfter=4,
        alignment=0,
    )
    
    table_text_style = ParagraphStyle(
        'TableText',
        parent=styles['Normal'],
        fontName=regular_font,
        fontSize=9,
        leading=12
    )
    
    table_header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontName=bold_font,
        fontSize=9,
        leading=12,
        alignment=1
    )
    
    story = []
    
    story.append(Paragraph("КАССОВЫЙ ЧЕК", title_style))
    story.append(Spacer(1, 10))
    
    story.append(Paragraph(f"Дата: {receipt['date']}", info_style))
    story.append(Paragraph(f"Кассир: {receipt['cashier']}", info_style))
    
    if receipt.get('coupon_code'):
        story.append(Paragraph(f"Купон: {receipt['coupon_code']}", info_style))
    
    if receipt.get('payment_info'):
        payment_id = receipt['payment_info'].get('payment_id') or receipt.get('sale_group') or '—'
        story.append(Paragraph(f"ID платежа: {payment_id}", info_style))
    
    story.append(Spacer(1, 15))
    
    data = [
        [
            Paragraph("№", table_header_style),
            Paragraph("Товар", table_header_style),
            Paragraph("Кол-во", table_header_style),
            Paragraph("Цена", table_header_style),
            Paragraph("Сумма", table_header_style),
        ]
    ]
    
    for i, item in enumerate(receipt['items'], 1):
        data.append([
            Paragraph(str(i), table_text_style),
            Paragraph(str(item['name']), table_text_style),
            Paragraph(f"{item['quantity']} {item['unit']}", table_text_style),
            Paragraph(f"{item['price']:.2f} ₽", table_text_style),
            Paragraph(f"{item['total']:.2f} ₽", table_text_style),
        ])
    
    data.append([
        Paragraph("", table_text_style),
        Paragraph("", table_text_style),
        Paragraph("", table_text_style),
        Paragraph("<b>ПОДЫТОГ:</b>", table_text_style),
        Paragraph(f"<b>{receipt['subtotal']:.2f} ₽</b>", table_text_style),
    ])
    
    if receipt.get('discount', 0) > 0:
        data.append([
            Paragraph("", table_text_style),
            Paragraph("", table_text_style),
            Paragraph("", table_text_style),
            Paragraph("<b>СКИДКА:</b>", table_text_style),
            Paragraph(f"<b>-{receipt['discount']:.2f} ₽</b>", table_text_style),
        ])
    
    data.append([
        Paragraph("", table_text_style),
        Paragraph("", table_text_style),
        Paragraph("", table_text_style),
        Paragraph("<b>ИТОГО:</b>", table_text_style),
        Paragraph(f"<b>{receipt['total']:.2f} ₽</b>", table_text_style),
    ])
    
    table = Table(data, colWidths=[30, 220, 70, 70, 80])
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), regular_font),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e9ecef')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.HexColor('#2c3e50')),
        ('ALIGN', (0, 0), (-1, 0), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), bold_font),
        ('GRID', (0, 0), (-1, -4), 0.5, colors.HexColor('#dee2e6')),
        ('GRID', (0, -3), (-1, -1), 0.5, colors.HexColor('#dee2e6')),
        ('ALIGN', (0, 1), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (4, -1), 'RIGHT'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 8),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f8f9fa')),
        ('FONTNAME', (0, -1), (-1, -1), bold_font),
    ]))
    
    story.append(table)
    story.append(Spacer(1, 20))
    
    thanks_style = ParagraphStyle(
        'Thanks',
        parent=styles['Normal'],
        fontName=regular_font,
        fontSize=10,
        alignment=1,
        spaceAfter=5
    )
    
    story.append(Paragraph("Спасибо за покупку!", thanks_style))
    story.append(Paragraph("Чек является фискальным документом", thanks_style))
    
    story.append(Spacer(1, 15))
    
    footer_style = ParagraphStyle(
        'Footer',
        parent=styles['Normal'],
        fontName=regular_font,
        fontSize=8,
        alignment=1,
        textColor=colors.HexColor('#6c757d')
    )
    
    story.append(Paragraph("Кассовая система", footer_style))
    story.append(Paragraph(f"Чек сформирован: {timezone.now().strftime('%d.%m.%Y %H:%M')}", footer_style))
    
    doc.build(story)
    
    pdf = buffer.getvalue()
    buffer.close()
    
    filename = f"receipt_{receipt['date'].replace(' ', '_').replace(':', '-')}.pdf"
    
    response = HttpResponse(pdf, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    
    return response


@login_required
def dashboard_view(request):
    """Дашборд эффективности с улучшенным отображением"""
    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_year = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Для обычного пользователя - только свои данные
    if not is_admin(request.user):
        user_store = get_user_store(request.user)
        if not user_store:
            return render(request, 'cash_app/no_store.html')
        
        try:
            plan = SalesPlan.objects.get(user=request.user)
        except SalesPlan.DoesNotExist:
            plan = SalesPlan.objects.create(user=request.user, monthly_target=0)
        
        # Продажи пользователя за текущий месяц
        user_sales = History.objects.filter(
            type='sale',
            user=request.user,
            date__gte=start_of_month
        )
        monthly_sales = sum(float(s.total_price or 0) for s in user_sales if s.total_price is not None)
        
        unique_sales = user_sales.values('sale_group').distinct().count()
        if unique_sales > 0:
            average_check = monthly_sales / unique_sales
        else:
            average_check = 0
        
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_sales = History.objects.filter(
            type='sale',
            user=request.user,
            date__gte=today_start
        )
        today_total = sum(float(s.total_price or 0) for s in today_sales if s.total_price is not None)
        
        week_start = now - timedelta(days=7)
        week_sales = History.objects.filter(
            type='sale',
            user=request.user,
            date__gte=week_start
        )
        week_total = sum(float(s.total_price or 0) for s in week_sales if s.total_price is not None)
        
        # Расчет зарплаты
        try:
            salary_settings = SalarySettings.objects.get(user=request.user)
        except SalarySettings.DoesNotExist:
            salary_settings = None
        
        # Расчет комиссии
        commission = 0
        bonus = 0
        total_salary = 0
        plan_completion = 0
        
        if salary_settings:
            # Комиссия от продаж
            commission = monthly_sales * (float(salary_settings.commission_percent or 0) / 100)
            
            # Выполнение плана
            if plan.monthly_target and plan.monthly_target > 0:
                plan_completion = (monthly_sales / float(plan.monthly_target)) * 100
            else:
                plan_completion = 0
            
            # Премия (если выполнены условия)
            if plan_completion >= float(salary_settings.plan_completion_threshold or 0):
                bonus = float(salary_settings.base_salary or 0) * (float(salary_settings.bonus_percent or 0) / 100)
            
            # Итого зарплата
            total_salary = float(salary_settings.base_salary or 0) + commission + bonus
        
        # Лучший день в месяце
        best_day = 0
        best_day_amount = 0
        if user_sales.exists():
            day_sales = {}
            for sale in user_sales:
                if sale.total_price is not None:
                    day = sale.date.day
                    day_sales[day] = day_sales.get(day, 0) + float(sale.total_price)
            for day, amount in day_sales.items():
                if amount > best_day_amount:
                    best_day_amount = amount
                    best_day = day
        
        # Статистика по дням для графика
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        daily_data = []
        labels = []
        
        for day in range(1, days_in_month + 1):
            day_date = datetime(now.year, now.month, day).date()
            day_start = timezone.make_aware(datetime.combine(day_date, datetime.min.time()))
            day_end = timezone.make_aware(datetime.combine(day_date, datetime.max.time()))
            
            day_sales = History.objects.filter(
                type='sale',
                user=request.user,
                date__range=[day_start, day_end]
            )
            day_total = sum(float(s.total_price or 0) for s in day_sales if s.total_price is not None)
            
            daily_data.append(round(day_total, 2))
            labels.append(day)
        
        # Топ-5 товаров пользователя за месяц
        top_products = History.objects.filter(
            type='sale',
            user=request.user,
            date__gte=start_of_month
        ).values(
            'product__name',
            'product__unit'
        ).annotate(
            total=Sum('total_price'),
            quantity=Sum('quantity')
        ).order_by('-total')[:5]
        
        top_products_list = []
        for p in top_products:
            top_products_list.append({
                'name': p['product__name'] or 'Товар',
                'unit': p['product__unit'],
                'total': float(p['total']) if p['total'] else 0,
                'quantity': float(p['quantity']) if p['quantity'] else 0
            })
        
        context = {
            'plan': plan,
            'monthly_sales': round(monthly_sales, 2),
            'completion_percentage': plan.get_completion_percentage(),
            'remaining_amount': round(plan.get_remaining_amount(), 2),
            'daily_average': round(plan.get_daily_average(), 2),
            'today_total': round(today_total, 2),
            'week_total': round(week_total, 2),
            'average_check': round(average_check, 2),
            'sales_count': unique_sales,
            'daily_data': json.dumps(daily_data),
            'labels': json.dumps(labels),
            'has_sales': any(d > 0 for d in daily_data),
            'is_admin': False,
            'user_store': user_store,
            'best_day': best_day,
            'best_day_amount': round(best_day_amount, 2),
            'top_products': top_products_list,
            # Данные по зарплате
            'salary_settings': salary_settings,
            'commission': round(commission, 2),
            'bonus': round(bonus, 2),
            'total_salary': round(total_salary, 2),
            'plan_completion': round(plan_completion, 1),
        }
        return render(request, 'cash_app/dashboard_user.html', context)
    
    # Для администратора - общая статистика
    else:
        # Получаем выбранный период из GET параметра
        period = request.GET.get('period', 'current')
        custom_month = request.GET.get('month')
        custom_year = request.GET.get('year')
        
        # Определяем даты для периода
        if period == 'previous':
            if now.month == 1:
                start_of_month = now.replace(year=now.year-1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
            else:
                start_of_month = now.replace(month=now.month-1, day=1, hour=0, minute=0, second=0, microsecond=0)
        elif period == 'custom' and custom_month and custom_year:
            start_of_month = datetime(int(custom_year), int(custom_month), 1)
            start_of_month = timezone.make_aware(start_of_month)
        else:
            start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        
        # Конец месяца
        if start_of_month.month == 12:
            next_month = start_of_month.replace(year=start_of_month.year+1, month=1, day=1)
        else:
            next_month = start_of_month.replace(month=start_of_month.month+1, day=1)
        end_of_month = next_month - timedelta(days=1)
        
        # Получаем выбранный склад из GET параметра
        selected_store_id = request.GET.get('store')
        selected_store = None
        if selected_store_id:
            try:
                selected_store = StoreAddress.objects.get(pk=selected_store_id, is_active=True)
            except StoreAddress.DoesNotExist:
                selected_store = None
        
        # Базовые фильтры
        store_filter = {}
        if selected_store:
            store_filter = {'store': selected_store}
        
        # Общая выручка за выбранный период
        total_monthly_sales = History.objects.filter(
            type='sale',
            date__gte=start_of_month,
            date__lte=end_of_month,
            **store_filter
        ).aggregate(total=Sum('total_price'))['total'] or 0
        
        # Общая выручка за год
        total_yearly_sales = History.objects.filter(
            type='sale',
            date__gte=start_of_year,
            **store_filter
        ).aggregate(total=Sum('total_price'))['total'] or 0
        
        # Количество уникальных продаж (чеков) за выбранный период
        sales_count = History.objects.filter(
            type='sale',
            date__gte=start_of_month,
            date__lte=end_of_month,
            **store_filter
        ).values('sale_group').distinct().count()
        
        # Средний чек
        if sales_count > 0:
            average_check = float(total_monthly_sales) / sales_count
        else:
            average_check = 0
        
        # Статистика по планам
        users_with_plans = SalesPlan.objects.all().select_related('user', 'updated_by')
        
        total_plans = users_with_plans.count()
        half_completed = 0
        over_completed = 0
        
        for p in users_with_plans:
            percentage = p.get_completion_percentage()
            if percentage >= 100:
                over_completed += 1
                half_completed += 1
            elif percentage >= 50:
                half_completed += 1
        
        # Продажи по дням для графика
        days_in_month = calendar.monthrange(start_of_month.year, start_of_month.month)[1]
        daily_data = []
        labels = []
        
        for day in range(1, days_in_month + 1):
            day_date = datetime(start_of_month.year, start_of_month.month, day).date()
            day_start = timezone.make_aware(datetime.combine(day_date, datetime.min.time()))
            day_end = timezone.make_aware(datetime.combine(day_date, datetime.max.time()))
            
            day_sales = History.objects.filter(
                type='sale',
                date__range=[day_start, day_end],
                **store_filter
            )
            day_total = sum(float(s.total_price or 0) for s in day_sales if s.total_price is not None)
            
            daily_data.append(round(day_total, 2))
            labels.append(day)
        
        # Расчет динамики
        if start_of_month.month == 1:
            prev_month_start = start_of_month.replace(year=start_of_month.year-1, month=12, day=1)
        else:
            prev_month_start = start_of_month.replace(month=start_of_month.month-1, day=1)
        
        if prev_month_start.month == 12:
            prev_month_end = prev_month_start.replace(year=prev_month_start.year+1, month=1, day=1) - timedelta(days=1)
        else:
            prev_month_end = prev_month_start.replace(month=prev_month_start.month+1, day=1) - timedelta(days=1)
        
        prev_month_sales = History.objects.filter(
            type='sale',
            date__gte=prev_month_start,
            date__lte=prev_month_end,
            **store_filter
        ).aggregate(total=Sum('total_price'))['total'] or 0
        
        if prev_month_sales > 0:
            growth_percent = ((float(total_monthly_sales) - float(prev_month_sales)) / float(prev_month_sales)) * 100
        else:
            growth_percent = 0 if total_monthly_sales == 0 else 100
        
        # Топ-5 товаров
        top_products = History.objects.filter(
            type='sale',
            date__gte=start_of_month,
            date__lte=end_of_month,
            **store_filter
        ).values(
            'product__name',
            'product__unit'
        ).annotate(
            total=Sum('total_price'),
            quantity=Sum('quantity')
        ).order_by('-total')[:5]
        
        top_products_list = []
        for p in top_products:
            top_products_list.append({
                'name': p['product__name'] or 'Товар',
                'unit': p['product__unit'],
                'total': float(p['total']) if p['total'] else 0,
                'quantity': float(p['quantity']) if p['quantity'] else 0
            })
        
        # Статистика по складам
        stores = StoreAddress.objects.filter(is_active=True)
        store_stats = []
        for store in stores:
            store_sales = History.objects.filter(
                type='sale',
                store=store,
                date__gte=start_of_month,
                date__lte=end_of_month
            ).aggregate(total=Sum('total_price'))['total'] or 0
            
            store_sales_count = History.objects.filter(
                type='sale',
                store=store,
                date__gte=start_of_month,
                date__lte=end_of_month
            ).values('sale_group').distinct().count()
            
            store_stats.append({
                'store': store,
                'sales': float(store_sales),
                'sales_count': store_sales_count,
                'average_check': float(store_sales) / store_sales_count if store_sales_count > 0 else 0,
                'users_count': UserProfile.objects.filter(store=store).count()
            })
        
        # Топ продавцов
        top_sellers = []
        for user in User.objects.filter(is_staff=False, is_active=True):
            user_sales = History.objects.filter(
                type='sale',
                user=user,
                date__gte=start_of_month,
                date__lte=end_of_month
            ).aggregate(total=Sum('total_price'))['total'] or 0
            
            try:
                plan = SalesPlan.objects.get(user=user)
                plan_amount = plan.monthly_target
                completion = plan.get_completion_percentage()
            except SalesPlan.DoesNotExist:
                plan_amount = 0
                completion = 0
            
            if user_sales > 0:
                top_sellers.append({
                    'user': user,
                    'sales': float(user_sales),
                    'plan': float(plan_amount),
                    'completion': completion
                })
        
        top_sellers = sorted(top_sellers, key=lambda x: x['sales'], reverse=True)[:5]
        
        # Расчет зарплат сотрудников
        salary_summary = []
        total_salary_amount = 0
        
        for user in User.objects.filter(is_staff=False, is_active=True):
            try:
                salary_settings = SalarySettings.objects.get(user=user)
            except SalarySettings.DoesNotExist:
                continue
            
            try:
                plan = SalesPlan.objects.get(user=user)
                plan_target = float(plan.monthly_target) if plan.monthly_target else 0
            except SalesPlan.DoesNotExist:
                plan_target = 0
            
            # Продажи пользователя за месяц
            user_sales_total = History.objects.filter(
                type='sale',
                user=user,
                date__gte=start_of_month,
                date__lte=end_of_month
            ).aggregate(total=Sum('total_price'))['total'] or 0
            
            # Выполнение плана
            if plan_target > 0:
                plan_completion = (float(user_sales_total) / plan_target) * 100
            else:
                plan_completion = 0
            
            # Расчет комиссии
            commission = float(user_sales_total) * (float(salary_settings.commission_percent or 0) / 100)
            
            # Премия
            bonus = 0
            if plan_completion >= float(salary_settings.plan_completion_threshold or 0):
                bonus = float(salary_settings.base_salary or 0) * (float(salary_settings.bonus_percent or 0) / 100)
            
            # Итого
            total_salary = float(salary_settings.base_salary or 0) + commission + bonus
            total_salary_amount += total_salary
            
            salary_summary.append({
                'user': user,
                'base_salary': float(salary_settings.base_salary or 0),
                'commission_percent': float(salary_settings.commission_percent or 0),
                'bonus_percent': float(salary_settings.bonus_percent or 0),
                'plan_completion_threshold': float(salary_settings.plan_completion_threshold or 0),
                'sales': float(user_sales_total),
                'plan_completion': plan_completion,
                'commission': commission,
                'bonus': bonus,
                'total': total_salary,
            })
        
        # Сортируем по итоговой зарплате
        salary_summary = sorted(salary_summary, key=lambda x: x['total'], reverse=True)
        
        month_names = {
            1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
            5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
            9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
        }
        current_month_name = month_names.get(start_of_month.month, '')
        
        context = {
            'total_monthly_sales': round(float(total_monthly_sales), 2),
            'total_yearly_sales': round(float(total_yearly_sales), 2),
            'sales_count': sales_count,
            'average_check': round(average_check, 2),
            'users_with_plans': users_with_plans,
            'total_plans': total_plans,
            'half_completed': half_completed,
            'over_completed': over_completed,
            'daily_data': json.dumps(daily_data),
            'labels': json.dumps(labels),
            'top_products': top_products_list,
            'has_sales': any(d > 0 for d in daily_data),
            'is_admin': True,
            'stores': stores,
            'store_stats': store_stats,
            'selected_store': selected_store,
            'top_sellers': top_sellers,
            'period': period,
            'current_month': start_of_month.month,
            'current_year': start_of_month.year,
            'current_month_name': current_month_name,
            'growth_percent': round(growth_percent, 1),
            'prev_month_sales': round(float(prev_month_sales), 2),
            'salary_summary': salary_summary,
            'total_salary_amount': round(total_salary_amount, 2),
        }
        
        return render(request, 'cash_app/dashboard_admin.html', context)


@login_required
@user_passes_test(is_admin)
def plan_edit(request, user_id):
    """Редактирование плана пользователя (только для админа)"""
    user = get_object_or_404(User, pk=user_id)
    plan, created = SalesPlan.objects.get_or_create(user=user)
    
    if request.method == 'POST':
        form = SalesPlanForm(request.POST, instance=plan)
        if form.is_valid():
            plan = form.save(commit=False)
            plan.updated_by = request.user
            plan.save()
            messages.success(request, f'План для пользователя {user.username} успешно обновлен')
            return redirect('dashboard')
    else:
        form = SalesPlanForm(instance=plan)
    
    context = {
        'form': form,
        'target_user': user,
        'plan': plan,
    }
    return render(request, 'cash_app/plan_edit.html', context)


@login_required
def user_sales_detail(request, user_id):
    """Детальная информация о продажах пользователя (только для админа)"""
    if not is_admin(request.user):
        messages.error(request, 'У вас нет прав для просмотра этой страницы')
        return redirect('dashboard')
    
    target_user = get_object_or_404(User, pk=user_id)
    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Продажи пользователя за месяц
    sales = History.objects.filter(
        type='sale',
        user=target_user,
        date__gte=start_of_month
    ).select_related('product', 'coupon').order_by('-date')
    
    total = sum(float(s.total_price or 0) for s in sales)
    
    try:
        plan = SalesPlan.objects.get(user=target_user)
    except SalesPlan.DoesNotExist:
        plan = None
    
    context = {
        'target_user': target_user,
        'sales': sales,
        'total': total,
        'plan': plan,
    }
    return render(request, 'cash_app/user_sales_detail.html', context)


# Функции для управления купонами (только для администратора)
@login_required
@user_passes_test(is_admin)
def coupon_list(request):
    """Список купонов"""
    coupons = Coupon.objects.all().order_by('-created_at')
    return render(request, 'cash_app/coupon_list.html', {'coupons': coupons})


@login_required
@user_passes_test(is_admin)
def coupon_create(request):
    """Создание купона"""
    if request.method == 'POST':
        form = CouponForm(request.POST)
        if form.is_valid():
            coupon = form.save(commit=False)
            coupon.created_by = request.user
            coupon.save()
            messages.success(request, 'Купон успешно создан')
            return redirect('coupon_list')
    else:
        form = CouponForm()
    return render(request, 'cash_app/coupon_form.html', {'form': form, 'title': 'Создание купона'})


@login_required
@user_passes_test(is_admin)
def coupon_edit(request, pk):
    """Редактирование купона"""
    coupon = get_object_or_404(Coupon, pk=pk)
    if request.method == 'POST':
        form = CouponForm(request.POST, instance=coupon)
        if form.is_valid():
            form.save()
            messages.success(request, 'Купон обновлен')
            return redirect('coupon_list')
    else:
        form = CouponForm(instance=coupon)
    return render(request, 'cash_app/coupon_form.html', {'form': form, 'title': 'Редактирование купона'})


@login_required
@user_passes_test(is_admin)
def coupon_delete(request, pk):
    """Удаление купона"""
    coupon = get_object_or_404(Coupon, pk=pk)
    if request.method == 'POST':
        coupon.delete()
        messages.success(request, 'Купон удален')
        return redirect('coupon_list')
    return render(request, 'cash_app/coupon_confirm_delete.html', {'coupon': coupon})


@login_required
def apply_coupon(request):
    """Применение купона к корзине"""
    if request.method == 'POST':
        coupon_id = request.POST.get('coupon_id')
        if coupon_id:
            try:
                coupon = Coupon.objects.get(pk=coupon_id)
                if coupon.is_valid():
                    request.session['applied_coupon'] = coupon_id
                    messages.success(request, f'Купон {coupon.code} применён. Скидка {coupon.discount_percent}%')
                else:
                    messages.error(request, 'Купон недействителен')
            except Coupon.DoesNotExist:
                messages.error(request, 'Купон не найден')
        else:
            messages.error(request, 'Выберите купон')
    return redirect('sale')


@login_required
def remove_coupon(request):
    """Удаление купона из корзины"""
    if 'applied_coupon' in request.session:
        del request.session['applied_coupon']
        request.session.modified = True
        messages.success(request, 'Купон убран')
    return redirect('sale')


@login_required
@user_passes_test(is_admin)
def category_list(request):
    """Список категорий"""
    categories = Category.objects.all().order_by('name')
    return render(request, 'cash_app/category_list.html', {'categories': categories})


@login_required
@user_passes_test(is_admin)
def category_create(request):
    """Создание категории"""
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        
        if name:
            Category.objects.create(name=name, description=description)
            messages.success(request, f'Категория "{name}" успешно создана')
        else:
            messages.error(request, 'Название категории обязательно')
        
        return redirect('category_list')
    
    return render(request, 'cash_app/category_form.html', {'title': 'Создание категории'})


@login_required
@user_passes_test(is_admin)
def category_edit(request, pk):
    """Редактирование категории"""
    category = get_object_or_404(Category, pk=pk)
    
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        
        if name:
            category.name = name
            category.description = description
            category.save()
            messages.success(request, f'Категория "{name}" успешно обновлена')
        else:
            messages.error(request, 'Название категории обязательно')
        
        return redirect('category_list')
    
    context = {
        'title': 'Редактирование категории',
        'category': category
    }
    return render(request, 'cash_app/category_form.html', context)


@login_required
@user_passes_test(is_admin)
def category_delete(request, pk):
    """Удаление категории"""
    category = get_object_or_404(Category, pk=pk)
    
    products_count = Product.objects.filter(category=category).count()
    
    if request.method == 'POST':
        if products_count > 0:
            messages.error(request, f'Нельзя удалить категорию, в которой есть товары ({products_count} шт.)')
        else:
            category.delete()
            messages.success(request, f'Категория "{category.name}" удалена')
        
        return redirect('category_list')
    
    context = {
        'category': category,
        'products_count': products_count
    }
    return render(request, 'cash_app/category_confirm_delete.html', context)


@login_required
def save_collapsed_categories(request):
    """Сохранение состояния свёрнутых категорий"""
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            collapsed = data.get('collapsed', [])
            request.session['collapsed_categories'] = collapsed
            request.session.modified = True
            return JsonResponse({'status': 'ok'})
        except:
            return JsonResponse({'status': 'error'}, status=400)
    return JsonResponse({'status': 'error'}, status=405)


@login_required
def scan_qr(request):
    """Страница сканирования QR-кода"""
    action = request.GET.get('action', 'add_to_cart')
    
    scanned_product_id = request.session.get('scanned_product_id')
    scan_action = request.session.get('scan_action')
    
    if scanned_product_id and scan_action:
        try:
            product = Product.objects.get(pk=scanned_product_id)
            del request.session['scanned_product_id']
            del request.session['scan_action']
            return render(request, 'cash_app/scan_qr.html', {
                'scanned_product': product,
                'scan_action': scan_action
            })
        except:
            pass
    
    return render(request, 'cash_app/scan_qr.html', {
        'scan_action': action,
        'scanned_product': None
    })


@login_required
def scan_qr_result(request):
    """Обработка результата сканирования QR-кода"""
    qr_data = request.GET.get('data', '')
    action = request.GET.get('action', 'add_to_cart')
    
    if not qr_data:
        messages.error(request, 'Не удалось прочитать QR-код')
        return redirect('scan_qr')
    
    try:
        if qr_data.startswith('product:'):
            qr_uuid = qr_data.replace('product:', '')
            product = Product.objects.get(qr_uuid=qr_uuid)
            
            request.session['scanned_product_id'] = product.id
            request.session['scan_action'] = action
            
            return render(request, 'cash_app/scan_qr.html', {
                'scanned_product': product,
                'scan_action': action
            })
        else:
            messages.error(request, 'Неверный формат QR-кода')
    except Product.DoesNotExist:
        messages.error(request, 'Товар не найден в системе. Возможно, QR-код устарел.')
    except Exception as e:
        messages.error(request, f'Ошибка: {str(e)}')
    
    return redirect('scan_qr')


@login_required
def scan_qr_upload(request):
    """Обработка загруженного изображения с QR-кодом"""
    if request.method == 'POST' and request.FILES.get('qr_image'):
        from pyzbar.pyzbar import decode
        from PIL import Image
        import io
        
        action = request.POST.get('action', 'add_to_cart')
        
        try:
            image_file = request.FILES['qr_image']
            image = Image.open(io.BytesIO(image_file.read()))
            
            decoded_objects = decode(image)
            
            if decoded_objects:
                qr_data = decoded_objects[0].data.decode('utf-8')
                return redirect(f"{reverse('scan_qr_result')}?data={qr_data}&action={action}")
            else:
                messages.error(request, 'На изображении не найден QR-код')
        except Exception as e:
            messages.error(request, f'Ошибка при обработке изображения: {str(e)}')
    
    return redirect('scan_qr')


@login_required
def process_qr_action(request):
    """Обработка действия после сканирования QR-кода"""
    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        quantity_str = request.POST.get('quantity', '1')
        action = request.POST.get('action')

        product = get_object_or_404(Product, pk=product_id)

        try:
            quantity = parse_decimal_quantity(quantity_str)
            validate_quantity_for_product(product, quantity)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('scan_qr')

        if action == 'add_to_cart':
            user_store = get_user_store(request.user)
            store_product = StoreProduct.objects.filter(store=user_store, product=product).first()
            available_quantity = store_product.quantity if store_product else 0

            if available_quantity < quantity:
                messages.error(request, f'Недостаточно товара "{product.name}" на складе')
                return redirect('sale')

            if product.expiration_date < date.today():
                messages.error(request, f'Товар "{product.name}" просрочен и не может быть продан')
                return redirect('sale')

            cart = request.session.get('cart', {})
            product_id_str = str(product.id)

            current_quantity = Decimal('0.00')
            if product_id_str in cart:
                try:
                    current_quantity = parse_decimal_quantity(cart[product_id_str]['quantity'])
                except (KeyError, ValueError):
                    current_quantity = Decimal('0.00')
            
            new_quantity = current_quantity + quantity

            if new_quantity > available_quantity:
                messages.error(request, f'Нельзя добавить больше, чем есть на складе (доступно: {available_quantity} {product.get_unit_display()})')
                return redirect('sale')
            
            cart[product_id_str] = {
                'quantity': float(new_quantity) if product.unit == 'kg' else int(new_quantity),
                'price': str(product.display_price if hasattr(product, 'display_price') else product.price),
                'unit': product.unit,
            }
            
            request.session['cart'] = cart
            request.session.modified = True
            
            if product.unit == 'kg':
                qty_display = f"{float(quantity):.3f}"
            else:
                qty_display = f"{int(quantity)}"
            
            messages.success(request, f'Товар "{product.name}" добавлен в корзину ({qty_display} {product.get_unit_display()})')
            return redirect('sale')

        elif action == 'receipt':
            product.quantity += quantity
            product.save()

            History.objects.create(
                type='receipt',
                product=product,
                quantity=quantity,
                user=request.user
            )

            if product.unit == 'kg':
                qty_display = f"{float(quantity):.3f}"
            else:
                qty_display = f"{int(quantity)}"
            
            messages.success(request, f'Поступление товара "{product.name}" оформлено. Добавлено {qty_display} {product.get_unit_display()}')
            return redirect('product_list')
        
        else:
            messages.error(request, 'Неизвестное действие')
            return redirect('scan_qr')
    
    return redirect('scan_qr')


@login_required
def generate_product_qr(request, product_id):
    """Генерация QR-кода для существующего товара"""
    product = get_object_or_404(Product, pk=product_id)
    
    if not product.qr_code:
        product.generate_qr_code()
        product.save()
        messages.success(request, f'QR-код для товара "{product.name}" сгенерирован')
    else:
        messages.info(request, f'QR-код для товара "{product.name}" уже существует')
    
    return redirect('product_list')


@login_required
def download_product_qr(request, product_id):
    """Скачивание QR-кода товара"""
    product = get_object_or_404(Product, pk=product_id)
    
    if not product.qr_code:
        product.generate_qr_code()
        product.save()
    
    response = FileResponse(product.qr_code, as_attachment=True, filename=f'qr_{product.name}.png')
    return response


@login_required
def print_product_qr(request, product_id):
    """Печать QR-кода товара"""
    product = get_object_or_404(Product, pk=product_id)
    
    if not product.qr_code:
        product.generate_qr_code()
        product.save()
    
    return render(request, 'cash_app/print_qr.html', {'product': product})


@login_required
def payment_page(request):
    """Страница оплаты через терминал"""
    pending_sale = request.session.get('pending_sale')
    pre_receipt = request.session.get('pre_receipt')
    
    if not pending_sale or not pre_receipt:
        messages.warning(request, 'Нет данных для оплаты')
        return redirect('sale')
    
    context = {
        'receipt': pre_receipt,
        'total': pre_receipt['total'],
    }
    return render(request, 'cash_app/payment_page.html', context)


@login_required
def process_payment(request):
    """Обработка оплаты через терминал - после подтверждения оплаты списываем товары"""
    if request.method != 'POST':
        return redirect('sale')

    pending_sale = request.session.get('pending_sale')
    if not pending_sale:
        messages.error(request, 'Нет данных для оплаты')
        return redirect('sale')

    amount = pending_sale.get('total_with_discount', 0)
    sale_group_id = pending_sale.get('sale_group_id', '')
    description = f"Оплата в кассовой системе №{sale_group_id[:8]}"

    if not check_terminal_status():
        messages.error(
            request,
            'Терминал не доступен. Пожалуйста, проверьте подключение и убедитесь, '
            'что Kaspi POS Simulator запущен.'
        )
        return redirect('payment_page')

    result = process_terminal_payment(amount, description)

    if not result.get('success'):
        messages.error(request, f"Ошибка оплаты: {result.get('error', 'Неизвестная ошибка')}")
        return redirect('payment_page')

    try:
        with transaction.atomic():
            sale_items = pending_sale.get('sale_items', [])
            coupon_id = pending_sale.get('coupon_id')
            sale_group_id = pending_sale.get('sale_group_id')

            coupon = None
            if coupon_id:
                coupon = Coupon.objects.filter(pk=coupon_id).first()

            locked_products = {}
            user_store = get_user_store(request.user)
            
            if not user_store:
                raise ValueError('Склад пользователя не найден')

            # Первый проход: проверяем наличие всех товаров и ингредиентов
            for item in sale_items:
                product = Product.objects.select_for_update().get(pk=item['product_id'])
                quantity = parse_decimal_quantity(item['quantity'])
                validate_quantity_for_product(product, quantity)

                if product.expiration_date < date.today():
                    raise ValueError(f'Товар "{product.name}" просрочен и не может быть продан')

                # Для составного блюда - проверяем ингредиенты
                if product.is_composite:
                    try:
                        recipe = Recipe.objects.get(product=product)
                        required_ingredients = recipe.get_required_ingredients()
                        
                        for ing_id, ing_data in required_ingredients.items():
                            required_qty = ing_data['quantity'] * float(quantity)
                            
                            store_product = StoreProduct.objects.select_for_update().filter(
                                store=user_store, 
                                product_id=ing_id
                            ).first()
                            
                            if not store_product or store_product.quantity < required_qty:
                                raise ValueError(
                                    f'Недостаточно ингредиента "{ing_data["product"].name}" '
                                    f'для приготовления "{product.name}". '
                                    f'Требуется: {required_qty} {ing_data["unit"]}'
                                )
                            
                            # Сохраняем информацию о необходимых ингредиентах
                            if product.id not in locked_products:
                                locked_products[product.id] = {
                                    'product': product,
                                    'quantity': quantity,
                                    'total_price': item['total'],  # Сохраняем сумму
                                    'ingredients': []
                                }
                            locked_products[product.id]['ingredients'].append({
                                'store_product': store_product,
                                'required_qty': required_qty,
                                'ingredient': ing_data['product']
                            })
                    except Recipe.DoesNotExist:
                        raise ValueError(f'Для блюда "{product.name}" не настроен рецепт')
                else:
                    # Обычный товар - проверяем наличие на складе
                    store_product = StoreProduct.objects.select_for_update().filter(
                        store=user_store, 
                        product=product
                    ).first()
                    
                    if not store_product or store_product.quantity < quantity:
                        raise ValueError(f'Недостаточно товара "{product.name}" на складе')
                    
                    locked_products[product.id] = {
                        'product': product,
                        'quantity': quantity,
                        'total_price': item['total'],  # Сохраняем сумму
                        'store_product': store_product,
                        'is_composite': False
                    }

        # Второй проход: списываем товары и ингредиенты
        products_for_receipt = []

        for product_id, data in locked_products.items():
            product = data['product']
            quantity = data['quantity']
            total_price = Decimal(str(data['total_price']))  # Получаем сумму
            quantity_decimal = Decimal(str(quantity))  # Преобразуем количество в Decimal
            
            if product.is_composite:
                # Для составного блюда - списываем ингредиенты
                for ing_data in data['ingredients']:
                    ing_data['store_product'].quantity -= Decimal(str(ing_data['required_qty']))
                    ing_data['store_product'].save()
                    
                    # Записываем в историю списание ингредиента
                    History.objects.create(
                        type='sale',
                        product=ing_data['ingredient'],
                        store=user_store,
                        quantity=Decimal(str(ing_data['required_qty'])),
                        total_price=None,
                        user=request.user,
                        coupon=coupon,
                        sale_group=uuid.UUID(sale_group_id)
                    )
                
                # Записываем продажу готового блюда
                History.objects.create(
                    type='sale',
                    product=product,
                    store=user_store,
                    quantity=quantity_decimal,
                    total_price=total_price,
                    user=request.user,
                    coupon=coupon,
                    sale_group=uuid.UUID(sale_group_id)
                )
                
                # Рассчитываем цену за единицу
                price_per_unit = total_price / quantity_decimal if quantity_decimal > 0 else Decimal('0')
                
                products_for_receipt.append({
                    'name': product.name,
                    'quantity': float(quantity) if product.unit == 'kg' else int(quantity),
                    'price': float(price_per_unit),
                    'total': float(total_price),
                    'unit': product.get_unit_display()
                })
            else:
                # Обычный товар - списываем со склада
                data['store_product'].quantity -= quantity_decimal
                data['store_product'].save()
                
                # Записываем продажу
                History.objects.create(
                    type='sale',
                    product=product,
                    store=user_store,
                    quantity=quantity_decimal,
                    total_price=total_price,
                    user=request.user,
                    coupon=coupon,
                    sale_group=uuid.UUID(sale_group_id)
                )
                
                # Рассчитываем цену за единицу
                price_per_unit = total_price / quantity_decimal if quantity_decimal > 0 else Decimal('0')
                
                products_for_receipt.append({
                    'name': product.name,
                    'quantity': float(quantity) if product.unit == 'kg' else int(quantity),
                    'price': float(price_per_unit),
                    'total': float(total_price),
                    'unit': product.get_unit_display()
                })

            # Увеличиваем счетчик использований купона
            if coupon:
                coupon.used_count += 1
                coupon.save(update_fields=['used_count', 'updated_at'])

            # Составляем финальный чек
            payment_info = result.get('data', {})
            payment_info.setdefault('payment_id', result.get('payment_id'))

            final_receipt = {
                'items': products_for_receipt,
                'subtotal': pending_sale['total_without_discount'],
                'discount': pending_sale['discount_amount'],
                'total': pending_sale['total_with_discount'],
                'coupon_code': coupon.code if coupon else None,
                'date': timezone.now().strftime('%d.%m.%Y %H:%M'),
                'cashier': request.user.username,
                'sale_group': sale_group_id,
                'payment_info': payment_info
            }

            request.session['last_receipt'] = final_receipt

            # Очищаем временные данные
            for key in ['pending_sale', 'pre_receipt', 'cart', 'applied_coupon']:
                request.session.pop(key, None)

            request.session['payment_success'] = True
            request.session.modified = True

            messages.success(request, f'Оплата прошла успешно! Сумма: {amount:.2f} ₽')
            return redirect('payment_success')

    except Exception as e:
        messages.error(request, f'Ошибка при списании товаров: {str(e)}')
        return redirect('payment_page')
    

@login_required
def payment_success(request):
    """Страница успешной оплаты"""
    if not request.session.get('payment_success'):
        return redirect('sale')

    receipt = request.session.get('last_receipt')
    request.session.pop('payment_success', None)
    request.session.modified = True

    context = {'receipt': receipt}
    return render(request, 'cash_app/payment_success.html', context)


@login_required
def terminal_status(request):
    """Статус терминала (AJAX)"""
    return JsonResponse({'connected': check_terminal_status()})


@csrf_exempt
def payment_callback(request):
    """Callback для получения результатов оплаты от терминала"""
    if request.method == 'POST':
        try:
            try:
                data = json.loads(request.body)
            except:
                data = request.POST.dict()
            
            print(f"📞 Получен callback от терминала: {data}")
            
            payment_id = data.get('payment_id') or data.get('id')
            status = data.get('status')
            
            if payment_id and status:
                if 'payment_callbacks' not in request.session:
                    request.session['payment_callbacks'] = {}
                request.session['payment_callbacks'][payment_id] = {
                    'status': status,
                    'data': data,
                    'received_at': timezone.now().isoformat()
                }
                request.session.modified = True
            
            return JsonResponse({'status': 'ok', 'received': True})
            
        except Exception as e:
            print(f"❌ Ошибка в callback: {e}")
            return JsonResponse({'status': 'error', 'message': str(e)}, status=400)
    
    return JsonResponse({'status': 'error'}, status=405)


# Функции для управления складами
@login_required
@user_passes_test(is_admin)
def store_list(request):
    """Список складов"""
    stores = StoreAddress.objects.all().order_by('name')
    return render(request, 'cash_app/store_list.html', {'stores': stores})


@login_required
@user_passes_test(is_admin)
def store_create(request):
    """Создание склада"""
    if request.method == 'POST':
        name = request.POST.get('name')
        address = request.POST.get('address')
        city = request.POST.get('city', '')
        phone = request.POST.get('phone', '')
        
        if name and address:
            StoreAddress.objects.create(
                name=name,
                address=address,
                city=city,
                phone=phone,
                is_active=True
            )
            messages.success(request, f'Склад "{name}" успешно создан')
            return redirect('store_list')
        else:
            messages.error(request, 'Название и адрес склада обязательны')
    
    return render(request, 'cash_app/store_form.html', {'title': 'Создание склада'})


@login_required
@user_passes_test(is_admin)
def store_edit(request, pk):
    """Редактирование склада"""
    store = get_object_or_404(StoreAddress, pk=pk)
    
    if request.method == 'POST':
        store.name = request.POST.get('name')
        store.address = request.POST.get('address')
        store.city = request.POST.get('city', '')
        store.phone = request.POST.get('phone', '')
        store.is_active = request.POST.get('is_active') == 'on'
        store.save()
        messages.success(request, f'Склад "{store.name}" успешно обновлен')
        return redirect('store_list')
    
    context = {
        'title': 'Редактирование склада',
        'store': store
    }
    return render(request, 'cash_app/store_form.html', context)


@login_required
@user_passes_test(is_admin)
def store_delete(request, pk):
    """Удаление склада"""
    store = get_object_or_404(StoreAddress, pk=pk)
    
    if request.method == 'POST':
        name = store.name
        store.delete()
        messages.success(request, f'Склад "{name}" удален')
        return redirect('store_list')
    
    return render(request, 'cash_app/store_confirm_delete.html', {'store': store})


# Функции для управления прайс-листами
@login_required
@user_passes_test(is_admin)
def price_list_list(request):
    """Список прайс-листов"""
    price_lists = PriceList.objects.all().order_by('name')
    return render(request, 'cash_app/price_list_list.html', {'price_lists': price_lists})


@login_required
@user_passes_test(is_admin)
def price_list_create(request):
    """Создание прайс-листа"""
    products = Product.objects.all().order_by('name')
    
    if request.method == 'POST':
        name = request.POST.get('name')
        description = request.POST.get('description', '')
        
        multiplier_str = request.POST.get('multiplier', '1.00').replace(',', '.')
        try:
            multiplier = Decimal(multiplier_str)
        except:
            multiplier = Decimal('1.00')
        
        if name:
            price_list = PriceList.objects.create(
                name=name,
                description=description,
                multiplier=multiplier,
                is_active=True
            )
            
            # Добавляем выбранные товары
            product_ids = request.POST.getlist('product_ids')
            custom_prices = request.POST.getlist('custom_prices')
            
            for i, product_id in enumerate(product_ids):
                custom_price_str = custom_prices[i].replace(',', '.') if i < len(custom_prices) and custom_prices[i] else ''
                custom_price = None
                if custom_price_str:
                    try:
                        custom_price = Decimal(custom_price_str)
                    except:
                        custom_price = None
                
                PriceListItem.objects.create(
                    price_list=price_list,
                    product_id=product_id,
                    custom_price=custom_price
                )
            
            messages.success(request, f'Прайс-лист "{name}" успешно создан')
            return redirect('price_list_list')
        else:
            messages.error(request, 'Название прайс-листа обязательно')
    
    context = {
        'title': 'Создание прайс-листа',
        'products': products,
        'existing_items_keys': [],
        'price_list': None,
    }
    return render(request, 'cash_app/price_list_form.html', context)


@login_required
@user_passes_test(is_admin)
def price_list_edit(request, pk):
    """Редактирование прайс-листа"""
    price_list = get_object_or_404(PriceList, pk=pk)
    products = Product.objects.all().order_by('name')
    
    existing_items = {item.product_id: item for item in price_list.items.all()}
    existing_items_keys = list(existing_items.keys())
    
    if request.method == 'POST':
        price_list.name = request.POST.get('name')
        price_list.description = request.POST.get('description', '')
        
        multiplier_str = request.POST.get('multiplier', '1.00').replace(',', '.')
        try:
            price_list.multiplier = Decimal(multiplier_str)
        except:
            price_list.multiplier = Decimal('1.00')
        
        price_list.is_active = request.POST.get('is_active') == 'on'
        price_list.save()
        
        price_list.items.all().delete()
        
        product_ids = request.POST.getlist('product_ids')
        custom_prices = request.POST.getlist('custom_prices')
        
        for i, product_id in enumerate(product_ids):
            custom_price_str = custom_prices[i].replace(',', '.') if i < len(custom_prices) and custom_prices[i] else ''
            custom_price = None
            if custom_price_str:
                try:
                    custom_price = Decimal(custom_price_str)
                except:
                    custom_price = None
            
            PriceListItem.objects.create(
                price_list=price_list,
                product_id=product_id,
                custom_price=custom_price
            )
        
        messages.success(request, f'Прайс-лист "{price_list.name}" успешно обновлен')
        return redirect('price_list_list')
    
    context = {
        'title': 'Редактирование прайс-листа',
        'price_list': price_list,
        'products': products,
        'existing_items_keys': existing_items_keys,
    }
    return render(request, 'cash_app/price_list_form.html', context)


@login_required
@user_passes_test(is_admin)
def price_list_delete(request, pk):
    """Удаление прайс-листа"""
    price_list = get_object_or_404(PriceList, pk=pk)
    
    if request.method == 'POST':
        name = price_list.name
        price_list.delete()
        messages.success(request, f'Прайс-лист "{name}" удален')
        return redirect('price_list_list')
    
    return render(request, 'cash_app/price_list_confirm_delete.html', {'price_list': price_list})


# Функции для управления пользователями
@login_required
@user_passes_test(is_admin)
def user_list(request):
    """Список пользователей"""
    users = User.objects.filter(is_superuser=False).select_related('profile')
    return render(request, 'cash_app/user_list.html', {'users': users})


@login_required
@user_passes_test(is_admin)
def create_user(request):
    """Создание нового пользователя"""
    stores = StoreAddress.objects.filter(is_active=True)
    
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        password2 = request.POST.get('password2')
        store_id = request.POST.get('store_id')
        position = request.POST.get('position', 'Кассир')
        
        if not username or not password:
            messages.error(request, 'Логин и пароль обязательны')
            return redirect('create_user')
        
        if password != password2:
            messages.error(request, 'Пароли не совпадают')
            return redirect('create_user')
        
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Пользователь с таким логином уже существует')
            return redirect('create_user')
        
        user = User.objects.create_user(
            username=username,
            password=password,
            is_staff=False,
            is_superuser=False
        )
        
        store = None
        if store_id:
            try:
                store = StoreAddress.objects.get(pk=store_id)
            except StoreAddress.DoesNotExist:
                pass
        
        UserProfile.objects.create(
            user=user,
            store=store,
            position=position
        )
        
        messages.success(request, f'Пользователь {username} успешно создан')
        return redirect('user_list')
    
    context = {
        'stores': stores,
        'title': 'Создание нового сотрудника'
    }
    return render(request, 'cash_app/create_user.html', context)


@login_required
def select_price_list(request):
    if request.method == 'POST':
        price_list_id = request.POST.get('price_list_id')
        if price_list_id:
            try:
                # Проверяем, что прайс-лист активен
                price_list = PriceList.objects.get(pk=price_list_id, is_active=True)
                request.session['selected_price_list'] = price_list_id
                messages.success(request, f'Выбран прайс-лист: {price_list.name}')
            except PriceList.DoesNotExist:
                messages.error(request, 'Прайс-лист не найден или неактивен')
        else:
            if 'selected_price_list' in request.session:
                del request.session['selected_price_list']
            messages.success(request, 'Прайс-лист сброшен')
    return redirect('sale')


@login_required
@user_passes_test(is_admin)
def select_store(request):
    """Выбор склада для просмотра (только для админа)"""
    if request.method == 'POST':
        store_id = request.POST.get('store_id')
        if store_id:
            request.session['selected_store'] = store_id
            messages.success(request, 'Склад выбран')
        else:
            if 'selected_store' in request.session:
                del request.session['selected_store']
            messages.success(request, 'Показаны все склады')
        request.session.modified = True
    return redirect('product_list')


@login_required
@user_passes_test(is_admin)
def add_store_quantity(request, product_id):
    """Добавление количества товара на склад"""
    product = get_object_or_404(Product, pk=product_id)
    stores = StoreAddress.objects.filter(is_active=True)
    
    if request.method == 'POST':
        store_id = request.POST.get('store_id')
        quantity = Decimal(request.POST.get('quantity', 0))
        
        if store_id and quantity > 0:
            store = get_object_or_404(StoreAddress, pk=store_id)
            store_product, created = StoreProduct.objects.get_or_create(
                store=store,
                product=product,
                defaults={'quantity': 0}
            )
            store_product.quantity += quantity
            store_product.save()
            
            # Сохраняем склад в историю поступления
            History.objects.create(
                type='receipt',
                product=product,
                store=store,  # <-- Добавляем склад
                quantity=quantity,
                user=request.user
            )
            
            messages.success(request, f'Добавлено {quantity} {product.get_unit_display()} товара "{product.name}" на склад "{store.name}"')
        else:
            messages.error(request, 'Выберите склад и укажите количество')
        
        return redirect('product_list')
    
    context = {
        'product': product,
        'stores': stores,
    }
    return render(request, 'cash_app/add_store_quantity.html', context)


@login_required
@user_passes_test(is_admin)
def api_store_product(request, store_id, product_id):
    """API для получения количества товара на складе"""
    try:
        store_product = StoreProduct.objects.get(store_id=store_id, product_id=product_id)
        return JsonResponse({'quantity': float(store_product.quantity)})
    except StoreProduct.DoesNotExist:
        return JsonResponse({'quantity': 0})
    

@login_required
@user_passes_test(is_admin)
def salary_settings_list(request):
    """Список настроек зарплаты"""
    settings_list = SalarySettings.objects.all().select_related('user', 'updated_by')
    users_without_settings = User.objects.filter(is_staff=False, is_active=True).exclude(
        id__in=SalarySettings.objects.values_list('user_id', flat=True)
    )
    
    context = {
        'settings_list': settings_list,
        'users_without_settings': users_without_settings,
    }
    return render(request, 'cash_app/salary_settings_list.html', context)


@login_required
@user_passes_test(is_admin)
def salary_settings_create(request, user_id):
    """Создание настроек зарплаты для пользователя"""
    user = get_object_or_404(User, pk=user_id)
    
    if request.method == 'POST':
        # Заменяем запятые на точки и преобразуем в Decimal
        base_salary_str = request.POST.get('base_salary', '0').replace(',', '.')
        commission_percent_str = request.POST.get('commission_percent', '0').replace(',', '.')
        bonus_percent_str = request.POST.get('bonus_percent', '0').replace(',', '.')
        plan_completion_threshold_str = request.POST.get('plan_completion_threshold', '100').replace(',', '.')
        
        try:
            base_salary = Decimal(base_salary_str)
            commission_percent = Decimal(commission_percent_str)
            bonus_percent = Decimal(bonus_percent_str)
            plan_completion_threshold = Decimal(plan_completion_threshold_str)
        except:
            messages.error(request, 'Неверный формат чисел. Используйте точку или запятую.')
            return redirect('salary_settings_create', user_id=user_id)
        
        settings = SalarySettings.objects.create(
            user=user,
            base_salary=base_salary,
            commission_percent=commission_percent,
            bonus_percent=bonus_percent,
            plan_completion_threshold=plan_completion_threshold,
            updated_by=request.user
        )
        
        messages.success(request, f'Настройки зарплаты для {user.username} созданы')
        return redirect('salary_settings_list')
    
    context = {
        'user': user,
        'title': f'Создание настроек зарплаты для {user.username}'
    }
    return render(request, 'cash_app/salary_settings_form.html', context)


@login_required
@user_passes_test(is_admin)
def salary_settings_edit(request, pk):
    """Редактирование настроек зарплаты"""
    settings = get_object_or_404(SalarySettings, pk=pk)
    
    if request.method == 'POST':
        # Заменяем запятые на точки и преобразуем в Decimal
        base_salary_str = request.POST.get('base_salary', '0').replace(',', '.')
        commission_percent_str = request.POST.get('commission_percent', '0').replace(',', '.')
        bonus_percent_str = request.POST.get('bonus_percent', '0').replace(',', '.')
        plan_completion_threshold_str = request.POST.get('plan_completion_threshold', '100').replace(',', '.')
        
        try:
            settings.base_salary = Decimal(base_salary_str)
            settings.commission_percent = Decimal(commission_percent_str)
            settings.bonus_percent = Decimal(bonus_percent_str)
            settings.plan_completion_threshold = Decimal(plan_completion_threshold_str)
        except:
            messages.error(request, 'Неверный формат чисел. Используйте точку или запятую.')
            return redirect('salary_settings_edit', pk=pk)
        
        settings.updated_by = request.user
        settings.save()
        
        messages.success(request, f'Настройки зарплаты для {settings.user.username} обновлены')
        return redirect('salary_settings_list')
    
    context = {
        'settings': settings,
        'user': settings.user,
        'title': f'Редактирование настроек зарплаты для {settings.user.username}'
    }
    return render(request, 'cash_app/salary_settings_form.html', context)


@login_required
@user_passes_test(is_admin)
def salary_calculations(request):
    """Список расчетов зарплаты"""
    year = request.GET.get('year', timezone.now().year)
    month = request.GET.get('month')
    
    calculations = SalaryCalculation.objects.filter(year=year).select_related('user')
    
    if month:
        calculations = calculations.filter(month=month)
    
    total_amount = calculations.aggregate(total=Sum('total_salary'))['total'] or 0
    
    context = {
        'calculations': calculations,
        'current_year': int(year),
        'current_month': int(month) if month else None,
        'total_amount': total_amount,
        'months': [(i, f'{i:02d}') for i in range(1, 13)],
    }
    return render(request, 'cash_app/salary_calculations.html', context)


@login_required
@user_passes_test(is_admin)
def salary_calculate(request, year, month):
    """Расчет зарплаты за месяц для всех сотрудников"""
    start_date = datetime(int(year), int(month), 1)
    if int(month) == 12:
        end_date = datetime(int(year) + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = datetime(int(year), int(month) + 1, 1) - timedelta(days=1)
    
    start_date = timezone.make_aware(start_date)
    end_date = timezone.make_aware(end_date.replace(hour=23, minute=59, second=59))
    
    # Получаем всех активных сотрудников
    users = User.objects.filter(is_staff=False, is_active=True)
    
    calculated_count = 0
    
    for user in users:
        # Получаем настройки зарплаты
        try:
            settings = SalarySettings.objects.get(user=user)
        except SalarySettings.DoesNotExist:
            continue
        
        # Получаем продажи пользователя за месяц
        sales = History.objects.filter(
            type='sale',
            user=user,
            date__gte=start_date,
            date__lte=end_date
        )
        
        total_sales = sum(float(s.total_price or 0) for s in sales)
        unique_sales = sales.values('sale_group').distinct().count()
        
        # Получаем план пользователя
        try:
            plan = SalesPlan.objects.get(user=user)
            plan_amount = float(plan.monthly_target)
            plan_completion = (total_sales / plan_amount * 100) if plan_amount > 0 else 0
        except SalesPlan.DoesNotExist:
            plan_amount = 0
            plan_completion = 0
        
        # Расчет комиссии от продаж
        commission = total_sales * (float(settings.commission_percent) / 100)
        
        # Расчет премии (если выполнены условия)
        bonus = 0
        if plan_completion >= float(settings.plan_completion_threshold):
            bonus = float(settings.base_salary) * (float(settings.bonus_percent) / 100)
        
        # Итого
        total_salary = float(settings.base_salary) + commission + bonus
        
        # Создаем или обновляем запись
        calculation, created = SalaryCalculation.objects.update_or_create(
            user=user,
            month=int(month),
            year=int(year),
            defaults={
                'base_salary': settings.base_salary,
                'commission': Decimal(str(commission)),
                'bonus': Decimal(str(bonus)),
                'total_salary': Decimal(str(total_salary)),
                'total_sales': Decimal(str(total_sales)),
                'plan_completion': Decimal(str(plan_completion)),
                'status': 'calculated'
            }
        )
        calculated_count += 1
    
    messages.success(request, f'Рассчитано зарплат: {calculated_count}')
    return redirect('salary_calculations')


@login_required
@user_passes_test(is_admin)
def salary_detail(request, pk):
    """Детали расчета зарплаты"""
    calculation = get_object_or_404(SalaryCalculation, pk=pk)
    
    # Получаем все продажи за этот месяц
    start_date = datetime(calculation.year, calculation.month, 1)
    if calculation.month == 12:
        end_date = datetime(calculation.year + 1, 1, 1) - timedelta(days=1)
    else:
        end_date = datetime(calculation.year, calculation.month + 1, 1) - timedelta(days=1)
    
    start_date = timezone.make_aware(start_date)
    end_date = timezone.make_aware(end_date.replace(hour=23, minute=59, second=59))
    
    sales = History.objects.filter(
        type='sale',
        user=calculation.user,
        date__gte=start_date,
        date__lte=end_date
    ).select_related('product', 'store').order_by('-date')
    
    context = {
        'calculation': calculation,
        'sales': sales,
        'total_sales_amount': sum(float(s.total_price or 0) for s in sales),
    }
    return render(request, 'cash_app/salary_detail.html', context)


@login_required
@user_passes_test(is_admin)
def salary_mark_paid(request, pk):
    """Отметить зарплату как выплаченную"""
    calculation = get_object_or_404(SalaryCalculation, pk=pk)
    calculation.status = 'paid'
    calculation.paid_at = timezone.now()
    calculation.save()
    
    messages.success(request, f'Зарплата {calculation.user.username} за {calculation.month}.{calculation.year} отмечена как выплаченная')
    return redirect('salary_calculations')


@login_required
@user_passes_test(is_admin)
def store_dashboard(request):
    """Дашборд по складам для администратора"""
    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_year = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Получаем все активные склады
    stores = StoreAddress.objects.filter(is_active=True)
    
    # Статистика по каждому складу
    store_stats = []
    total_sales_all = 0
    total_sales_count_all = 0
    
    for store in stores:
        # Продажи за месяц
        store_sales = History.objects.filter(
            type='sale',
            store=store,
            date__gte=start_of_month
        ).aggregate(total=Sum('total_price'))['total'] or 0
        
        # Количество чеков
        store_sales_count = History.objects.filter(
            type='sale',
            store=store,
            date__gte=start_of_month
        ).values('sale_group').distinct().count()
        
        # Продажи за год
        store_yearly_sales = History.objects.filter(
            type='sale',
            store=store,
            date__gte=start_of_year
        ).aggregate(total=Sum('total_price'))['total'] or 0
        
        # Средний чек
        if store_sales_count > 0 and store_sales is not None:
            store_avg_check = float(store_sales) / store_sales_count
        else:
            store_avg_check = 0
        
        # Количество сотрудников на складе
        employees_count = UserProfile.objects.filter(store=store).count()
        
        # Количество товаров на складе (с ненулевым остатком)
        products_count = StoreProduct.objects.filter(store=store, quantity__gt=0).count()
        
        # Топ-3 товара на складе
        top_products = History.objects.filter(
            type='sale',
            store=store,
            date__gte=start_of_month
        ).values(
            'product__name',
            'product__unit'
        ).annotate(
            total=Sum('total_price'),
            quantity=Sum('quantity')
        ).order_by('-total')[:3]
        
        top_products_list = []
        for p in top_products:
            top_products_list.append({
                'name': p['product__name'] or 'Товар',
                'unit': p['product__unit'],
                'total': float(p['total']) if p['total'] is not None else 0,
                'quantity': float(p['quantity']) if p['quantity'] is not None else 0
            })
        
        store_sales_value = float(store_sales) if store_sales is not None else 0
        store_yearly_sales_value = float(store_yearly_sales) if store_yearly_sales is not None else 0
        
        store_stats.append({
            'store': store,
            'sales': store_sales_value,
            'sales_count': store_sales_count,
            'yearly_sales': store_yearly_sales_value,
            'average_check': store_avg_check,
            'employees_count': employees_count,
            'products_count': products_count,
            'top_products': top_products_list,
        })
        
        total_sales_all += store_sales_value
        total_sales_count_all += store_sales_count
    
    # Сортируем по выручке
    store_stats = sorted(store_stats, key=lambda x: x['sales'], reverse=True)
    
    # Статистика по дням для графика (сравнение складов)
    days_in_month = calendar.monthrange(now.year, now.month)[1]
    chart_data = {}
    
    for store in stores:
        store_data = []
        for day in range(1, days_in_month + 1):
            day_date = datetime(now.year, now.month, day).date()
            day_start = timezone.make_aware(datetime.combine(day_date, datetime.min.time()))
            day_end = timezone.make_aware(datetime.combine(day_date, datetime.max.time()))
            
            day_sales = History.objects.filter(
                type='sale',
                store=store,
                date__range=[day_start, day_end]
            )
            day_total = sum(float(s.total_price or 0) for s in day_sales if s.total_price is not None)
            store_data.append(round(day_total, 2))
        chart_data[store.name] = store_data
    
    context = {
        'store_stats': store_stats,
        'total_sales_all': round(total_sales_all, 2),
        'total_sales_count_all': total_sales_count_all,
        'stores_count': stores.count(),
        'total_employees': UserProfile.objects.filter(store__isnull=False).count(),
        'chart_data': json.dumps(chart_data),
        'labels': json.dumps(list(range(1, days_in_month + 1))),
        'store_names': json.dumps([store.name for store in stores]),
    }
    
    return render(request, 'cash_app/store_dashboard.html', context)


@login_required
@user_passes_test(is_admin)
def profit_dashboard(request):
    """Дашборд прибыли для администратора"""
    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_year = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Получаем параметры периода
    period = request.GET.get('period', 'current')
    custom_month = request.GET.get('month')
    custom_year = request.GET.get('year')
    
    if period == 'previous':
        if now.month == 1:
            start_date = now.replace(year=now.year-1, month=12, day=1, hour=0, minute=0, second=0, microsecond=0)
        else:
            start_date = now.replace(month=now.month-1, day=1, hour=0, minute=0, second=0, microsecond=0)
    elif period == 'custom' and custom_month and custom_year:
        start_date = datetime(int(custom_year), int(custom_month), 1)
        start_date = timezone.make_aware(start_date)
    else:
        start_date = start_of_month
    
    if start_date.month == 12:
        end_date = start_date.replace(year=start_date.year+1, month=1, day=1) - timedelta(days=1)
    else:
        end_date = start_date.replace(month=start_date.month+1, day=1) - timedelta(days=1)
    end_date = end_date.replace(hour=23, minute=59, second=59)
    
    # Получаем выбранный склад
    selected_store_id = request.GET.get('store')
    selected_store = None
    if selected_store_id:
        try:
            selected_store = StoreAddress.objects.get(pk=selected_store_id, is_active=True)
        except StoreAddress.DoesNotExist:
            selected_store = None
    
    # Фильтр по складу
    store_filter = {}
    if selected_store:
        store_filter = {'store': selected_store}
    
    # Получаем все продажи за период
    sales = History.objects.filter(
        type='sale',
        date__gte=start_date,
        date__lte=end_date,
        **store_filter
    ).select_related('product', 'store')
    
    # Расчет общей выручки
    total_revenue = sum(float(s.total_price or 0) for s in sales)
    
    # Расчет себестоимости проданных товаров
    total_cost = 0
    profit_by_product = {}
    
    for sale in sales:
        cost = float(sale.product.cost_price) * float(sale.quantity)
        total_cost += cost
        
        product_name = sale.product.name
        if product_name not in profit_by_product:
            profit_by_product[product_name] = {
                'product': sale.product,
                'quantity': 0,
                'revenue': 0,
                'cost': 0,
            }
        profit_by_product[product_name]['quantity'] += float(sale.quantity)
        profit_by_product[product_name]['revenue'] += float(sale.total_price or 0)
        profit_by_product[product_name]['cost'] += cost
    
    # Общая прибыль
    total_profit = total_revenue - total_cost
    profit_margin = (total_profit / total_revenue * 100) if total_revenue > 0 else 0
    
    # Расходы на зарплату за период
    # Получаем все расчеты зарплат за выбранный месяц и год
    salary_calculations = SalaryCalculation.objects.filter(
        year=start_date.year,
        month=start_date.month,
        status__in=['calculated', 'paid']  # Учитываем только рассчитанные и выплаченные
    )
    total_salary_expense = sum(float(s.total_salary) for s in salary_calculations)
    
    # Если нет расчетов зарплаты, пробуем получить настройки зарплат и рассчитать
    if total_salary_expense == 0:
        # Получаем всех сотрудников
        users = User.objects.filter(is_staff=False, is_active=True)
        for user in users:
            try:
                settings = SalarySettings.objects.get(user=user)
            except SalarySettings.DoesNotExist:
                continue
            
            # Получаем продажи пользователя за период
            user_sales = sales.filter(user=user)
            user_revenue = sum(float(s.total_price or 0) for s in user_sales)
            
            # Получаем план пользователя
            try:
                plan = SalesPlan.objects.get(user=user)
                plan_target = float(plan.monthly_target)
                plan_completion = (user_revenue / plan_target * 100) if plan_target > 0 else 0
            except SalesPlan.DoesNotExist:
                plan_completion = 0
            
            # Расчет комиссии
            commission = user_revenue * (float(settings.commission_percent) / 100)
            
            # Расчет премии
            bonus = 0
            if plan_completion >= float(settings.plan_completion_threshold):
                bonus = float(settings.base_salary) * (float(settings.bonus_percent) / 100)
            
            # Итого зарплата
            user_salary = float(settings.base_salary) + commission + bonus
            total_salary_expense += user_salary
    
    # Чистая прибыль (с учетом зарплат)
    net_profit = total_profit - total_salary_expense
    
    # Топ товаров по прибыли
    profit_by_product_list = []
    for product_name, data in profit_by_product.items():
        profit = data['revenue'] - data['cost']
        profit_by_product_list.append({
            'name': product_name,
            'unit': data['product'].get_unit_display(),
            'quantity': data['quantity'],
            'revenue': data['revenue'],
            'cost': data['cost'],
            'profit': profit,
            'margin': (profit / data['revenue'] * 100) if data['revenue'] > 0 else 0,
        })
    profit_by_product_list = sorted(profit_by_product_list, key=lambda x: x['profit'], reverse=True)[:10]
    
    # Статистика по складам
    store_profit_stats = []
    stores = StoreAddress.objects.filter(is_active=True)
    for store in stores:
        store_sales = sales.filter(store=store)
        if store_sales.exists():
            store_revenue = sum(float(s.total_price or 0) for s in store_sales)
            store_cost = 0
            for s in store_sales:
                store_cost += float(s.product.cost_price) * float(s.quantity)
            store_profit = store_revenue - store_cost
            store_profit_stats.append({
                'store': store,
                'revenue': store_revenue,
                'cost': store_cost,
                'profit': store_profit,
                'margin': (store_profit / store_revenue * 100) if store_revenue > 0 else 0,
            })
    store_profit_stats = sorted(store_profit_stats, key=lambda x: x['profit'], reverse=True)
    
    # Статистика по дням для графика
    days_in_month = calendar.monthrange(start_date.year, start_date.month)[1]
    daily_revenue = []
    daily_profit = []
    labels = []
    
    for day in range(1, days_in_month + 1):
        day_date = datetime(start_date.year, start_date.month, day).date()
        day_start = timezone.make_aware(datetime.combine(day_date, datetime.min.time()))
        day_end = timezone.make_aware(datetime.combine(day_date, datetime.max.time()))
        
        day_sales = sales.filter(date__range=[day_start, day_end])
        day_revenue = sum(float(s.total_price or 0) for s in day_sales)
        day_cost = 0
        for s in day_sales:
            day_cost += float(s.product.cost_price) * float(s.quantity)
        day_profit = day_revenue - day_cost
        
        daily_revenue.append(round(day_revenue, 2))
        daily_profit.append(round(day_profit, 2))
        labels.append(day)
    
    month_names = {
        1: 'Январь', 2: 'Февраль', 3: 'Март', 4: 'Апрель',
        5: 'Май', 6: 'Июнь', 7: 'Июль', 8: 'Август',
        9: 'Сентябрь', 10: 'Октябрь', 11: 'Ноябрь', 12: 'Декабрь'
    }
    current_month_name = month_names.get(start_date.month, '')
    
    # Получение списка зарплат для отображения
    salary_details = []
    for calc in salary_calculations:
        salary_details.append({
            'user': calc.user,
            'base_salary': float(calc.base_salary),
            'commission': float(calc.commission),
            'bonus': float(calc.bonus),
            'total': float(calc.total_salary),
            'status': calc.status,
        })
    
    context = {
        'total_revenue': round(total_revenue, 2),
        'total_cost': round(total_cost, 2),
        'total_profit': round(total_profit, 2),
        'profit_margin': round(profit_margin, 1),
        'total_salary_expense': round(total_salary_expense, 2),
        'net_profit': round(net_profit, 2),
        'profit_by_product': profit_by_product_list,
        'store_profit_stats': store_profit_stats,
        'salary_details': salary_details,
        'daily_revenue': json.dumps(daily_revenue),
        'daily_profit': json.dumps(daily_profit),
        'labels': json.dumps(labels),
        'current_month_name': current_month_name,
        'current_year': start_date.year,
        'period': period,
        'selected_store': selected_store,
        'stores': stores,
        'has_sales': total_revenue > 0,
    }
    return render(request, 'cash_app/profit_dashboard.html', context)


@login_required
@user_passes_test(is_admin)
def recipe_list(request):
    """Список рецептов"""
    # Показываем все составные блюда, даже если у них еще нет рецепта
    composite_products = Product.objects.filter(is_composite=True)
    
    recipes = []
    for product in composite_products:
        try:
            recipe = Recipe.objects.get(product=product)
            recipes.append({
                'id': recipe.id,
                'product': product,
                'recipe': recipe,
                'has_recipe': True,
                'ingredients_count': recipe.recipe_ingredients.count()
            })
        except Recipe.DoesNotExist:
            recipes.append({
                'id': None,
                'product': product,
                'recipe': None,
                'has_recipe': False,
                'ingredients_count': 0
            })
    
    context = {
        'recipes': recipes,
    }
    return render(request, 'cash_app/recipe_list.html', context)


@login_required
@user_passes_test(is_admin)
def recipe_create(request, product_id):
    """Создание рецепта для блюда"""
    product = get_object_or_404(Product, pk=product_id, is_composite=True)
    ingredients = Product.objects.filter(is_composite=False).exclude(pk=product_id)
    
    if request.method == 'POST':
        yield_quantity = Decimal(request.POST.get('yield_quantity', 1).replace(',', '.'))
        cooking_time = request.POST.get('cooking_time')
        instructions = request.POST.get('instructions', '')
        
        recipe, created = Recipe.objects.get_or_create(
            product=product,
            defaults={
                'yield_quantity': yield_quantity,
                'cooking_time': cooking_time if cooking_time else None,
                'instructions': instructions
            }
        )
        
        # Обновляем ингредиенты
        recipe.recipe_ingredients.all().delete()
        
        ingredient_ids = request.POST.getlist('ingredient_ids')
        quantities = request.POST.getlist('quantities')
        
        for i, ing_id in enumerate(ingredient_ids):
            if ing_id and i < len(quantities) and quantities[i]:
                quantity = Decimal(quantities[i].replace(',', '.'))
                RecipeIngredient.objects.create(
                    recipe=recipe,
                    ingredient_id=ing_id,
                    quantity=quantity
                )
        
        # Обновляем себестоимость
        recipe.update_product_cost_price()
        
        messages.success(request, f'Рецепт для "{product.name}" успешно сохранен')
        return redirect('recipe_list')
    
    # Получаем текущие ингредиенты, если рецепт существует
    existing_ingredients = {}
    existing_ids = []
    try:
        recipe = Recipe.objects.get(product=product)
        for item in recipe.recipe_ingredients.all():
            existing_ingredients[item.ingredient_id] = {
                'quantity': float(item.quantity)
            }
            existing_ids.append(item.ingredient_id)
    except Recipe.DoesNotExist:
        recipe = None
    
    context = {
        'product': product,
        'ingredients': ingredients,
        'recipe': recipe,
        'existing_ingredients': existing_ingredients,
        'existing_ids': existing_ids,
        'title': f'Рецепт: {product.name}'
    }
    return render(request, 'cash_app/recipe_form.html', context)


@login_required
@user_passes_test(is_admin)
def recipe_edit(request, pk):
    """Редактирование рецепта"""
    recipe = get_object_or_404(Recipe, pk=pk)
    product = recipe.product
    ingredients = Product.objects.filter(is_composite=False).exclude(pk=product.id)
    
    if request.method == 'POST':
        recipe.yield_quantity = Decimal(request.POST.get('yield_quantity', 1).replace(',', '.'))
        recipe.cooking_time = request.POST.get('cooking_time')
        recipe.instructions = request.POST.get('instructions', '')
        recipe.save()
        
        # Обновляем ингредиенты
        recipe.recipe_ingredients.all().delete()
        
        ingredient_ids = request.POST.getlist('ingredient_ids')
        quantities = request.POST.getlist('quantities')
        
        for i, ing_id in enumerate(ingredient_ids):
            if ing_id and i < len(quantities) and quantities[i]:
                quantity = Decimal(quantities[i].replace(',', '.'))
                RecipeIngredient.objects.create(
                    recipe=recipe,
                    ingredient_id=ing_id,
                    quantity=quantity
                )
        
        # Обновляем себестоимость
        recipe.update_product_cost_price()
        
        messages.success(request, f'Рецепт для "{product.name}" успешно обновлен')
        return redirect('recipe_list')
    
    existing_ingredients = {}
    for item in recipe.recipe_ingredients.all():
        existing_ingredients[item.ingredient_id] = {
            'quantity': float(item.quantity)
        }
    
    context = {
        'product': product,
        'ingredients': ingredients,
        'recipe': recipe,
        'existing_ingredients': existing_ingredients,
        'title': f'Редактирование рецепта: {product.name}'
    }
    return render(request, 'cash_app/recipe_form.html', context)


@login_required
@user_passes_test(is_admin)
def recipe_delete(request, pk):
    """Удаление рецепта"""
    recipe = get_object_or_404(Recipe, pk=pk)
    product_name = recipe.product.name
    recipe.delete()
    messages.success(request, f'Рецепт для "{product_name}" удален')
    return redirect('recipe_list')
