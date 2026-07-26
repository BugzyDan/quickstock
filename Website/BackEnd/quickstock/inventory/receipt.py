from datetime import datetime

# ---------------------------
# Constants
# ---------------------------
TAX_RATE = 0.15  # 15% GCT


# ---------------------------
# Helper Functions
# ---------------------------
def money(value):
    """
    Round a numeric value to 2 decimal places (currency format).
    """
    return round(value, 2)


def calculate_gct(amount):
    """
    Calculate GCT (General Consumption Tax) for a given amount.
    """
    return money(amount * TAX_RATE)


def validate_item(item):
    """
    Ensure that a given inventory item dictionary has all required keys.

    Required keys:
        - Number
        - SKU
        - Category
        - Name
        - Amount

    Returns:
        bool: True if all keys are present, False otherwise.
    """
    required = ["Number", "SKU", "Category", "Name", "Amount"]
    return all(k in item for k in required)


# ---------------------------
# Build Receipt
# ---------------------------
def build_receipt(item, quantity):
    """
    Construct a receipt dictionary for a single item sale.

    Parameters:
        item (dict): The inventory item being sold.
        quantity (int): The quantity sold.

    Returns:
        dict: A structured receipt including totals, tax, profit, and timestamp.
    """
    subtotal = money(item["Price"] * quantity)
    profit = money((item["Price"] - item.get("Cost", 0)) * quantity)
    gct = calculate_gct(subtotal)
    total_bill = money(subtotal + gct)

    return {
        "Items Purchased": [
            {
                "SKU": item["SKU"],
                "Name": item["Name"],
                "Quantity": quantity,
                "Price": item["Price"],
                "Total": subtotal,
                "Profit": profit,
            }
        ],
        "Subtotal": subtotal,
        "TaxRate": TAX_RATE,
        "GCT": gct,
        "Total Bill": total_bill,
        "Date": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
