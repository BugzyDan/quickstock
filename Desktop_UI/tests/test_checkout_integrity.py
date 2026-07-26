import copy
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

import Inventory_gui as inventory_gui


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


def _desktop_gui():
    gui = inventory_gui.InventoryGUI.__new__(inventory_gui.InventoryGUI)
    gui.current_user_id = 7
    gui.current_username = "cashier"
    gui.current_role = "cashier"
    gui.current_tenant_id = 3
    gui.current_operator_status = "active"
    gui.default_location_id = 11
    gui.active_location_id = 11
    gui.active_register_id = 19
    gui.active_register_location_id = 11
    gui.active_register_opened_at = "2026-07-22T08:00:00+00:00"
    gui.active_register_is_open = True
    gui.locations = [{"id": 11, "name": "Main Store"}]
    gui.customers = [
        {
            "id": 301,
            "owner_id": 3,
            "name": "Credit Customer",
            "credit_balance": 50.0,
        }
    ]
    gui.system = SimpleNamespace(
        settings={"tax_rate": 0.15, "tax_label": "GCT", "currency": "JMD"},
        inventory=[
            {
                "id": 101,
                "product_id": 101,
                "SKU": "CHECKOUT-SKU",
                "Name": "Checkout Item",
                "Price": 100.0,
                "Cost": 50.0,
                "Amount": 10,
                "is_taxable": True,
                "status": "active",
                "is_deleted": False,
            }
        ],
        receipts=[],
    )
    gui.api_token = "test-token"
    return gui


def _valid_payload(gui):
    return gui._build_sale_payload(
        total_price=100.00,
        gct_amount=13.04,
        discount_amount=0.00,
        subtotal=86.96,
        items_sold=[
            {
                "product_id": 101,
                "sku": "CHECKOUT-SKU",
                "quantity": 1,
                "unit_price": 100.00,
                "unit_cost": 50.00,
                "is_taxable": True,
                "status": "active",
                "is_deleted": False,
            }
        ],
        tender="cash",
        location_id=11,
        amount_tendered=100.00,
    )


def _mutate_payload(payload, case):
    if case == "empty_cart":
        payload["line_items"] = []
    elif case == "duplicate_item":
        payload["line_items"].append(copy.deepcopy(payload["line_items"][0]))
    elif case == "zero_quantity":
        payload["line_items"][0]["quantity"] = 0
    elif case == "negative_quantity":
        payload["line_items"][0]["quantity"] = -1
    elif case == "deleted_item":
        payload["line_items"][0]["is_deleted"] = True
    elif case == "archived_item":
        payload["line_items"][0]["status"] = "archived"
    elif case == "invalid_price":
        payload["line_items"][0]["unit_price"] = "-1.00"
    elif case == "invalid_cost":
        payload["line_items"][0]["unit_cost"] = "-1.00"
    elif case == "invalid_tax":
        payload["line_items"][0]["tax_rate"] = "0.1000"
    elif case == "invalid_discount":
        payload["totals"]["discount"] = "-1.00"
    elif case == "subtotal_mismatch":
        payload["totals"]["subtotal"] = "80.00"
    elif case == "total_mismatch":
        payload["totals"]["total"] = "99.00"
    elif case == "missing_payment":
        payload["payment"]["method"] = ""
    elif case == "underpayment":
        payload["payment"]["amount_tendered"] = "99.00"
    elif case == "missing_credit_customer":
        payload["payment"]["amount_tendered"] = "90.00"
        payload["payment"]["account_credit"] = "10.00"
    elif case == "invalid_location":
        payload["location"]["id"] = 99
    elif case == "invalid_queue":
        payload["metadata"]["queue_schema_version"] = 2
    else:
        raise AssertionError(f"Unknown mutation case: {case}")


@pytest.mark.parametrize(
    ("case", "expected_code"),
    [
        ("empty_cart", "EMPTY_CART"),
        ("duplicate_item", "DUPLICATE_ITEM"),
        ("zero_quantity", "INVALID_QUANTITY"),
        ("negative_quantity", "INVALID_QUANTITY"),
        ("deleted_item", "ITEM_UNAVAILABLE"),
        ("archived_item", "ITEM_UNAVAILABLE"),
        ("invalid_price", "INVALID_PRICE"),
        ("invalid_cost", "INVALID_PRICE"),
        ("invalid_tax", "INVALID_TAX"),
        ("invalid_discount", "INVALID_DISCOUNT"),
        ("subtotal_mismatch", "INVALID_TOTAL"),
        ("total_mismatch", "INVALID_TOTAL"),
        ("missing_payment", "MISSING_PAYMENT"),
        ("underpayment", "UNDERPAYMENT"),
        ("missing_credit_customer", "CUSTOMER_REQUIRED"),
        ("invalid_location", "INVALID_LOCATION"),
        ("invalid_queue", "INVALID_QUEUE_SCHEMA"),
    ],
)
def test_desktop_preflight_rejects_invalid_checkout_contracts(case, expected_code):
    gui = _desktop_gui()
    payload = _valid_payload(gui)
    _mutate_payload(payload, case)

    valid, error = gui._validate_checkout_payload(payload)

    assert not valid
    assert error["code"] == expected_code


def test_desktop_preflight_requires_active_authorized_operator_and_open_register():
    gui = _desktop_gui()
    payload = _valid_payload(gui)

    gui.current_operator_status = "inactive"
    valid, error = gui._validate_checkout_payload(payload)
    assert not valid
    assert error["code"] == "OPERATOR_INACTIVE"

    gui.current_operator_status = "active"
    gui.current_role = "viewer"
    payload["operator"]["role"] = "viewer"
    valid, error = gui._validate_checkout_payload(payload)
    assert not valid
    assert error["code"] == "OPERATOR_UNAUTHORIZED"

    gui.current_role = "cashier"
    payload["operator"]["role"] = "cashier"
    gui.active_register_is_open = False
    valid, error = gui._validate_checkout_payload(payload)
    assert not valid
    assert error["code"] == "REGISTER_CLOSED"


def test_desktop_payload_builder_produces_valid_contract_with_preserved_item_metadata():
    gui = _desktop_gui()

    payload = _valid_payload(gui)
    valid, error = gui._validate_checkout_payload(payload)

    assert valid, error
    assert payload["contract_version"] == inventory_gui.CHECKOUT_CONTRACT_VERSION
    assert payload["offline_client_ref"].startswith("DESKTOP-")
    assert payload["line_items"][0]["product_id"] == 101
    assert payload["line_items"][0]["status"] == "active"
    assert payload["line_items"][0]["is_deleted"] is False
    assert payload["line_items"][0]["is_taxable"] is True


def test_cart_rejects_invalid_quantities_and_unavailable_items_before_checkout():
    register = inventory_gui.Cash_register_GUI.__new__(inventory_gui.Cash_register_GUI)
    register.cart = []
    register.update_cart_display = Mock()
    item = copy.deepcopy(_desktop_gui().system.inventory[0])

    assert not register.add_to_cart_logic(item, 0)
    assert not register.add_to_cart_logic(item, -2)
    item["is_deleted"] = True
    assert not register.add_to_cart_logic(item, 1)
    item["is_deleted"] = False
    item["status"] = "archived"
    assert not register.add_to_cart_logic(item, 1)
    assert register.cart == []
    register.update_cart_display.assert_not_called()


def test_cart_preserves_authoritative_item_metadata_for_checkout():
    register = inventory_gui.Cash_register_GUI.__new__(inventory_gui.Cash_register_GUI)
    register.cart = []
    register.update_cart_display = Mock()
    item = copy.deepcopy(_desktop_gui().system.inventory[0])

    assert register.add_to_cart_logic(item, 2)

    assert register.cart == [
        {
            "Product ID": 101,
            "SKU": "CHECKOUT-SKU",
            "Name": "Checkout Item",
            "Cost": 50.0,
            "Price": 100.0,
            "Quantity": 2,
            "Taxable": True,
            "Status": "active",
            "Is Deleted": False,
        }
    ]


def test_failed_checkout_preserves_cart_and_has_no_transmission_or_local_effects():
    gui = _desktop_gui()
    gui._sync_queue_request = Mock(side_effect=AssertionError("invalid checkout was transmitted"))
    gui._queue_action = Mock(side_effect=AssertionError("invalid checkout was queued"))

    register = inventory_gui.Cash_register_GUI.__new__(inventory_gui.Cash_register_GUI)
    register.gui_parent = gui
    register.system = gui.system
    register._sale_lock = False
    register.cart = [
        {
            "Product ID": 101,
            "SKU": "CHECKOUT-SKU",
            "Name": "Checkout Item",
            "Cost": 50.0,
            "Price": 100.0,
            "Quantity": 1,
            "Taxable": True,
            "Status": "active",
            "Is Deleted": False,
        }
    ]
    register.discount_entry = _Value("0")
    register.customer_entry = _Value("")
    register.payment_method_var = _Value("cash")
    register.amount_paid_var = _Value("99.00")
    register.credit_to_apply_var = _Value("0.00")
    original_cart = copy.deepcopy(register.cart)

    with patch.object(inventory_gui.messagebox, "showerror") as showerror:
        register.process_sale()

    assert register.cart == original_cart
    assert gui.system.inventory[0]["Amount"] == 10
    assert gui.system.receipts == []
    gui._sync_queue_request.assert_not_called()
    gui._queue_action.assert_not_called()
    assert showerror.call_args.args[0] == "Checkout Blocked"
