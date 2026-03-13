from django.contrib.auth.models import User
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib import messages
from django.db import transaction
from django.db.models import Q, Sum, Count
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from datetime import date, timedelta, datetime
from .models import Product, History, SalesPlan
from .forms import LoginForm, ProductForm, SaleForm, BarcodeForm, DisposalForm, SalesPlanForm
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from io import BytesIO
import json
import calendar


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
    """Список товаров с фильтрацией по статусу"""
    # Получаем параметр фильтра из URL
    filter_type = request.GET.get('filter', 'all')
    
    # Базовый запрос
    products = Product.objects.all()
    
    # Применяем фильтр в зависимости от параметра
    if filter_type == 'expired':
        products = [p for p in products if p.is_expired()]
        filter_title = "Просроченные товары"
    elif filter_type == 'expiring_soon':
        products = [p for p in products if p.is_expiring_soon()]
        filter_title = "Товары с истекающим сроком годности"
    else:
        filter_type = 'all'
        filter_title = "Все товары"
    
    # Подсчет статистики (для всех товаров, без фильтра)
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
    }
    return render(request, 'cash_app/product_list.html', context)

@login_required
def product_create(request):
    """Создание товара"""
    if request.method == 'POST':
        form = ProductForm(request.POST)
        if form.is_valid():
            product = form.save()
            
            # Создаем запись в истории о поступлении
            if product.quantity > 0:
                History.objects.create(
                    type='receipt',
                    product=product,
                    quantity=product.quantity,
                    user=request.user
                )
            
            messages.success(request, 'Товар успешно добавлен!')
            return redirect('product_list')
    else:
        # Проверяем, есть ли временный штрих-код из формы добавления по штрих-коду
        temp_barcode = request.session.get('temp_barcode')
        if temp_barcode:
            form = ProductForm(initial={'barcode': temp_barcode})
            # Очищаем временный штрих-код из сессии
            del request.session['temp_barcode']
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
    """История движений товаров"""
    history = History.objects.all().select_related('product', 'user')
    
    # Фильтрация по типу
    type_filter = request.GET.get('type')
    if type_filter and type_filter in dict(History.TYPE_CHOICES).keys():
        history = history.filter(type=type_filter)
    
    # Фильтрация по товару
    product_filter = request.GET.get('product')
    if product_filter:
        history = history.filter(product_id=product_filter)
    
    context = {
        'history': history,
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
        
        try:
            with transaction.atomic():
                sale_items = []
                total_amount = 0
                
                for product_id, item in cart.items():
                    product = Product.objects.select_for_update().get(pk=product_id)
                    quantity = item['quantity']
                    
                    if product.quantity < quantity:
                        raise ValueError(f'Недостаточно товара "{product.name}" на складе')
                    
                    # Уменьшаем количество
                    product.quantity -= quantity
                    product.save()
                    
                    # Считаем сумму
                    item_total = product.price * quantity
                    total_amount += item_total
                    
                    # Создаем запись в истории с суммой
                    history = History.objects.create(
                        type='sale',
                        product=product,
                        quantity=quantity,
                        total_price=item_total,
                        user=request.user
                    )
                    
                    sale_items.append({
                        'product': product,
                        'quantity': quantity,
                        'price': product.price,
                        'total': item_total,
                        'unit': product.get_unit_display()
                    })
                
                # Сохраняем данные чека в сессию для PDF
                request.session['last_receipt'] = {
                    'items': [
                        {
                            'name': item['product'].name,
                            'quantity': item['quantity'],
                            'price': float(item['price']),
                            'total': float(item['total']),
                            'unit': item['unit']
                        } for item in sale_items
                    ],
                    'total': float(total_amount),
                    'date': timezone.now().strftime('%d.%m.%Y %H:%M'),
                    'cashier': request.user.username
                }
                
                # Очищаем корзину
                request.session['cart'] = {}
                request.session.modified = True
                
                messages.success(request, 'Продажа успешно оформлена!')
                
                # Перенаправляем на страницу с чеком
                return redirect('receipt_view')
                
        except Exception as e:
            messages.error(request, f'Ошибка при оформлении продажи: {str(e)}')
        
        return redirect('sale')
    
    # GET запрос - отображаем страницу продажи
    products = Product.objects.filter(quantity__gt=0).exclude(expiration_date__lt=date.today())
    cart = request.session.get('cart', {})
    
    # Получаем товары в корзине
    cart_items = []
    total = 0
    
    for product_id, item in cart.items():
        try:
            product = Product.objects.get(pk=product_id)
            subtotal = product.price * item['quantity']
            total += subtotal
            cart_items.append({
                'product': product,
                'quantity': item['quantity'],
                'subtotal': subtotal
            })
        except Product.DoesNotExist:
            continue
    
    context = {
        'products': products,
        'cart_items': cart_items,
        'total': total,
    }
    return render(request, 'cash_app/sale.html', context)

@login_required
def add_to_cart(request):
    """Добавление товара в корзину"""
    if request.method == 'POST':
        product_id = request.POST.get('product_id')
        quantity = int(request.POST.get('quantity', 1))
        
        product = get_object_or_404(Product, pk=product_id)
        
        if product.quantity < quantity:
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
                'price': str(product.price)
            }
        
        request.session['cart'] = cart
        request.session.modified = True
        
        messages.success(request, f'Товар "{product.name}" добавлен в корзину')
    
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
    request.session.modified = True
    messages.success(request, 'Корзина очищена')
    return redirect('sale')

@login_required
def barcode_add(request):
    """Добавление товара по штрих-коду"""
    if request.method == 'POST':
        form = BarcodeForm(request.POST)
        if form.is_valid():
            barcode = form.cleaned_data['barcode']
            quantity = form.cleaned_data['quantity']
            
            try:
                # Ищем товар по штрих-коду
                product = Product.objects.get(barcode=barcode)
                
                # Проверяем наличие
                if product.quantity < quantity:
                    messages.error(request, f'Недостаточно товара "{product.name}" на складе')
                    return redirect('barcode_add')
                
                if product.expiration_date < date.today():
                    messages.error(request, f'Товар "{product.name}" просрочен')
                    return redirect('barcode_add')
                
                # Добавляем в корзину
                cart = request.session.get('cart', {})
                product_id = str(product.id)
                
                if product_id in cart:
                    cart[product_id]['quantity'] += quantity
                else:
                    cart[product_id] = {
                        'quantity': quantity,
                        'price': str(product.price)
                    }
                
                request.session['cart'] = cart
                request.session.modified = True
                
                messages.success(request, f'Товар "{product.name}" добавлен в корзину')
                return redirect('sale')
                
            except Product.DoesNotExist:
                # Если товар не найден, предлагаем создать
                request.session['temp_barcode'] = barcode
                messages.warning(request, f'Товар со штрих-кодом {barcode} не найден. Создайте новый товар.')
                return redirect('product_create')
    else:
        form = BarcodeForm()
    
    return render(request, 'cash_app/barcode.html', {'form': form})

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
        ('BACKGROUND', (0, 1), (-1, -2), colors.beige),
        ('GRID', (0, 0), (-1, -2), 1, colors.black),
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
    """Дашборд эффективности"""
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
            'daily_data': json.dumps(daily_data),
            'labels': json.dumps(labels),
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
        
        # Количество продаж
        sales_count = History.objects.filter(
            type='sale',
            date__gte=start_of_month
        ).count()
        
        # Топ-3 продавца
        top_sellers = []
        for user in User.objects.filter(is_staff=False, is_active=True):
            user_sales = History.objects.filter(
                type='sale',
                user=user,
                date__gte=start_of_month
            ).aggregate(total=Sum('total_price'))['total'] or 0
            
            try:
                plan = SalesPlan.objects.get(user=user)
                plan_amount = plan.monthly_target
                completion = plan.get_completion_percentage()
            except SalesPlan.DoesNotExist:
                plan_amount = 0
                completion = 0
            
            top_sellers.append({
                'user': user,
                'sales': user_sales,
                'plan': plan_amount,
                'completion': completion
            })
        
        top_sellers = sorted(top_sellers, key=lambda x: x['sales'], reverse=True)[:3]
        
        # Статистика по планам
        users_with_plans = SalesPlan.objects.all().select_related('user')
        
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
        
        context = {
            'total_monthly_sales': total_monthly_sales,
            'total_yearly_sales': total_yearly_sales,
            'sales_count': sales_count,
            'average_check': total_monthly_sales / sales_count if sales_count > 0 else 0,
            'top_sellers': top_sellers,
            'users_with_plans': users_with_plans,
            'daily_data': json.dumps(daily_data),
            'labels': json.dumps(labels),
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
    ).select_related('product').order_by('-date')
    
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
