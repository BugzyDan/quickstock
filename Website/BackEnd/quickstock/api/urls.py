from django.urls import path
from api import views

urlpatterns = [
    path('login/', views.api_login, name='api_login'),
    path('profile/', views.api_profile, name='api_profile'),
    path('users/', views.api_users, name='api_users'),
    path('health/', views.health_check, name='api_health'),
    path('inventory/', views.sync_pull_inventory, name='api_inventory_pull'),
    path('inventory/push/', views.sync_push_inventory, name='api_inventory_push'),
    path('inventory/<str:sku>/', views.desktop_delete_inventory_item, name='api_inventory_item'),
    path('sales/', views.sync_pull_sales, name='api_sales_pull'),
    path('sales/push/', views.sync_push_sales, name='api_sales_push'),
    path('sync/bidirectional/', views.sync_bidirectional, name='api_sync_bidirectional'),
    path('sync/reference/', views.sync_reference_data, name='api_sync_reference'),
    path('sync/status/', views.sync_status, name='api_sync_status'),
    path("register/open/", views.desktop_open_register, name="desktop_open_register"),
    path("register/close/", views.desktop_close_register, name="desktop_close_register"),
    path("categories/", views.desktop_create_category, name="desktop_create_category"),
    path("suppliers/", views.desktop_create_supplier, name="desktop_create_supplier"),
    path("receive-stock/", views.desktop_receive_stock, name="desktop_receive_stock"),
    path("transfer-stock/", views.desktop_transfer_stock, name="desktop_transfer_stock"),
]
