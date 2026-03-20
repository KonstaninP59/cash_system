from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    
    # Товары
    path('products/', views.product_list, name='product_list'),
    path('products/create/', views.product_create, name='product_create'),
    path('products/<int:pk>/update/', views.product_update, name='product_update'),
    path('products/<int:pk>/delete/', views.product_delete, name='product_delete'),
    path('products/<int:pk>/disposal/', views.product_disposal, name='product_disposal'),
    
    # QR-коды (вместо штрих-кодов)
    path('scan-qr/', views.scan_qr, name='scan_qr'),
    path('scan-qr/result/', views.scan_qr_result, name='scan_qr_result'),
    path('scan-qr/upload/', views.scan_qr_upload, name='scan_qr_upload'),
    path('scan-qr/process/', views.process_qr_action, name='process_qr_action'),
    path('product/<int:product_id>/generate-qr/', views.generate_product_qr, name='generate_product_qr'),
    path('product/<int:product_id>/download-qr/', views.download_product_qr, name='download_product_qr'),
    path('product/<int:product_id>/print-qr/', views.print_product_qr, name='print_product_qr'),
    
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
    
    # Дашборд эффективности
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('dashboard/plan/<int:user_id>/edit/', views.plan_edit, name='plan_edit'),
    path('dashboard/user/<int:user_id>/sales/', views.user_sales_detail, name='user_sales_detail'),
    
    # Купоны (только для администратора)
    path('coupons/', views.coupon_list, name='coupon_list'),
    path('coupons/create/', views.coupon_create, name='coupon_create'),
    path('coupons/<int:pk>/edit/', views.coupon_edit, name='coupon_edit'),
    path('coupons/<int:pk>/delete/', views.coupon_delete, name='coupon_delete'),
    
    # Категории (только для администратора)
    path('categories/', views.category_list, name='category_list'),
    path('categories/create/', views.category_create, name='category_create'),
    path('categories/<int:pk>/edit/', views.category_edit, name='category_edit'),
    path('categories/<int:pk>/delete/', views.category_delete, name='category_delete'),
]
