# quickstock/inventory/urls.py
from django.urls import path, include
from django.contrib.auth import views as auth_views
from . import views




urlpatterns = [
    # ---------------------------
    # Authentication
    # ---------------------------
    
    path('', views.index, name='index'),
    path('login/', views.login_view, name='login'),
    path('login/social/<str:provider>/', views.social_login_start, name='social_login_start'),
    path('login/social/<str:provider>/callback/', views.social_login_callback, name='social_login_callback'),
    path('logout/', views.logout_view, name='logout'),
    path('session/heartbeat/', views.session_heartbeat, name='session_heartbeat'),
    path('session/expire/', views.expire_session, name='expire_session'),
    path('signup/', views.signup_view, name='signup'),
    path('activate/<uidb64>/<token>/', views.activate_account, name='activate'),

    # ---------------------------
    # Locations / Network
    # ---------------------------
    path('locations/', views.locations_page, name='locations'),
    path('network/add/', views.add_location, name='add_location'),
    path('network/<int:location_id>/delete/', views.delete_location, name='delete_location'),


    # ---------------------------
    # Operations
    # ---------------------------
    path('operations/', views.operations, name='operations'),
    path('daily_summary/', views.daily_summary, name='daily_summary'),
    path('daily_summary/<str:date>/', views.daily_summary, name='daily_summary_date'),
    path('cash-reconciliation/', views.cash_reconciliation, name='cash_reconciliation'),

    path('cash-reconciliation/<str:date>/', views.cash_reconciliation, name='cash_reconciliation_date'),
    path('deliveries_collections/', views.deliveries_collections, name='deliveries_collections'),

    # ---------------------------
    # Dashboards
    # ---------------------------
    
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('search/', views.global_search, name='global_search'),
    path('search/panel/', views.global_search_panel_api, name='global_search_panel_api'),
    path('login-redirect/', views.login_redirect, name='login_redirect'),
    path('admin-dashboard/', views.admin_dashboard, name='admin_dashboard'),
    path('super-admin-dashboard/', views.super_admin_dashboard, name='super_admin_dashboard'),
    path('manager-dashboard/', views.manager_dashboard, name='manager_dashboard'),
    path('cashier-dashboard/', views.cashier_dashboard, name='cashier_dashboard'),

    # ---------------------------
    # Password & Profile
    # ---------------------------
    path('profile/', views.profile_view, name='profile'),
    path('change-password/', views.change_password_view, name='change_password'),
    path('delete-account/', views.delete_account, name='delete_account'),
    # ---------------------------
    # Password Reset (Account Recovery)
    # ---------------------------
    path('password-reset/', auth_views.PasswordResetView.as_view(
        template_name='inventory/password_reset_form.html',
        subject_template_name='inventory/password_reset_subject.txt',
        email_template_name='inventory/password_reset_email.html'
    ), name='password_reset'),

    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(
        template_name='inventory/password_reset_done.html'
    ), name='password_reset_done'),

    path('password-reset-confirm/<uidb64>/<token>/', auth_views.PasswordResetConfirmView.as_view(
        template_name='inventory/password_reset_confirm.html'
    ), name='password_reset_confirm'),

    path('password-reset-complete/', auth_views.PasswordResetCompleteView.as_view(
        template_name='inventory/password_reset_complete.html'
    ), name='password_reset_complete'),

    # ---------------------------
    # Inventory Management
    # ---------------------------
    path('inventory/', views.inventory_view, name='inventory'),
    path('inventory-view/', views.inventory_view, name='inventory_view'),
    path('inventory/overview/', views.inventory_overview_view, name='inventory_overview'),
    path('inventory/add/', views.inventory_add_view, name='inventory_add'),
    path('inventory/<int:item_id>/update/', views.inventory_update_view, name='inventory_update'),
    path('inventory/<int:item_id>/delete/', views.inventory_delete_view, name='inventory_delete'),
    path('legacy-api/inventory/', views.inventory_api, name='api_inventory'),
    path('legacy-api/locations/', views.api_locations, name='api_locations'),
    path('legacy-api/sales/', views.api_sales, name='api_sales'),
    path('legacy-api/transfer-stock/', views.api_transfer_stock, name='api_transfer_stock'),
    path('receive-stock/', views.receive_stock, name='receive_stock'),
    path('transfer/', views.transfer_stock_view, name='transfer_stock'),
    # Look for a line like this in urls.py
    path('customers/', views.customer_list, name='customer_list'),
    path('customers/add/', views.add_customer, name='add_customer'),
    path('customers/<int:pk>/', views.customer_detail, name='customer_detail'),
    path('customers/<int:pk>/edit/', views.edit_customer, name='edit_customer'),
    path('customers/<int:pk>/delete/', views.delete_customer, name='delete_customer'),

    # ---------------------------
    # Suppliers & Purchase Orders
    # ---------------------------
    path('suppliers/', views.supplier_list, name='supplier_list'),
    path('suppliers/add/', views.supplier_add, name='supplier_add'),
    path('suppliers/<int:pk>/edit/', views.supplier_edit, name='supplier_edit'),
    path('supplier/delete/<int:pk>/', views.supplier_delete, name='supplier_delete'),
    path('supplier/ledger/<int:supplier_id>/', views.supplier_ledger, name='supplier_ledger'),
    path('supplier/<int:supplier_id>/add-invoice/', views.add_invoice, name='add_invoice'),
    path('invoice/<int:invoice_id>/', views.invoice_detail, name='invoice_detail'),
    path('invoice/<int:invoice_id>/pay/', views.mark_invoice_paid, name='mark_invoice_paid'),
    path('invoice/<int:invoice_id>/undo-payment/', views.undo_invoice_payment, name='undo_invoice_payment'),
    path(
        'invoice/<int:invoice_id>/payment/<int:payment_id>/reverse/',
        views.reverse_supplier_invoice_payment,
        name='reverse_supplier_invoice_payment',
    ),
    path('invoice/<int:invoice_id>/void/', views.void_invoice, name='void_invoice'),
    path('invoice/<int:invoice_id>/credit-note/', views.supplier_invoice_credit_note, name='supplier_invoice_credit_note'),
    path('invoice/<int:invoice_id>/refund/', views.supplier_invoice_refund, name='supplier_invoice_refund'),
    

    # ---------------------------
    # Sales & Cash Register
    # ---------------------------
    path("manifest.webmanifest", views.quickstock_manifest, name="quickstock_manifest"),
    path("service-worker.js", views.quickstock_service_worker, name="quickstock_service_worker"),
    path('cash-register/', views.cash_register, name='cash_register'),
    path('sales/', views.cash_register, name='sales'),  # Alias for cash_register
    path('sales/quotations/', views.sales_quotation_list, name='sales_quotation_list'),
    path('sales/quotations/new/', views.sales_quotation_create, name='sales_quotation_create'),
    path('sales/quotations/<int:quote_id>/', views.sales_quotation_detail, name='sales_quotation_detail'),
    path('sales/quotations/<int:quote_id>/email/', views.sales_quotation_email, name='sales_quotation_email'),
    path('sales/quotations/<int:quote_id>/convert/', views.sales_quotation_convert_to_invoice, name='sales_quotation_convert_to_invoice'),
    path('sales/invoices/', views.sales_invoice_list, name='sales_invoice_list'),
    path('sales/invoices/new/', views.sales_invoice_create, name='sales_invoice_create'),
    path('sales/invoices/<int:invoice_id>/', views.sales_invoice_detail, name='sales_invoice_detail'),
    path('sales/invoices/<int:invoice_id>/email/', views.sales_invoice_email, name='sales_invoice_email'),
    path('sales/invoices/<int:invoice_id>/credit-note/', views.sales_invoice_create_credit_note, name='sales_invoice_create_credit_note'),
    path('sales/invoices/<int:invoice_id>/payment/', views.sales_invoice_payment, name='sales_invoice_payment'),
    path('sales/invoices/<int:invoice_id>/payment/<int:payment_id>/revert/', views.sales_invoice_payment_revert, name='sales_invoice_payment_revert'),
    path('sales/invoices/<int:invoice_id>/mark-paid/', views.sales_invoice_mark_paid, name='sales_invoice_mark_paid'),
    path('sales/invoices/<int:invoice_id>/mark-collected/', views.sales_invoice_mark_collected, name='sales_invoice_mark_collected'),
    path('sales/invoices/<int:invoice_id>/classify-collection/', views.sales_invoice_classify_collection, name='sales_invoice_classify_collection'),
    path('pos/items/', views.pos_items, name='pos_items'),
    path('pos/item-lookup/', views.pos_item_lookup, name='pos_item_lookup'),
    path('pos/card-checkout/', views.card_checkout, name='card_checkout'),
    path('jamdex/checkout/', views.jamdex_checkout, name='jamdex_checkout'),
    path('jamdex/callback/', views.jamdex_callback, name='jamdex_callback'),
    path('role-status/', views.role_status, name='role_status'),
    path('receipt/<int:sale_id>/', views.view_receipt, name='view_receipt'),
    path('receipt/<int:sale_id>/print/', views.receipt_print_view, name='receipt_print'),
    path('receipt/<int:sale_id>/download/', views.receipt_download_pdf, name='receipt_download_pdf'),
    path('receipt/<int:sale_id>/email/', views.receipt_email_view, name='receipt_email'),
    path('receipt/<int:sale_id>/edit/', views.receipt_edit_view, name='receipt_edit'),
    path('receipt/<int:sale_id>/void/', views.receipt_void_view, name='receipt_void'),
    path('sales-history/', views.sales_history, name='sales_history'),
    path('clear-sales/', views.clear_sales_view, name='clear_sales'),
    path('shift/close/<int:shift_id>/', views.close_shift_view, name='close_shift'),
    path('shift/reconcile/<int:shift_id>/', views.cash_shift_reconcile, name='cash_shift_reconcile'),
    path('shift/movement/<int:shift_id>/', views.cash_shift_movement, name='cash_shift_movement'),
    path('open-shift/', views.open_shift, name='open_shift'),

    # ---------------------------
    # Reports & Exports
    # ---------------------------
    path('advanced-reports/', views.advanced_reports, name='advanced_reports'),
    path('export-inventory/', views.export_inventory_csv, name='export_inventory_csv'),
    path('export-sales/', views.export_sales_csv, name='export_sales_csv'),
    path('export-suppliers/', views.export_suppliers_csv, name='export_suppliers_csv'),
    path('export/<str:report_type>/', views.export_data, name='export_data'),
    path('audit-logs/', views.audit_logs_view, name='audit_logs'),
    path('export-audit-logs/', views.export_audit_csv, name='export_audit_csv'),
    path('audit-logs/cleanup/', views.audit_cleanup_now, name='audit_cleanup_now'),
    path('settings/clear-all-logs/', views.clear_all_logs, name='clear_all_logs'),

    # ---------------------------
    # Subscription & Payments
    # ---------------------------
    path('upgrade/', views.upgrade_plan, name='upgrade'),
    path('upgrade-pro/', views.upgrade_plan, name='upgrade_pro'),
    path('upgrade/success/', views.upgrade_success, name='upgrade_success'),
    path('upgrade/cancel/', views.upgrade_cancel, name='upgrade_cancel'),
    path('upgrade/create-stripe-session/', views.create_checkout_session, name='create_checkout_session'),
    path('upgrade/create-wipay-session/', views.create_wipay_checkout_session, name='create_wipay_checkout_session'),
    path('upgrade/checkout-session/', views.create_wipay_checkout_session, name='create_wipay_checkout_session'),
    path('upgrade/wipay-response/', views.wipay_response, name='wipay_response'),
    path('pricing/', views.pricing_view, name='pricing'),

    # ---------------------------
    # Premium Features
    # ---------------------------
    path('premium/', views.premium_feature, name='premium_feature'),

    # ---------------------------
    # Settings / Staff
    # ---------------------------
    path('settings/', views.settings_view, name='settings'),
    path('manage-staff/', views.manage_staff, name='manage_staff'),
    path('logout/clock-out/', views.logout_with_clock_out, name='logout_with_clock_out'),

    # ---------------------------
    # Downloads
    # ---------------------------
    path('download-software/', views.download_software, name='download_software'),

    # ---------------------------
    # Static / Info
    # ---------------------------
    path('about/', views.about_view, name='about'),
    path('privacy/', views.privacy_view, name='privacy'),
    path('terms/', views.terms_view, name='terms'),
    path('status/', views.status_view, name='status'),
    path('audit/export/', views.export_audit_logs, name='export_audit_logs'),
    path('login/otp/', views.login_otp_view, name='login_otp'),
    path('import/items/', views.import_items_view, name='import_items'),
    path('support/', views.support_view, name='support'),
    path('import/customers/', views.import_customers_view, name='import_customers'),



    
]
