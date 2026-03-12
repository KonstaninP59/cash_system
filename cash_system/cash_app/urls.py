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
    
    # История
    path('history/', views.history_list, name='history_list'),
    
    # Продажи
    path('sale/', views.sale_view, name='sale'),
    path('sale/add/', views.add_to_cart, name='add_to_cart'),
    path('sale/remove/<int:product_id>/', views.remove_from_cart, name='remove_from_cart'),
    path('sale/clear/', views.clear_cart, name='clear_cart'),
]
