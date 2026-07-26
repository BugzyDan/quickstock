from django.urls import path
from api import views

urlpatterns = [
    path('login/', views.api_login, name='api_login'),
    path('profile/', views.api_profile, name='api_profile'),
    path('users/', views.api_users, name='api_users'),
    path('health/', views.health_check, name='api_health'),
    path('inventory/', views.sync_pull_inventory, name='api_inventory_pull'),
    path('inventory/push/', views.sync_push_inventory, name='api_inventory_push'),
    path('sales/', views.sync_pull_sales, name='api_sales_pull'),
    path('sales/push/', views.sync_push_sales, name='api_sales_push'),
    path('sync/bidirectional/', views.sync_bidirectional, name='api_sync_bidirectional'),
    path('sync/reference/', views.sync_reference_data, name='api_sync_reference'),
    path('sync/status/', views.sync_status, name='api_sync_status'),
]
