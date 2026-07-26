#!/usr/bin/env python
"""
Script to safely clear all users from the QuickStock database.
This will remove:
- All User records (auth_user table)
- All UserProfile records (inventory_userprofile table)
- All related data (locations, suppliers, items, etc.) if they belong to users

WARNING: This is a DESTRUCTIVE operation. Make sure you have backups if needed.
"""

import os
import sys
import django
import json

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'quickstock.settings')

# Add the project root to Python path
base_dir = os.path.dirname(os.path.abspath(__file__))
project_path = os.path.join(base_dir, 'Website', 'BackEnd', 'quickstock')
sys.path.insert(0, project_path)

# Configure Django
django.setup()

from django.contrib.auth.models import User
from inventory.models import (
    UserProfile,
    Location,
    Supplier,
    Item,
    Category,
    Brand,
    Sale,
    SalesInvoice,
    SalesInvoicePayment,
    SalesInvoiceCreditNote,
    CustomerCreditMovement,
    SupplierInvoice,
    SupplierInvoicePayment,
    SupplierInvoiceAdjustment,
    SupplierInvoiceRefund,
    PurchaseOrder,
)

def clear_all_users():
    """Safely clear all users and related data."""
    
    print("=" * 60)
    print("QUICKSTOCK USER CLEARING UTILITY")
    print("=" * 60)
    print()
    
    # Show current statistics
    user_count = User.objects.count()
    profile_count = UserProfile.objects.count()
    location_count = Location.objects.count()
    supplier_count = Supplier.objects.count()
    item_count = Item.objects.count()
    
    print(f"📊 Current Database Statistics:")
    print(f"   • Users: {user_count}")
    print(f"   • User Profiles: {profile_count}")
    print(f"   • Locations: {location_count}")
    print(f"   • Suppliers: {supplier_count}")
    print(f"   • Inventory Items: {item_count}")
    print()
    
    if user_count == 0:
        print("✅ No users found in database. Nothing to clear.")
        return
    
    # Confirmation
    print("⚠️  WARNING: This will PERMANENTLY DELETE all users and their data!")
    response = input("Are you sure you want to proceed? (yes/no): ").strip().lower()
    
    if response not in ['yes', 'y']:
        print("❌ Operation cancelled by user.")
        return

    financial_history = {
        "sales": Sale.objects.count(),
        "sales_invoices": SalesInvoice.objects.count(),
        "sales_payments": SalesInvoicePayment.objects.count(),
        "sales_credit_notes": SalesInvoiceCreditNote.objects.count(),
        "customer_credit_movements": CustomerCreditMovement.objects.count(),
        "supplier_invoices": SupplierInvoice.objects.count(),
        "supplier_payments": SupplierInvoicePayment.objects.count(),
        "supplier_adjustments": SupplierInvoiceAdjustment.objects.count(),
        "supplier_refunds": SupplierInvoiceRefund.objects.count(),
        "purchase_orders": PurchaseOrder.objects.count(),
    }
    if any(financial_history.values()):
        print(json.dumps({
            "ok": False,
            "code": "financial_history_protected",
            "message": "User purge blocked; archive accounts so financial history survives.",
            "history": financial_history,
        }, indent=2, default=str))
        return

    print()
    print("🗑️  Clearing users...")
    
    # Explicitly clear protected transactional data first
    # This prevents the ProtectedError when User.delete() is called
    Sale.objects.all().delete()
    SalesInvoice.objects.all().delete()

    try:
        # Delete all users (this will cascade delete related data)
        deleted_count = 0

        # Delete all users. This will trigger the pre_delete signal for each user,
        # which handles cleanup of related models, and UserProfile will cascade.
        users_deleted = User.objects.all().delete()
        deleted_count += users_deleted[0]
        print(f"   • Deleted {users_deleted[0]} users")
        
        # Clean up any orphaned data that might not have been cascade deleted
        # (This is just to be extra safe)
        orphaned_locations = Location.objects.filter(owner__isnull=True).delete()
        if orphaned_locations[0] > 0:
            print(f"   • Deleted {orphaned_locations[0]} orphaned locations")
        
        orphaned_suppliers = Supplier.objects.filter(owner__isnull=True).delete()
        if orphaned_suppliers[0] > 0:
            print(f"   • Deleted {orphaned_suppliers[0]} orphaned suppliers")
        
        orphaned_items = Item.objects.filter(owner__isnull=True).delete()
        if orphaned_items[0] > 0:
            print(f"   • Deleted {orphaned_items[0]} orphaned items")
        
        print()
        print("✅ Successfully cleared all users and related data!")
        print()
        
        # Show final statistics
        print("📊 Final Database Statistics:")
        print(f"   • Users: {User.objects.count()}")
        print(f"   • User Profiles: {UserProfile.objects.count()}")
        print(f"   • Locations: {Location.objects.count()}")
        print(f"   • Suppliers: {Supplier.objects.count()}")
        print(f"   • Inventory Items: {Item.objects.count()}")
        
    except Exception as e:
        print(f"❌ Error occurred: {e}")
        print("Please check the database connection and try again.")
        return

def create_test_user():
    """Optionally create a test admin user."""
    print()
    response = input("Would you like to create a new admin user? (yes/no): ").strip().lower()
    
    if response not in ['yes', 'y']:
        print("👋 Done!")
        return
    
    print()
    print("📝 Create New Admin User")
    print("-" * 30)
    
    username = input("Username: ").strip()
    email = input("Email (optional): ").strip()
    password = input("Password: ").strip()
    confirm_password = input("Confirm Password: ").strip()
    
    if password != confirm_password:
        print("❌ Passwords do not match!")
        return
    
    if not username:
        print("❌ Username cannot be empty!")
        return
    
    try:
        # Create the user
        user = User.objects.create_user(
            username=username,
            email=email if email else None,
            password=password
        )
        
        # The UserProfile is automatically created via the signal in signals.py
        # We simply fetch and update the necessary fields.
        profile = UserProfile.objects.get(user=user)
        profile.role = 'admin'
        profile.status = 'active'
        profile.save()
        
        print()
        print(f"✅ Successfully created admin user '{username}'!")
        print("   You can now log in with these credentials.")
        
    except Exception as e:
        print(f"❌ Failed to create user: {e}")

if __name__ == "__main__":
    try:
        clear_all_users()
        create_test_user()
    except KeyboardInterrupt:
        print("\n\n❌ Operation interrupted by user.")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
