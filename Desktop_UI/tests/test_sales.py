from src.core.sales import SalesManager, build_receipt


def test_build_receipt_includes_customer_metadata():
    receipt = build_receipt(
        item={"SKU": "ABC-1", "Name": "Widget", "Price": 100},
        quantity=2,
        customer_name="KeviDan",
        document_type="Quotation",
        tax_rate=0.15,
    )

    assert receipt["Customer Name"] == "KeviDan"
    assert receipt["Document Type"] == "Quotation"
    assert receipt["Subtotal"] == 200.0
    assert receipt["Total Bill"] == 230.0


def test_sales_manager_process_sale_keeps_customer_metadata():
    manager = SalesManager([])
    item = {"SKU": "ABC-1", "Name": "Widget", "Price": 100, "Amount": 5}

    receipt = manager.process_sale(
        item=item,
        quantity=1,
        inventory=[item],
        settings={"brand_name": "QuickStock", "tax_rate": 0.15, "tax_label": "GCT"},
        business_trn="123456",
        customer_name="KeviDan",
        document_type="Invoice",
    )

    assert receipt["Customer Name"] == "KeviDan"
    assert receipt["Document Type"] == "Invoice"
    assert item["Amount"] == 4
