from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum, Count, F
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from datetime import date, timedelta, datetime
from .models import Product, History, SalesPlan, Category, Coupon
from .forms import LoginForm, ProductForm, SaleForm, BarcodeForm, DisposalForm, SalesPlanForm, CouponForm
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from io import BytesIO
from django.db.models.functions import Lower
import qrcode
import uuid
from django.core.files.base import ContentFile
from django.views.decorators.csrf import csrf_exempt
from decimal import Decimal
import json
import calendar
from django.views.decorators.csrf import csrf_exempt
from django.http import JsonResponse
from .payment import process_terminal_payment, check_terminal_status, get_terminal_info


def is_admin(user):
    """Проверка, является ли пользователь администратором"""
    return user.is_staff or user.is_superuser


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
    return redirect('product_list')


@login_required
def product_list(request):
    """Список товаров с фильтрацией по статусу и поиском"""
    filter_type = request.GET.get('filter', 'all')
    search_query = request.GET.get('search', '').strip()
    
    products = Product.objects.all()
    
    # Применяем поиск по названию (регистронезависимый)
    if search_query:
        search_query_lower = search_query.lower()
        all_products = list(products)
        filtered_products = []
        
        for product in all_products:
            name_lower = product.name.lower() if product.name else ''
            if search_query_lower in name_lower:
                filtered_products.append(product)
        
        products = filtered_products
        search_title = f'Результаты поиска: "{search_query}"'
    else:
        search_title = None
        products = list(products)
    
    # Применяем фильтр по статусу
    if filter_type == 'expired':
        products = [p for p in products if p.is_expired()]
        filter_title = "Просроченные товары"
    elif filter_type == 'expiring_soon':
        products = [p for p in products if p.is_expiring_soon()]
        filter_title = "Товары с истекающим сроком годности"
    else:
        filter_type = 'all'
        filter_title = "Все товары"
    
    all_products = Product.objects.all()
    total_products = all_products.count()
    expired_count = sum(1 for p in all_products if p.is_expired())
    expiring_soon_count = sum(1 for p in all_products if p.is_expiring_soon())
    
    context = {
        'products': products,
        'total_products': total_products,
        'expired_count': expired_count,
        'expiring_soon_count': expiring_soon_count,
        'current_filter': filter_type,
        'filter_title': filter_title,
        'search_query': search_query,
        'search_title': search_title,
    }
    return render(request, 'cash_app/product_list.html', context)


@login_required
def product_create(request):
    """Создание товара"""
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save()
            
            if product.quantity > 0:
                History.objects.create(
                    type='receipt',
                    product=product,
                    quantity=product.quantity,
                    user=request.user
                )
            
            # Генерируем QR-код
            product.generate_qr_code()
            product.save()
            
            messages.success(request, 'Товар успешно добавлен! QR-код сгенерирован')
            return redirect('product_list')
    else:
        form = ProductForm()
    
    return render(request, 'cash_app/product_form.html', {'form': form, 'title': 'Добавление товара'})


@login_required
def product_update(request, pk):
    """Редактирование товара"""
    product = get_object_or_404(Product, pk=pk)
    old_quantity = product.quantity
    
    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        if form.is_valid():
            new_product = form.save()
            
            # Если количество изменилось, создаем запись в истории
            if new_product.quantity != old_quantity:
                if new_product.quantity > old_quantity:
                    History.objects.create(
                        type='receipt',
                        product=new_product,
                        quantity=new_product.quantity - old_quantity,
                        user=request.user
                    )
                else:
                    History.objects.create(
                        type='disposal',
                        product=new_product,
                        quantity=old_quantity - new_product.quantity,
                        user=request.user
                    )
            
            messages.success(request, 'Товар успешно обновлен!')
            return redirect('product_list')
    else:
        form = ProductForm(instance=product)
    
    return render(request, 'cash_app/product_form.html', {
        'form': form, 
        'title': 'Редактирование товара',
        'product': product
    })

@login_required
def product_delete(request, pk):
    """Удаление товара"""
    product = get_object_or_404(Product, pk=pk)
    
    if request.method == 'POST':
        # Создаем запись об утилизации перед удалением
        if product.quantity > 0:
            History.objects.create(
                type='disposal',
                product=product,
                quantity=product.quantity,
                user=request.user
            )
        
        product.delete()
        messages.success(request, 'Товар успешно удален!')
        return redirect('product_list')
    
    return render(request, 'cash_app/product_confirm_delete.html', {'product': product})

@login_required
def product_disposal(request, pk):
    """Списание просроченного товара"""
    product = get_object_or_404(Product, pk=pk)
    
    # Проверяем, действительно ли товар просрочен
    if not product.is_expired():
        messages.error(request, 'Можно списывать только просроченные товары!')
        return redirect('product_list')
    
    if request.method == 'POST':
        form = DisposalForm(request.POST)
        if form.is_valid():
            quantity = form.cleaned_data['quantity']
            reason = form.cleaned_data['reason']
            
            if quantity > product.quantity:
                messages.error(request, f'Нельзя списать больше, чем есть на складе! Доступно: {product.quantity}')
                return redirect('product_disposal', pk=product.pk)
            
            if quantity <= 0:
                messages.error(request, 'Количество должно быть больше 0')
                return redirect('product_disposal', pk=product.pk)
            
            # Уменьшаем количество товара
            product.quantity -= quantity
            product.save()
            
            # Создаем запись в истории
            History.objects.create(
                type='disposal',
                product=product,
                quantity=quantity,
                user=request.user,
                reason=reason
            )
            
            messages.success(request, f'Списано {quantity} {product.get_unit_display()} товара "{product.name}"')
            return redirect('product_list')
    else:
        form = DisposalForm(initial={'quantity': product.quantity})
    
    context = {
        'product': product,
        'form': form,
    }
    return render(request, 'cash_app/product_disposal.html', context)


@login_required
def history_list(request):
    """История движений товаров с группировкой продаж"""
    
    # Получаем параметры фильтрации
    type_filter = request.GET.get('type')
    product_filter = request.GET.get('product')
    
    # Базовый запрос
    history = History.objects.all().select_related('product', 'user', 'coupon')
    
    # Фильтрация по типу (для продаж используем группировку)
    if type_filter and type_filter in dict(History.TYPE_CHOICES).keys():
        if type_filter == 'sale':
            # Для продаж - группируем по sale_group
            pass
        else:
            history = history.filter(type=type_filter)
    else:
        # Если не выбрана продажа, показываем все, кроме продаж (они будут сгруппированы отдельно)
        pass
    
    # Фильтрация по товару (только для не-продаж)
    if product_filter and type_filter != 'sale':
        history = history.filter(product_id=product_filter)
    
    # Получаем все записи о продажах и группируем их
    sales = history.filter(type='sale').order_by('-date')
    grouped_sales = {}
    
    for sale in sales:
        if sale.sale_group:
            if sale.sale_group not in grouped_sales:
                grouped_sales[sale.sale_group] = {
                    'items': [],
                    'date': sale.date,
                    'user': sale.user,
                    'total': 0,
                    'coupon': sale.coupon,
                    'sale_group': sale.sale_group
                }
            grouped_sales[sale.sale_group]['items'].append(sale)
            grouped_sales[sale.sale_group]['total'] += float(sale.total_price or 0)
    
    # Преобразуем grouped_sales в список для шаблона
    grouped_sales_list = []
    for group_id, group_data in grouped_sales.items():
        grouped_sales_list.append({
            'type': 'sale_group',
            'sale_group': group_data['sale_group'],
            'date': group_data['date'],
            'user': group_data['user'],
            'total': round(group_data['total'], 2),
            'coupon': group_data['coupon'],
            'items': group_data['items']
        })
    
    # Сортируем по дате (новые сверху)
    grouped_sales_list.sort(key=lambda x: x['date'], reverse=True)
    
    # Получаем остальные записи (не продажи)
    other_history = history.filter(type__in=['receipt', 'disposal']).order_by('-date')
    
    # Объединяем и сортируем
    all_entries = []
    all_entries.extend(other_history)
    all_entries.extend(grouped_sales_list)
    
    # Сортируем все записи по дате
    all_entries.sort(key=lambda x: x.date if hasattr(x, 'date') else x['date'], reverse=True)
    
    context = {
        'entries': all_entries,
        'type_choices': History.TYPE_CHOICES,
        'products': Product.objects.all(),
        'current_type': type_filter,
        'current_product': product_filter,
    }
    return render(request, 'cash_app/history_list.html', context)


@login_required
def sale_view(request):
    """Продажа товаров"""
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
            total_without_discount = 0
            sale_group_id = uuid.uuid4()
            
            for product_id, item in cart.items():
                product = Product.objects.get(pk=product_id)
                quantity = Decimal(str(item['quantity']))
                
                if product.unit == 'kg' and quantity < Decimal('0.001'):
                    raise ValueError(f'Минимальное количество для "{product.name}" - 0,001 кг')
                
                if float(product.quantity) < float(quantity):
                    raise ValueError(f'Недостаточно "{product.name}" на складе')
                
                if product.expiration_date < date.today():
                    raise ValueError(f'Товар "{product.name}" просрочен')
                
                item_price = float(product.price)
                item_total = item_price * float(quantity)
                total_without_discount += item_total
                
                sale_items.append({
                    'product_id': product.id,
                    'product_name': product.name,
                    'quantity': float(quantity),
                    'price': round(item_price, 2),
                    'total': round(item_total * discount_factor, 2),
                    'unit': product.get_unit_display(),
                })
            
            total_with_discount = round(total_without_discount * discount_factor, 2)
            discount_amount = round(total_without_discount - total_with_discount, 2) if coupon else 0
            
            # Сохраняем в сессию
            request.session['pending_sale'] = {
                'sale_items': sale_items,
                'total_without_discount': round(total_without_discount, 2),
                'total_with_discount': total_with_discount,
                'discount_amount': discount_amount,
                'coupon_id': coupon.id if coupon else None,
                'sale_group_id': str(sale_group_id),
            }
            
            request.session['pre_receipt'] = {
                'items': [
                    {'name': item['product_name'], 'quantity': item['quantity'],
                     'price': item['price'], 'total': item['total'], 'unit': item['unit']}
                    for item in sale_items
                ],
                'subtotal': round(total_without_discount, 2),
                'discount': discount_amount,
                'total': total_with_discount,
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
    
    # GET часть
    # Группируем товары по категориям
    available_products = Product.objects.filter(
        quantity__gt=0,
        expiration_date__gte=date.today()
    ).select_related('category').order_by('category__name', 'name')
    
    # Получаем все категории
    all_categories = Category.objects.all().order_by('name')
    
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
    total = 0
    
    for product_id, item in cart.items():
        try:
            product = Product.objects.get(pk=product_id)
            subtotal = float(product.price) * item['quantity']
            total += subtotal
            cart_items.append({
                'product': product,
                'quantity': item['quantity'],
                'subtotal': subtotal
            })
        except Product.DoesNotExist:
            continue
    
    total = round(total, 2)
    
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
    discount_amount = 0
    total_with_discount = total
    
    if applied_coupon_id:
        try:
            applied_coupon = Coupon.objects.get(pk=applied_coupon_id)
            if applied_coupon.is_valid():
                total_with_discount = applied_coupon.apply_discount(total)
                total_with_discount = round(total_with_discount, 2)
                discount_amount = round(total - total_with_discount, 2)
            else:
                del request.session['applied_coupon']
                request.session.modified = True
        except Coupon.DoesNotExist:
            del request.session['applied_coupon']
            request.session.modified = True
    
    context = {
        'categories': categories_with_products,
        'all_categories': all_categories,
        'collapsed_categories': [str(cat_id) for cat_id in collapsed_categories],
        'cart_items': cart_items,
        'total': total,
        'total_with_discount': total_with_discount,
        'discount_amount': discount_amount,
        'available_coupons': available_coupons,
        'applied_coupon': applied_coupon,
    }
    return render(request, 'cash_app/sale.html', context)


@login_required
def add_to_cart(request):
    """Добавление товара в корзину"""
    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        quantity = float(request.POST.get('quantity', 1))
        
        product = get_object_or_404(Product, pk=product_id)
        
        # Проверка минимального количества для кг
        if product.unit == 'kg' and quantity < 0.001:
            messages.error(request, f'Минимальное количество для товара "{product.name}" - 0,001 кг')
            return redirect('sale')
        
        # Проверка наличия
        if float(product.quantity) < quantity:
            messages.error(request, f'Недостаточно товара "{product.name}" на складе')
            return redirect('sale')
        
        if product.expiration_date < date.today():
            messages.error(request, f'Товар "{product.name}" просрочен и не может быть продан')
            return redirect('sale')
        
        cart = request.session.get('cart', {})
        
        if product_id in cart:
            cart[product_id]['quantity'] += quantity
        else:
            cart[product_id] = {
                'quantity': quantity,
                'price': str(product.price),
                'unit': product.unit
            }
        
        request.session['cart'] = cart
        request.session.modified = True
        
        messages.success(request, f'Товар "{product.name}" добавлен в корзину ({quantity} {product.get_unit_display()})')
    
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
    
    # Создаем PDF
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, 
                           rightMargin=72, leftMargin=72,
                           topMargin=72, bottomMargin=18)
    
    styles = getSampleStyleSheet()
    story = []
    
    # Заголовок
    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontSize=24,
        alignment=1,  # Center alignment
        spaceAfter=30
    )
    
    story.append(Paragraph("КАССОВЫЙ ЧЕК", title_style))
    
    # Информация о продаже
    info_style = ParagraphStyle(
        'Info',
        parent=styles['Normal'],
        fontSize=12,
        spaceAfter=5
    )
    
    story.append(Paragraph(f"Дата: {receipt['date']}", info_style))
    story.append(Paragraph(f"Кассир: {receipt['cashier']}", info_style))
    if receipt.get('coupon_code'):
        story.append(Paragraph(f"Купон: {receipt['coupon_code']}", info_style))
    story.append(Spacer(1, 20))
    
    # Таблица с товарами
    data = [['№', 'Товар', 'Кол-во', 'Цена', 'Сумма']]
    for i, item in enumerate(receipt['items'], 1):
        data.append([
            str(i),
            item['name'],
            f"{item['quantity']} {item['unit']}",
            f"{item['price']:.2f} ₽",
            f"{item['total']:.2f} ₽"
        ])
    
    # Строка с подытогом и скидкой
    data.append(['', '', '', 'ПОДЫТОГ:', f"{receipt['subtotal']:.2f} ₽"])
    if receipt.get('discount', 0) > 0:
        data.append(['', '', '', 'СКИДКА:', f"-{receipt['discount']:.2f} ₽"])
    
    # Итоговая строка
    data.append(['', '', '', 'ИТОГО:', f"{receipt['total']:.2f} ₽"])
    
    table = Table(data, colWidths=[30, 200, 70, 70, 80])
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.grey),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.whitesmoke),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('FONTNAME', (0, 0), (-1, 0), 'Helvetica-Bold'),
        ('FONTSIZE', (0, 0), (-1, 0), 12),
        ('BOTTOMPADDING', (0, 0), (-1, 0), 12),
        ('BACKGROUND', (0, 1), (-1, -4), colors.beige),
        ('GRID', (0, 0), (-1, -4), 1, colors.black),
        ('FONTNAME', (0, -1), (-1, -1), 'Helvetica-Bold'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.lightgrey),
        ('ALIGN', (3, 1), (4, -1), 'RIGHT'),
    ]))
    
    story.append(table)
    story.append(Spacer(1, 30))
    
    # Подпись
    story.append(Paragraph("Спасибо за покупку!", info_style))
    
    doc.build(story)
    
    # Возвращаем PDF
    buffer.seek(0)
    response = HttpResponse(buffer, content_type='application/pdf')
    response['Content-Disposition'] = f'attachment; filename="receipt_{receipt["date"].replace(" ", "_").replace(":", "-")}.pdf"'
    
    return response


@login_required
def dashboard_view(request):
    """Дашборд эффективности с улучшенным отображением"""
    now = timezone.now()
    start_of_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    start_of_year = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
    
    # Для обычного пользователя - только свои данные
    if not is_admin(request.user):
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
        monthly_sales = sum(float(s.total_price or 0) for s in user_sales)
        
        # Количество продаж (уникальных групп продаж)
        # Считаем количество уникальных sale_group
        unique_sales = user_sales.values('sale_group').distinct().count()
        
        # Средний чек - сумма всех продаж / количество чеков
        if unique_sales > 0:
            average_check = monthly_sales / unique_sales
        else:
            average_check = 0
        
        # Продажи за сегодня
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_sales = History.objects.filter(
            type='sale',
            user=request.user,
            date__gte=today_start
        )
        today_total = sum(float(s.total_price or 0) for s in today_sales)
        
        # Продажи за неделю
        week_start = now - timedelta(days=7)
        week_sales = History.objects.filter(
            type='sale',
            user=request.user,
            date__gte=week_start
        )
        week_total = sum(float(s.total_price or 0) for s in week_sales)
        
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
            day_total = sum(float(s.total_price or 0) for s in day_sales)
            
            daily_data.append(day_total)
            labels.append(day)
        
        context = {
            'plan': plan,
            'monthly_sales': monthly_sales,
            'completion_percentage': plan.get_completion_percentage(),
            'remaining_amount': plan.get_remaining_amount(),
            'daily_average': plan.get_daily_average(),
            'today_total': today_total,
            'week_total': week_total,
            'average_check': average_check,  # Добавляем правильный средний чек
            'sales_count': unique_sales,    # Количество чеков
            'daily_data': json.dumps(daily_data),
            'labels': json.dumps(labels),
            'has_sales': any(d > 0 for d in daily_data),
            'is_admin': False,
        }
        
        return render(request, 'cash_app/dashboard_user.html', context)
    
    # Для администратора - общая статистика
    else:
        # Общая выручка за месяц
        total_monthly_sales = History.objects.filter(
            type='sale',
            date__gte=start_of_month
        ).aggregate(total=Sum('total_price'))['total'] or 0
        
        # Общая выручка за год
        total_yearly_sales = History.objects.filter(
            type='sale',
            date__gte=start_of_year
        ).aggregate(total=Sum('total_price'))['total'] or 0
        
        # Количество уникальных продаж (чеков) за месяц
        sales_count = History.objects.filter(
            type='sale',
            date__gte=start_of_month
        ).values('sale_group').distinct().count()
        
        # Средний чек - сумма всех продаж / количество чеков
        if sales_count > 0:
            average_check = float(total_monthly_sales) / sales_count
        else:
            average_check = 0
        
        # Статистика по планам
        users_with_plans = SalesPlan.objects.all().select_related('user', 'updated_by')
        
        # Подсчет статистики выполнения
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
        days_in_month = calendar.monthrange(now.year, now.month)[1]
        daily_data = []
        labels = []
        
        for day in range(1, days_in_month + 1):
            day_date = datetime(now.year, now.month, day).date()
            day_start = timezone.make_aware(datetime.combine(day_date, datetime.min.time()))
            day_end = timezone.make_aware(datetime.combine(day_date, datetime.max.time()))
            
            day_sales = History.objects.filter(
                type='sale',
                date__range=[day_start, day_end]
            )
            day_total = sum(float(s.total_price or 0) for s in day_sales)
            
            daily_data.append(day_total)
            labels.append(day)
        
        # В функции dashboard_view для администратора, в части top_products:

        # Топ-3 товара за месяц с единицами измерения
        top_products = History.objects.filter(
            type='sale',
            date__gte=start_of_month
        ).values(
            'product__name', 
            'product__unit'  # Добавляем единицу измерения
        ).annotate(
            total=Sum('total_price'),
            quantity=Sum('quantity')
        ).order_by('-total')[:5]

        # Преобразуем в список для удобства
        top_products_list = []
        for p in top_products:
            top_products_list.append({
                'product__name': p['product__name'],
                'product__unit': p['product__unit'],
                'total': p['total'],
                'quantity': p['quantity']
            })
        
        context = {
            'total_monthly_sales': float(total_monthly_sales),
            'total_yearly_sales': float(total_yearly_sales),
            'sales_count': sales_count,
            'average_check': average_check,
            'users_with_plans': users_with_plans,
            'total_plans': total_plans,
            'half_completed': half_completed,
            'over_completed': over_completed,
            'daily_data': json.dumps(daily_data),
            'labels': json.dumps(labels),
            'top_products': top_products,
            'has_sales': any(d > 0 for d in daily_data),
            'is_admin': True,
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
    
    # Проверяем, есть ли товары в этой категории
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
    action = request.GET.get('action', 'add_to_cart')  # add_to_cart или receipt
    
    # Проверяем, есть ли в сессии сохраненный товар (если пришли с результата)
    scanned_product_id = request.session.get('scanned_product_id')
    scan_action = request.session.get('scan_action')
    
    if scanned_product_id and scan_action:
        try:
            product = Product.objects.get(pk=scanned_product_id)
            # Очищаем сессию
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
    action = request.GET.get('action', 'add_to_cart')  # add_to_cart или receipt
    
    if not qr_data:
        messages.error(request, 'Не удалось прочитать QR-код')
        return redirect('scan_qr')
    
    # Парсим данные из QR-кода
    try:
        if qr_data.startswith('product:'):
            qr_uuid = qr_data.replace('product:', '')
            product = Product.objects.get(qr_uuid=qr_uuid)
            
            # Сохраняем в сессии для следующего шага
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
            # Читаем загруженное изображение
            image_file = request.FILES['qr_image']
            image = Image.open(io.BytesIO(image_file.read()))
            
            # Декодируем QR-код
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
        
        # Преобразуем количество в float, затем в Decimal для точности
        try:
            quantity = Decimal(str(quantity_str).replace(',', '.'))
        except:
            messages.error(request, 'Неверный формат количества')
            return redirect('scan_qr')
        
        # Проверка минимального количества для кг
        if product.unit == 'kg' and quantity < Decimal('0.001'):
            messages.error(request, f'Минимальное количество для товара "{product.name}" - 0,001 кг')
            return redirect('scan_qr')
        
        if product.unit == 'pcs' and quantity < 1:
            messages.error(request, f'Минимальное количество для товара "{product.name}" - 1 шт')
            return redirect('scan_qr')
        
        if action == 'add_to_cart':
            # Добавление в корзину для продажи
            if float(product.quantity) < float(quantity):
                messages.error(request, f'Недостаточно товара "{product.name}" на складе')
                return redirect('sale')
            
            if product.expiration_date < date.today():
                messages.error(request, f'Товар "{product.name}" просрочен и не может быть продан')
                return redirect('sale')
            
            cart = request.session.get('cart', {})
            product_id_str = str(product.id)
            
            if product_id_str in cart:
                cart[product_id_str]['quantity'] += float(quantity)
            else:
                cart[product_id_str] = {
                    'quantity': float(quantity),
                    'price': str(product.price),
                    'unit': product.unit
                }
            
            request.session['cart'] = cart
            request.session.modified = True
            
            # Форматируем количество для отображения
            qty_display = f"{float(quantity):.3f}" if product.unit == 'kg' else f"{int(quantity)}"
            messages.success(request, f'Товар "{product.name}" добавлен в корзину ({qty_display} {product.get_unit_display()})')
            return redirect('sale')
            
        elif action == 'receipt':
            # Оформление поступления товара
            product.quantity += quantity
            product.save()
            
            History.objects.create(
                type='receipt',
                product=product,
                quantity=quantity,
                user=request.user
            )
            
            qty_display = f"{float(quantity):.3f}" if product.unit == 'kg' else f"{int(quantity)}"
            messages.success(request, f'Поступление товара "{product.name}" оформлено. Добавлено {qty_display} {product.get_unit_display()}')
            return redirect('product_list')
    
    return redirect('scan_qr')


@login_required
def generate_product_qr(request, product_id):
    """Генерация QR-кода для существующего товара (на случай если его нет)"""
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
    
    from django.http import FileResponse
    import os
    
    response = FileResponse(product.qr_code, as_attachment=True, filename=f'qr_{product.name}.png')
    return response


@login_required
def print_product_qr(request, product_id):
    """Печать QR-кода товара (открывает в новом окне для печати)"""
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
    if request.method == 'POST':
        pending_sale = request.session.get('pending_sale')
        
        if not pending_sale:
            messages.error(request, 'Нет данных для оплаты')
            return redirect('sale')
        
        amount = pending_sale.get('total_with_discount', 0)
        sale_group_id = pending_sale.get('sale_group_id', '')
        description = f"Оплата в кассовой системе №{sale_group_id[:8]}"
        
        # Проверяем доступность терминала
        if not check_terminal_status():
            messages.error(request, 'Терминал не доступен. Пожалуйста, проверьте подключение и убедитесь, что Kaspi POS Simulator запущен.')
            return redirect('payment_page')
        
        # Отправляем запрос на оплату
        result = process_terminal_payment(amount, description)
        
        if result.get('success'):
            try:
                with transaction.atomic():
                    sale_items = pending_sale.get('sale_items', [])
                    coupon_id = pending_sale.get('coupon_id')
                    sale_group_id = pending_sale.get('sale_group_id')
                    
                    coupon = None
                    if coupon_id:
                        try:
                            coupon = Coupon.objects.get(pk=coupon_id)
                        except:
                            pass
                    
                    # Проверяем, что количество на складе не изменилось
                    for item in sale_items:
                        product = Product.objects.select_for_update().get(pk=item['product_id'])
                        if float(product.quantity) < item['quantity']:
                            raise ValueError(f'Количество товара "{product.name}" изменилось. Требуется: {item["quantity"]}, доступно: {product.quantity}')
                        
                        if product.expiration_date < date.today():
                            raise ValueError(f'Товар "{product.name}" просрочен и не может быть продан')
                    
                    # Списание товаров
                    for item in sale_items:
                        product = Product.objects.get(pk=item['product_id'])
                        product.quantity -= Decimal(str(item['quantity']))
                        product.save()
                        
                        # Создаем запись в истории
                        History.objects.create(
                            type='sale',
                            product=product,
                            quantity=Decimal(str(item['quantity'])),
                            total_price=Decimal(str(item['total'])),
                            user=request.user,
                            coupon=coupon,
                            sale_group=uuid.UUID(sale_group_id)
                        )
                    
                    # Увеличиваем счетчик использований купона
                    if coupon:
                        coupon.used_count += 1
                        coupon.save()
                    
                    # Получаем товары для чека
                    products_for_receipt = []
                    for item in sale_items:
                        product = Product.objects.get(pk=item['product_id'])
                        products_for_receipt.append({
                            'name': product.name,
                            'quantity': item['quantity'],
                            'price': item['price'],
                            'total': item['total'],
                            'unit': item['unit']
                        })
                    
                    # Сохраняем финальный чек
                    final_receipt = {
                        'items': products_for_receipt,
                        'subtotal': pending_sale['total_without_discount'],
                        'discount': pending_sale['discount_amount'],
                        'total': pending_sale['total_with_discount'],
                        'coupon_code': coupon.code if coupon else None,
                        'date': timezone.now().strftime('%d.%m.%Y %H:%M'),
                        'cashier': request.user.username,
                        'sale_group': sale_group_id,
                        'payment_info': result.get('data', {})
                    }
                    
                    request.session['last_receipt'] = final_receipt
                    
                    # Очищаем временные данные
                    for key in ['pending_sale', 'pre_receipt', 'cart', 'applied_coupon']:
                        if key in request.session:
                            del request.session[key]
                    
                    request.session['payment_success'] = True
                    request.session['payment_data'] = result.get('data')
                    request.session['payment_id'] = result.get('payment_id')
                    request.session.modified = True
                    
                    messages.success(request, f'Оплата прошла успешно! Сумма: {amount:.2f} ₽')
                    return redirect('payment_success')
                    
            except Exception as e:
                messages.error(request, f'Ошибка при списании товаров: {str(e)}')
                return redirect('payment_page')
        else:
            error_msg = result.get('error', 'Ошибка при оплате')
            messages.error(request, f'Ошибка оплаты: {error_msg}')
            return redirect('payment_page')
    
    return redirect('sale')


@login_required
def payment_success(request):
    """Страница успешной оплаты"""
    if not request.session.get('payment_success'):
        return redirect('sale')
    
    receipt = request.session.get('last_receipt')
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
            # Пытаемся получить JSON данные
            try:
                data = json.loads(request.body)
            except:
                data = request.POST.dict()
            
            print(f"📞 Получен callback от терминала: {data}")
            
            payment_id = data.get('payment_id') or data.get('id')
            status = data.get('status')
            
            if payment_id and status:
                # Сохраняем в сессию результат
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
