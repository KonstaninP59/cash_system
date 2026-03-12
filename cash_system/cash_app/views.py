from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from datetime import date, timedelta
from .models import Product, History
from .forms import LoginForm, ProductForm, SaleForm, BarcodeForm
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm
from io import BytesIO
import json

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
    """Список товаров"""
    products = Product.objects.all()
    
    # Подсчет статистики
    total_products = products.count()
    expired_count = sum(1 for p in products if p.is_expired())
    expiring_soon_count = sum(1 for p in products if p.is_expiring_soon())
    
    context = {
        'products': products,
        'total_products': total_products,
        'expired_count': expired_count,
        'expiring_soon_count': expiring_soon_count,
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
