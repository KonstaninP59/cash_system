from django.urls import path
from . import views

urlpatterns = [
    path('sale/select-price-list/', views.select_price_list, name='select_price_list'),
    # Базовые страницы
    path('', views.index, name='index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),

    # Товары
    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.product_create, name='product_create'),
    path('products/<int:pk>/update/', views.product_update, name='product_update'),
    path('products/<int:pk>/delete/', views.product_delete, name='product_delete'),
    path('products/<int:pk>/disposal/', views.product_disposal, name='product_disposal'),

    # QR-коды
    path('scan-qr/', views.scan_qr, name='scan_qr'),
    path('scan-qr/result/', views.scan_qr_result, name='scan_qr_result'),
    path('scan-qr/upload/', views.scan_qr_upload, name='scan_qr_upload'),
    path('scan-qr/process/', views.process_qr_action, name='process_qr_action'),
    path('products/<int:product_id>/generate-qr/', views.generate_product_qr, name='generate_product_qr'),
    path('products/<int:product_id>/download-qr/', views.download_product_qr, name='download_product_qr'),
    path('products/<int:product_id>/print-qr/', views.print_product_qr, name='print_product_qr'),

    # История
    path('history/', views.history_list, name='history_list'),

    # Продажи
    path('sale/', views.sale_view, name='sale'),
    path('sale/add/', views.add_to_cart, name='add_to_cart'),
    path('sale/remove/<int:product_id>/', views.remove_from_cart, name='remove_from_cart'),
    path('sale/clear/', views.clear_cart, name='clear_cart'),
    path('sale/apply-coupon/', views.apply_coupon, name='apply_coupon'),
    path('sale/remove-coupon/', views.remove_coupon, name='remove_coupon'),
    path('sale/save-collapsed/', views.save_collapsed_categories, name='save_collapsed'),

    # Чек
    path('receipt/', views.receipt_view, name='receipt_view'),
    path('receipt/pdf/', views.receipt_pdf, name='receipt_pdf'),

    # Дашборд
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('dashboard/plan/<int:user_id>/edit/', views.plan_edit, name='plan_edit'),
    path('dashboard/user/<int:user_id>/sales/', views.user_sales_detail, name='user_sales_detail'),

    # Купоны
    path('coupons/', views.coupon_list, name='coupon_list'),
    path('coupons/create/', views.coupon_create, name='coupon_create'),
    path('coupons/<int:pk>/edit/', views.coupon_edit, name='coupon_edit'),
    path('coupons/<int:pk>/delete/', views.coupon_delete, name='coupon_delete'),

    # Категории
    path('categories/', views.category_list, name='category_list'),
    path('categories/create/', views.category_create, name='category_create'),
    path('categories/<int:pk>/edit/', views.category_edit, name='category_edit'),
    path('categories/<int:pk>/delete/', views.category_delete, name='category_delete'),

    # Оплата
    path('payment/page/', views.payment_page, name='payment_page'),
    path('payment/process/', views.process_payment, name='process_payment'),
    path('payment/success/', views.payment_success, name='payment_success'),
    path('payment/status/', views.terminal_status, name='terminal_status'),
    path('payment/callback/', views.payment_callback, name='payment_callback'),

    # Прайс-листы (только для администратора)
    path('price-lists/', views.price_list_list, name='price_list_list'),
    path('price-lists/create/', views.price_list_create, name='price_list_create'),
    path('price-lists/<int:pk>/edit/', views.price_list_edit, name='price_list_edit'),
    path('price-lists/<int:pk>/delete/', views.price_list_delete, name='price_list_delete'),

    # Управление пользователями (только для администратора)
    path('users/', views.user_list, name='user_list'),
    path('users/create/', views.create_user, name='create_user'),
    path('store/select/', views.select_store, name='select_store'),

    # Управление складами
    path('stores/', views.store_list, name='store_list'),
    path('stores/create/', views.store_create, name='store_create'),
    path('stores/<int:pk>/edit/', views.store_edit, name='store_edit'),
    path('stores/<int:pk>/delete/', views.store_delete, name='store_delete'),

    path('products/<int:product_id>/add-store-quantity/', views.add_store_quantity, name='add_store_quantity'),
    path('api/store-product/<int:store_id>/<int:product_id>/', views.api_store_product, name='api_store_product'),

    # Зарплата
    path('salary/settings/', views.salary_settings_list, name='salary_settings_list'),
    path('salary/settings/create/<int:user_id>/', views.salary_settings_create, name='salary_settings_create'),
    path('salary/settings/edit/<int:pk>/', views.salary_settings_edit, name='salary_settings_edit'),
    path('salary/calculations/', views.salary_calculations, name='salary_calculations'),
    path('salary/calculate/<int:year>/<int:month>/', views.salary_calculate, name='salary_calculate'),
    path('salary/detail/<int:pk>/', views.salary_detail, name='salary_detail'),
    path('salary/mark-paid/<int:pk>/', views.salary_mark_paid, name='salary_mark_paid'),

    # Дашборд по складам
    path('store-dashboard/', views.store_dashboard, name='store_dashboard'),

    # Дашборд прибыли
    path('profit-dashboard/', views.profit_dashboard, name='profit_dashboard'),

    # Рецепты
    path('recipes/', views.recipe_list, name='recipe_list'),
    path('recipes/create/<int:product_id>/', views.recipe_create, name='recipe_create'),
    path('recipes/<int:pk>/edit/', views.recipe_edit, name='recipe_edit'),
    path('recipes/<int:pk>/delete/', views.recipe_delete, name='recipe_delete'),
]
