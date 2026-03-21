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
from .models import Category, Coupon, History, Product, SalesPlan
from .payment import check_terminal_status, process_terminal_payment


def is_admin(user):
    """Проверка, является ли пользователь администратором"""
    return user.is_staff or user.is_superuser


def parse_decimal_quantity(value):
    try:
        return Decimal(str(value).replace(',', '.'))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError('Неверный формат количества')


def validate_quantity_for_product(product, quantity):
    if quantity <= 0:
        raise ValueError('Количество должно быть больше 0')

    if product.unit == 'kg':
        if quantity < Decimal('0.001'):
            raise ValueError(f'Минимальное количество для "{product.name}" — 0,001 кг')
    elif product.unit == 'pcs':
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
        try:
            product.delete()
            messages.success(request, 'Товар успешно удален!')
        except ProtectedError:
            messages.error(
                request,
                'Нельзя удалить товар, по которому уже есть история операций. '
                'Сначала обнулите остаток и оставьте товар в системе для сохранения отчетности.'
            )
        return redirect('product_list')

    return render(request, 'cash_app/product_confirm_delete.html', {'product': product})


@login_required
def product_disposal(request, pk):
    """Списание просроченного товара"""
    product = get_object_or_404(Product, pk=pk)

    if not product.is_expired():
        messages.error(request, 'Можно списывать только просроченные товары!')
        return redirect('product_list')

    if request.method == 'POST':
        form = DisposalForm(request.POST)
        if form.is_valid():
            quantity = form.cleaned_data['quantity']
            reason = form.cleaned_data['reason']

            try:
                validate_quantity_for_product(product, quantity)
            except ValueError as e:
                messages.error(request, str(e))
                return redirect('product_disposal', pk=product.pk)

            if quantity > product.quantity:
                messages.error(request, f'Нельзя списать больше, чем есть на складе! Доступно: {product.quantity}')
                return redirect('product_disposal', pk=product.pk)

            product.quantity -= quantity
            product.save()

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
    """История движений товаров с корректной фильтрацией и группировкой продаж"""
    type_filter = request.GET.get('type')
    product_filter = request.GET.get('product')

    base_qs = History.objects.all().select_related('product', 'user', 'coupon')

    sales_qs = base_qs.filter(type='sale').order_by('-date')
    other_qs = base_qs.exclude(type='sale').order_by('-date')

    if product_filter:
        sales_qs = sales_qs.filter(product_id=product_filter)
        other_qs = other_qs.filter(product_id=product_filter)

    if type_filter == 'sale':
        other_qs = other_qs.none()
    elif type_filter in ['receipt', 'disposal']:
        other_qs = other_qs.filter(type=type_filter)
        sales_qs = sales_qs.none()

    grouped_sales = {}
    for sale in sales_qs:
        if sale.sale_group not in grouped_sales:
            grouped_sales[sale.sale_group] = {
                'type': 'sale_group',
                'sale_group': sale.sale_group,
                'date': sale.date,
                'user': sale.user,
                'coupon': sale.coupon,
                'items': [],
                'total': 0,
            }

        grouped_sales[sale.sale_group]['items'].append(sale)
        grouped_sales[sale.sale_group]['total'] += float(sale.total_price or 0)

    grouped_sales_list = list(grouped_sales.values())
    grouped_sales_list.sort(key=lambda x: x['date'], reverse=True)

    all_entries = list(other_qs) + grouped_sales_list
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
        product = get_object_or_404(Product, pk=product_id)

        try:
            quantity = parse_decimal_quantity(request.POST.get('quantity', 1))
            validate_quantity_for_product(product, quantity)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('sale')

        if product.quantity < quantity:
            messages.error(request, f'Недостаточно товара "{product.name}" на складе')
            return redirect('sale')

        if product.expiration_date < date.today():
            messages.error(request, f'Товар "{product.name}" просрочен и не может быть продан')
            return redirect('sale')

        cart = request.session.get('cart', {})
        product_id = str(product.id)

        current_quantity = Decimal(str(cart.get(product_id, {}).get('quantity', 0)))
        new_quantity = current_quantity + quantity

        if new_quantity > product.quantity:
            messages.error(request, f'Нельзя добавить больше, чем есть на складе')
            return redirect('sale')

        cart[product_id] = {
            'quantity': float(new_quantity) if product.unit == 'kg' else int(new_quantity),
            'price': str(product.price),
            'unit': product.unit,
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

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=20,
        leftMargin=20,
        topMargin=20,
        bottomMargin=20
    )

    font_path = settings.BASE_DIR / 'cash_app' / 'static' / 'cash_app' / 'fonts' / 'DejaVuSans.ttf'
    regular_font = 'Helvetica'
    bold_font = 'Helvetica-Bold'

    if font_path.exists():
        pdfmetrics.registerFont(TTFont('DejaVuSans', str(font_path)))
        regular_font = 'DejaVuSans'
        bold_font = 'DejaVuSans'

    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle(
        'CustomTitle',
        parent=styles['Heading1'],
        fontName=bold_font,
        fontSize=18,
        alignment=1,
        spaceAfter=12
    )

    info_style = ParagraphStyle(
        'Info',
        parent=styles['Normal'],
        fontName=regular_font,
        fontSize=10,
        leading=12,
        spaceAfter=4
    )

    table_text_style = ParagraphStyle(
        'TableText',
        parent=styles['Normal'],
        fontName=regular_font,
        fontSize=9,
        leading=11
    )

    story.append(Paragraph("КАССОВЫЙ ЧЕК", title_style))
    story.append(Paragraph(f"Дата: {receipt['date']}", info_style))
    story.append(Paragraph(f"Кассир: {receipt['cashier']}", info_style))

    if receipt.get('coupon_code'):
        story.append(Paragraph(f"Купон: {receipt['coupon_code']}", info_style))

    if receipt.get('payment_info'):
        payment_id = receipt['payment_info'].get('payment_id') or receipt.get('sale_group') or '—'
        story.append(Paragraph(f"ID платежа: {payment_id}", info_style))

    story.append(Spacer(1, 10))

    data = [[
        Paragraph('<b>№</b>', table_text_style),
        Paragraph('<b>Товар</b>', table_text_style),
        Paragraph('<b>Кол-во</b>', table_text_style),
        Paragraph('<b>Цена</b>', table_text_style),
        Paragraph('<b>Сумма</b>', table_text_style),
    ]]

    for i, item in enumerate(receipt['items'], 1):
        data.append([
            Paragraph(str(i), table_text_style),
            Paragraph(str(item['name']), table_text_style),
            Paragraph(f"{item['quantity']} {item['unit']}", table_text_style),
            Paragraph(f"{item['price']:.2f} ₽", table_text_style),
            Paragraph(f"{item['total']:.2f} ₽", table_text_style),
        ])

    data.append([
        '',
        '',
        '',
        Paragraph('<b>ПОДЫТОГ:</b>', table_text_style),
        Paragraph(f"<b>{receipt['subtotal']:.2f} ₽</b>", table_text_style),
    ])

    if receipt.get('discount', 0) > 0:
        data.append([
            '',
            '',
            '',
            Paragraph('<b>СКИДКА:</b>', table_text_style),
            Paragraph(f"<b>-{receipt['discount']:.2f} ₽</b>", table_text_style),
        ])

    data.append([
        '',
        '',
        '',
        Paragraph('<b>ИТОГО:</b>', table_text_style),
        Paragraph(f"<b>{receipt['total']:.2f} ₽</b>", table_text_style),
    ])

    table = Table(data, colWidths=[20, 220, 70, 70, 80])
    table.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), regular_font),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e9ecef')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.black),
        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('ALIGN', (2, 1), (-1, -1), 'RIGHT'),
        ('BACKGROUND', (0, -1), (-1, -1), colors.HexColor('#f8f9fa')),
    ]))

    story.append(table)
    story.append(Spacer(1, 12))
    story.append(Paragraph("Спасибо за покупку!", info_style))

    doc.build(story)

    pdf = buffer.getvalue()
    buffer.close()

    response = HttpResponse(pdf, content_type='application/pdf')
    filename = receipt["date"].replace(" ", "_").replace(":", "-")
    response['Content-Disposition'] = f'attachment; filename="receipt_{filename}.pdf"'
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

        try:
            quantity = parse_decimal_quantity(quantity_str)
            validate_quantity_for_product(product, quantity)
        except ValueError as e:
            messages.error(request, str(e))
            return redirect('scan_qr')

        if action == 'add_to_cart':
            if product.quantity < quantity:
                messages.error(request, f'Недостаточно товара "{product.name}" на складе')
                return redirect('sale')

            if product.expiration_date < date.today():
                messages.error(request, f'Товар "{product.name}" просрочен и не может быть продан')
                return redirect('sale')

            cart = request.session.get('cart', {})
            product_id_str = str(product.id)

            current_quantity = Decimal(str(cart.get(product_id_str, {}).get('quantity', 0)))
            new_quantity = current_quantity + quantity

            if new_quantity > product.quantity:
                messages.error(request, f'Нельзя добавить больше, чем есть на складе')
                return redirect('sale')

            cart[product_id_str] = {
                'quantity': float(new_quantity) if product.unit == 'kg' else int(new_quantity),
                'price': str(product.price),
                'unit': product.unit
            }

            request.session['cart'] = cart
            request.session.modified = True

            qty_display = f"{float(quantity):.3f}" if product.unit == 'kg' else f"{int(quantity)}"
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

            for item in sale_items:
                product = Product.objects.select_for_update().get(pk=item['product_id'])
                quantity = parse_decimal_quantity(item['quantity'])
                validate_quantity_for_product(product, quantity)

                if product.quantity < quantity:
                    raise ValueError(
                        f'Количество товара "{product.name}" изменилось. '
                        f'Требуется: {quantity}, доступно: {product.quantity}'
                    )

                if product.expiration_date < date.today():
                    raise ValueError(f'Товар "{product.name}" просрочен и не может быть продан')

                locked_products[product.id] = (product, quantity)

            for item in sale_items:
                product, quantity = locked_products[item['product_id']]
                product.quantity -= quantity
                product.save(update_fields=['quantity', 'updated_at', 'qr_code'])

                History.objects.create(
                    type='sale',
                    product=product,
                    quantity=quantity,
                    total_price=Decimal(str(item['total'])),
                    user=request.user,
                    coupon=coupon,
                    sale_group=uuid.UUID(sale_group_id)
                )

            if coupon:
                coupon.used_count += 1
                coupon.save(update_fields=['used_count', 'updated_at'])

            products_for_receipt = []
            for item in sale_items:
                product, quantity = locked_products[item['product_id']]
                products_for_receipt.append({
                    'name': product.name,
                    'quantity': float(quantity) if product.unit == 'kg' else int(quantity),
                    'price': item['price'],
                    'total': item['total'],
                    'unit': item['unit']
                })

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
