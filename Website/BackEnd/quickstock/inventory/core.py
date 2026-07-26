import sys, subprocess
import uuid
from typing import List, Dict, Optional, Union
from .storage import load_data, save_data
from .receipt import build_receipt, TAX_RATE
from collections import defaultdict

class InventorySystem:
    """A simple inventory management system that handles stock, receipts, and sales."""

    def __init__(self):
        """Load existing inventory and receipts from storage or initialize empty lists."""
        data = load_data()

        if data and len(data) == 2:
            # Load saved inventory and receipts
            self.inventory, self.receipts = data
        else:
            # Initialize empty inventory and receipt list if none exist
            self.inventory = []
            self.receipts = []

    def next_item_id(self) -> int:
        """Generate the next unique item number based on existing inventory."""
        if not self.inventory:
            return 1
        # Finds the highest current ID and adds 1
        return max(item['Number'] for item in self.inventory) + 1

    def generate_sku(self, category: str) -> str:
        """Generate a unique SKU using the category and a random UUID snippet."""
        return f"{category[:3]}-{uuid.uuid4().hex[:6].upper()}"

    def bulk_import(self, item_list: List[Dict]) -> int:
        """
        Import a list of dictionaries into the inventory.
        Supports migration services by bypassing interactive prompts.
        """
        added_count = 0
        for data in item_list:
            name = data.get('name')
            if not name:
                continue

            try:
                # Validation: Prevent invalid financial data during migration
                cost = max(0, float(data.get('cost', 0)))
                price = max(0, float(data.get('price', 0)))
                amount = max(0, int(data.get('amount', 0)))

                self.add_item(
                    category=data.get('category', 'Uncategorized'),
                    name=name,
                    cost=cost,
                    price=price,
                    amount=amount,
                    persist=False  # Optimization: Don't save for every item
                )
                added_count += 1
            except Exception as e:
                print(f"Error importing {data.get('name')}: {e}")

        self.save()  # Save once after all items are added
        return added_count

    def add_item(self, category: str, name: str, cost: float = 0, price: float = 0, amount: int = 0, persist: bool = True):
        """
        Add a single item to the inventory with a unique SKU.
        Converts cost and price to float and amount to int.
        """
        category = str(category).strip().title() if category else ''

        # Ensure generated SKU is unique
        while True:
            sku = self.generate_sku(category)
            if not any(i["SKU"] == sku for i in self.inventory):
                break

        item = {
            "Number": self.next_item_id(),
            "SKU": str(sku),          # Ensure SKU is string
            "Category": category,
            "Name": name,
            "Cost": float(cost),      # Ensure cost is float
            "Price": float(price),    # Ensure price is float
            "Amount": int(amount)     # Ensure quantity is integer
        }
        self.inventory.append(item)
        if persist:
            self.save()  # Persist changes immediately

    def view_last_receipt(self):
        """Display the last sale receipt in the console."""
        if not self.receipts:
            print("\n❌ No receipts available.")
            self.pressanykey()
            return

        receipt = self.receipts[-1]

        self.clearscreen()
        print("\n🧾 LAST SALE RECEIPT")
        print("-" * 30)

        for item in receipt["Items Purchased"]:
            print(f"{item['Name']} x{item['Quantity']}  ${item['Total']:.2f}")

        print("-" * 30)
        print(f"Subtotal: ${receipt['Subtotal']:.2f}")
        print(f"GCT: ${receipt['GCT']:.2f}")
        print(f"TOTAL: ${receipt['Total Bill']:.2f}")

        self.pressanykey()

    def sell_item(self, sku: str, quantity: int) -> Dict:
        """
        Sell an item based on its SKU and quantity.
        Updates stock, generates a receipt, and saves to storage.
        """
        search_sku = str(sku).strip()

        # Find item by SKU
        item = next(
            (i for i in self.inventory if str(i["SKU"]) == search_sku),
            None
        )

        if not item:
            raise ValueError(f"Item with SKU {search_sku} not found")
        if quantity <= 0:
            raise ValueError("Invalid quantity")
        if quantity > item["Amount"]:
            raise ValueError("Not enough stock")

        # Build receipt and update stock
        receipt = build_receipt(item, quantity)
        item["Amount"] -= quantity

        self.receipts.append(receipt)
        self.save()  # Persist changes
        return receipt

    def get_item_by_number(self, number: int) -> Optional[Dict]:
        """Retrieve an inventory item by its unique Number."""
        for item in self.inventory:
            if item["Number"] == number:
                return item
        return None

    def get_all_receipts(self):
        """Returns all receipts in reverse order (newest first). Useful for UI history pages."""
        return reversed(self.receipts)

    def delete_item_by_number(self, number: int):
        """Delete an inventory item using its unique Number."""
        self.inventory = [
            item for item in self.inventory if item["Number"] != number
        ]
        self.save()

    def clearscreen(self):
        """Clear the console screen based on OS."""
        OSys = sys.platform
        if OSys == 'win32':
            subprocess.run('cls', shell=True)
        elif OSys in ('linux', 'darwin'):
            subprocess.run('clear', shell=True)

    def pressanykey(self):
        """Pause execution until user presses Enter."""
        input("\nPress Enter to continue ...")
    def add_items(self):
        """
        Add multiple items interactively to the inventory under a single category.
        Prompts user for category, number of items, and details for each item.
        """
        self.clearscreen()
        
        # Prompt for category
        category = input("Category: ").strip()
        if not category:
            print("❌ Category cannot be empty.")
            self.pressanykey()
            return

        category = category.title()  # Standardize capitalization

        # Ask how many types of items to add
        try:
            count = int(input("How many different types of items in this category? "))
        except ValueError:
            print("❌ Invalid number.")
            self.pressanykey()
            return              
        
        for _ in range(count):
            print(f"\n--- Adding Item #{self.next_item_id()} ---")  

            # Prompt for item name
            name = input("Item name: ").strip()

            # Prompt for cost, price, and initial stock, converting to correct types
            try:
                cost = float(input("Item cost: $"))
                price = float(input("Item price: $"))
                amount = int(input("Initial stock amount: "))
            except ValueError:
                print("❌ Invalid input. Skipping this item.")
                continue

            # Add the item to the inventory
            self.add_item(category, name, cost, price, amount)

        # Pause before returning to menu
        self.pressanykey()


    def edit_items(self):
        """
        Edit existing inventory items interactively.
        User can choose which property to edit: category, name, or amount.
        Automatically regenerates SKU if category is changed.
        """
        while True:
            self.clearscreen()
            choice = input("\nWould you like to edit the inventory?\n\n(Y) Yes or (N) No: ").strip().upper()

            if choice in ("N", "NO"):
                break

            elif choice in ("Y", "YES"):
                self.clearscreen()
                num = input("\nEnter the inventory number: ")

                # Ensure inventory number is valid
                try:
                    num = int(num)
                except ValueError:
                    print("\n❌ Invalid input. Please enter a valid inventory number.")
                    self.pressanykey()
                    continue

                found = False

                for item in self.inventory:
                    if item["Number"] == num:
                        found = True

                        # Ask which property to edit
                        property_to_edit = input("\nEnter property to edit (category/name/amount): ").strip().lower()

                        if property_to_edit == "category":
                            new_category = input("\nEnter new category: ").strip()
                            if not new_category:
                                print("❌ Category cannot be empty.")
                                self.pressanykey()
                                break

                            new_category = new_category.title()
                            item["Category"] = new_category

                            # Regenerate SKU for the item to match new category
                            while True:
                                new_sku = str(self.generate_sku(new_category))
                                # Ensure SKU is unique (excluding the current item)
                                if not any(str(i["SKU"]) == new_sku for i in self.inventory if i is not item):
                                    item["SKU"] = new_sku
                                    break

                        elif property_to_edit == "name":
                            item["Name"] = input("\nEnter new name: ").strip()

                        elif property_to_edit == "amount":
                            try:
                                item["Amount"] = int(input("\nEnter new amount: "))
                            except ValueError:
                                print("❌ Invalid amount.")
                                self.pressanykey()
                                break

                        else:
                            print("\n❌ Invalid property.")
                            self.pressanykey()
                            break

                        # Save changes to storage
                        self.save()
                        print("✅ Item updated successfully.")
                        self.pressanykey()
                        return  # Return after one successful edit

                if not found:
                    print("\n❌ Item not found in inventory.")
                    self.pressanykey()

            else:
                print("\nPlease enter (Y) Yes or (N) No.")
                self.pressanykey()
    def item_price(self):
        """
        Adds a price to a specific item in the inventory.
        Prompts the user for the inventory number and validates the input.
        """
        self.clearscreen()
        
        # Prompt for inventory number
        num = input("\nEnter the inventory number: ")
        try:
            num = int(num)
        except ValueError:
            print("\n❌ Invalid input. Please enter a valid inventory number.")
            return self.inventory

        found = False
        for item in self.inventory:
            if item["Number"] == num:
                found = True
                price_input = input("\nEnter the price: $")
                try:
                    price = float(price_input)
                except ValueError:
                    print("\n❌ Invalid input. Please enter a valid price.")
                    return self.inventory
                
                item["Price"] = price
                self.save()
                print("\n✅ Price added successfully.")
                self.pressanykey()
                break

        if not found:
            print("\n❌ Item not found in inventory.")
            self.pressanykey()

        return self.inventory


    def edit_price(self):
        """
        Allows editing the price of an existing inventory item.
        Displays current details before updating.
        """
        self.clearscreen()
        
        # Prompt for inventory number
        num = input("\nEnter the inventory number: ")
        try:
            num = int(num)
        except ValueError:
            print("\n❌ Invalid input. Please enter a valid inventory number.")
            return self.inventory

        found = False
        for item in self.inventory:
            if item["Number"] == num:
                found = True
                
                # Display current details
                print("\nCurrent Details:")
                print(f"Category: {item['Category']}")
                print(f"Name: {item['Name']}")
                print(f"Cost: {item['Cost']}")
                print(f"Amount: {item['Amount']}")
                print(f"Price: ${item.get('Price', 'No price available')}")

                # Prompt for new price
                new_price = input("\nEnter New Price: ")
                try:
                    new_price = float(new_price)
                except ValueError:
                    print("\n❌ Invalid input. Please enter a valid price.")
                    return self.inventory

                item["Price"] = new_price
                self.save()
                print("\n✅ Price updated successfully.")
                self.pressanykey()
                break

        if not found:
            print("\n❌ Item not found in inventory.")
            self.pressanykey()


    def item_deletor(self):
        """
        Deletes an item from the inventory.
        Prompts for inventory number and confirms deletion.
        """
        while True:
            self.clearscreen()
            choice = input("\nDelete an item from the inventory? (Y/N): ").lower()

            if choice in ('n', 'no'):
                return
            elif choice not in ('y', 'yes'):
                print("❌ Please select Yes (Y) or No (N).")
                self.pressanykey()
                continue

            self.clearscreen()
            self.print_inventory()  # Display inventory for reference

            num = input("\nEnter the inventory number to delete ('b' to go back): ").strip().lower()

            # Handle back command first
            if num == 'b':
                continue

            # Convert to integer only now
            try:
                num = int(num)
            except ValueError:
                print("❌ Invalid inventory number.")
                self.pressanykey()
                continue

            # Find item and confirm deletion
            for i, item in enumerate(self.inventory):
                if item["Number"] == num:
                    confirm = input(f"\nAre you sure you want to delete '{item['Name']}'? (Y/N): ").lower()
                    if confirm in ('y', 'yes'):
                        del self.inventory[i]
                        self.save()
                        print("\n✅ Item deleted successfully.")
                    else:
                        print("\n❌ Deletion cancelled.")
                    
                    self.pressanykey()
                    return

            print("\n❌ Item not found in the inventory.")
            self.pressanykey()

    def delete_price_for_item(self, item):
        """
        Deletes the price of a given item after user confirmation.

        Parameters:
            item (dict): The inventory item whose price is to be deleted.
        """
        if "Price" in item:
            confirm = input(f"Delete price for '{item['Name']}'? (Y/N): ").lower()
            if confirm in ('y', 'yes'):
                del item["Price"]
                self.save()
                print("✅ Price deleted.")
            else:
                print("❌ Deletion cancelled.")
        else:
            print("❌ This item has no price set.")


    def price_deletor(self):
        """
        Allows the user to delete prices of items from the inventory interactively.
        Displays all items with current prices and handles user input/confirmation.
        """
        while True:
            self.clearscreen()
            print("INVENTORY (Prices)")
            print("------------------")
            for item in self.inventory:
                price = item.get("Price", "N/A")
                print(f"[{item['Number']}] {item['Name']} | Price: {price}")

            choice = input("\nDelete the price of an item? (Y/N): ").lower()
            if choice in ('n', 'no'):
                break
            if choice not in ('y', 'yes'):
                continue

            num = input("\nEnter the inventory number to delete ('b' to go back): ").strip().lower()
            if num == 'b':
                continue

            try:
                num = int(num)
            except ValueError:
                print("❌ Invalid inventory number.")
                self.pressanykey()
                continue

            for item in self.inventory:
                if item["Number"] == num:
                    self.delete_price_for_item(item)
                    self.pressanykey()
                    break
            else:
                print("❌ Item not found.")
                self.pressanykey()


    def search_inventory_by_name(self):
        """
        Search the inventory by item name and display its price and remaining stock.

        Prompts the user for a search term and displays the first matching item.
        """
        self.clearscreen()
        item_name = input("\nEnter the name of the item: ").lower()
        found = False

        for item in self.inventory:
            if item_name in item["Name"].lower():
                found = True
                price = item.get("Price", "Price not available")
                rem_stock = item["Amount"]
                self.clearscreen()
                print("\n-------------------------------")
                print("-\t  ITEM FOUND\t      -")
                print("-------------------------------")
                print(f"The price of {item['Name']} is ${price} and there are {rem_stock} items in stock.\n")
                self.pressanykey()
                break

        if not found:
            self.clearscreen()
            print("-------------------------------")
            print("-\tITEM NOT FOUND\t      -")
            print("-------------------------------")
            print(f"\n'{item_name}' not found in inventory.")
            self.pressanykey()


    def search_inventory_by_price(self):
        """
        Search the inventory by price and display the item name and remaining stock.

        Prompts the user for a price and displays the first matching item within a small tolerance.
        """
        self.clearscreen()
        try:
            item_price = float(input("\nEnter the price of the item: "))
        except ValueError:
            print("❌ Invalid price.")
            self.pressanykey()
            return

        found = False
        for item in self.inventory:
            if "Price" in item and abs(item["Price"] - item_price) < 0.01:
                found = True
                item_name = item["Name"]
                rem_stock = item["Amount"]
                self.clearscreen()
                print("-------------------------------")
                print("-\t  ITEM FOUND\t      -")
                print("-------------------------------")
                print(f"The price of {item_name} is ${item_price} and there are {rem_stock} items in stock.\n")
                self.pressanykey()
                break

        if not found:
            self.clearscreen()
            print("-------------------------------")
            print("-\tITEM NOT FOUND\t      -")
            print("-------------------------------")
            print(f"\tItem with price ${item_price} not found in inventory.")
            self.pressanykey()

    

    def print_inventory(self, pause=True):
        """
        Displays all items in the inventory, grouped by category.
        
        Parameters:
            pause (bool): If True, waits for user input after printing.
        """
        self.clearscreen()
        print("-------------------------------")
        print("-\tInventory Items:\t-")
        print("-------------------------------")

        if not self.inventory:
            print("\nNo items in inventory.")
            if pause:
                self.pressanykey()
            return

        # Group items by category
        categories = defaultdict(list)
        for item in self.inventory:
            categories[item["Category"]].append(item)

        # Display grouped inventory
        for category, items in categories.items():
            print(f"\nCategory: {category}")
            print("----------------")
            for item in items:
                price = f"${item['Price']:.2f}" if "Price" in item else "N/A"
                print(f"[{item['Number']}] {item['Name']} | Stock: {item['Amount']} | {price}")

        if pause:
            self.pressanykey()


    def add_price_to_item(self, item):
        """
        Allows the user to add a price to an item that doesn't have one yet.
        
        Parameters:
            item (dict): The inventory item to update.
        """
        price = input(f"Enter the price for {item['Name']}: $")
        try:
            item['Price'] = float(price)
            print(f"✅ Price added: ${item['Price']:.2f}")
        except ValueError:
            print("❌ Invalid price. Please enter a valid number.")


    def cash_register(self):
        """
        CLI interface for selling items.
        Allows scanning/entering an item ID, setting price if missing, and selling multiple items.
        """
        self.print_inventory(pause=False)

        while True:
            choice = input("\nEnter Item ID | 'q' = view last receipt | 'b' = back: ").lower()

            # Exit cash register
            if choice == 'b':
                print("\n↩️ Exiting cash register...")
                self.pressanykey()
                return

            # View last receipt
            if choice == 'q':
                self.view_last_receipt()
                return

            # Item selection
            try:
                num = int(choice)
            except ValueError:
                print("❌ Invalid input.")
                self.pressanykey()
                continue

            item = self.get_item_by_number(num)
            if not item:
                print("\n❌ Item not found.")
                self.pressanykey()
                continue

            # Ensure price exists
            if "Price" not in item or item["Price"] <= 0:
                print(f"\n⚠️ Item '{item['Name']}' has no price set.")
                self.add_price_to_item(item)
                self.save()
                continue

            print(f"\nItem found: {item['Name']} | Price: ${item['Price']} | Stock: {item['Amount']}")

            try:
                quantity = int(input("Enter quantity: "))
            except ValueError:
                print("❌ Invalid quantity.")
                self.pressanykey()
                continue

            # Sell item using central sell_item method
            try:
                receipt = self.sell_item(item["SKU"], quantity)
                print("✅ Item sold successfully.")
            except ValueError as e:
                print(f"❌ {e}")

            self.pressanykey()


    def print_daily_summary(self):
        """
        Prints a summary of all receipts for the day.
        Totals the number of items sold and total sales before tax.
        """
        self.clearscreen()
        total_items_sold = 0
        total_sales = 0

        print("*,~*,~*,~*,~*,~*,~*,~*,~*,~*,~*")
        print("*\tDAILY SUMMARY\t      *")
        print("*,~*,~*,~*,~*,~*,~*,~*,~*,~*,~*\n")

        for i, receipt in enumerate(self.receipts, start=1):
            print(f"\nRECEIPT #{i}")
            build_receipt(receipt)  # Assuming build_receipt can print formatted receipt

            for item in receipt["Items Purchased"]:
                total_items_sold += item["Quantity"]
                total_sales += item["Total"]

        print("\n-------------------------------")
        print(f"Total Items Sold: {total_items_sold}")
        print(f"Total Sales (before tax): ${total_sales:.2f}")
        print("-------------------------------")

        self.pressanykey()


    def save(self):
        """
        Save the current inventory and receipts to persistent storage.
        """
        save_data(self.inventory, self.receipts)
