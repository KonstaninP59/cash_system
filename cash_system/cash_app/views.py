from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db import transaction
from django.utils import timezone
from datetime import date, timedelta
from .models import Product, History
from .forms import LoginForm, ProductForm, SaleForm

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
                    # Поступление
                    History.objects.create(
                        type='receipt',
                        product=new_product,
                        quantity=new_product.quantity - old_quantity,
                        user=request.user
                    )
                else:
                    # Утилизация (уменьшение количества)
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
                for product_id, item in cart.items():
                    product = Product.objects.select_for_update().get(pk=product_id)
                    quantity = item['quantity']
                    
                    if product.quantity < quantity:
                        raise ValueError(f'Недостаточно товара "{product.name}" на складе')
                    
                    # Уменьшаем количество
                    product.quantity -= quantity
                    product.save()
                    
                    # Создаем запись в истории
                    History.objects.create(
                        type='sale',
                        product=product,
                        quantity=quantity,
                        user=request.user
                    )
                
                # Очищаем корзину
                request.session['cart'] = {}
                request.session.modified = True
                
                messages.success(request, 'Продажа успешно оформлена!')
                
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
