from django.contrib import admin
from django.db import models
from .models import (
    Item, Sale, UserProfile, Category, Brand, 
    Payment, AuditLog, Location, StockRecord, StockTransfer,
)

# ----------------------------
# 1. Base Multi-Tenant Admin
# ----------------------------
class OwnerScopedAdmin(admin.ModelAdmin):
    """
    Base class to ensure users only see and edit their own data.
    """
    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if request.user.is_superuser:
            return qs
        return qs.filter(owner=request.user)

    def save_model(self, request, obj, form, change):
        if not obj.pk:
            obj.owner = request.user
        super().save_model(request, obj, form, change)

# ----------------------------
# 2. Optimized Registrations
# ----------------------------

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "role", "default_location", "parent_admin", "can_edit_daily_summary")
    list_filter = ("role", "parent_admin", "can_edit_daily_summary")

@admin.register(Location)
class LocationAdmin(OwnerScopedAdmin):
    list_display = ("name", "country_code", "is_warehouse")
    list_filter = ("country_code", "is_warehouse")

@admin.register(Category)
class CategoryAdmin(OwnerScopedAdmin):
    list_display = ("name", "owner")

@admin.register(Brand)
class BrandAdmin(OwnerScopedAdmin):
    list_display = ("name", "owner")

@admin.register(Payment)
class PaymentAdmin(OwnerScopedAdmin):
    list_display = ("order_id", "amount", "status", "provider")
    list_filter = ("status", "provider")

@admin.register(StockRecord)
class StockRecordAdmin(OwnerScopedAdmin):
    list_display = ("item", "location", "quantity")
    list_filter = ("location",)

@admin.register(StockTransfer)
class StockTransferAdmin(OwnerScopedAdmin):
    list_display = ("from_location", "to_location", "status")
    list_filter = ("status",)

@admin.register(Sale)
class SaleAdmin(OwnerScopedAdmin):
    list_display = ("id", "timestamp", "tender", "total_price", "location")
    list_filter = ("tender", "timestamp", "location")
    date_hierarchy = "timestamp"
    readonly_fields = ("subtotal", "gct_amount", "total_price", "last_modified")

@admin.register(AuditLog)
class AuditLogAdmin(OwnerScopedAdmin):
    list_display = ("created_at", "user", "action", "severity", "message")
    list_filter = ("action", "severity", "created_at")
    search_fields = ("message", "user__username")

@admin.register(Item)
class ItemAdmin(OwnerScopedAdmin):
    # Use the property method name 'total_global_quantity' instead of 'quantity'
    list_display = ("name", "total_global_quantity", "price") 
    search_fields = ("name", "sku", "barcode")
    list_filter = ("status", "category", "brand")
    readonly_fields = ("sync_token", "last_modified")

    # If you want to explicitly define a column header and make it sortable, 
    # you can add this method:
    @admin.display(description='Total Quantity')
    def total_global_quantity(self, obj):
        return obj.total_global_quantity
