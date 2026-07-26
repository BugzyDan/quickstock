import json
import uuid
from django.conf import settings
from django.db import models, transaction
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from django.db.models.signals import post_save, post_delete
from django.db.models import F, Q, Sum
from django.db.models.deletion import ProtectedError
from django.dispatch import receiver
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from decimal import Decimal

# ----------------------------
# Constants
# ----------------------------
COUNTRY_CHOICES = (
    ("JM", "Jamaica"),
    ("TT", "Trinidad & Tobago"),
    ("BB", "Barbados"),
    ("GY", "Guyana"),
    ("LC", "Saint Lucia"),
    ("INT", "International"),
)

DOCUMENT_META_PREFIX = "[[QS_META]]"


def _document_decimal(value, default="0.00"):
    try:
        return Decimal(str(value if value is not None else default))
    except Exception:
        return Decimal(default)


def _document_meta_from_notes(notes):
    raw_notes = str(notes or "").strip()
    if not raw_notes.startswith(DOCUMENT_META_PREFIX):
        return {}

    first_line, _, _remainder = raw_notes.partition("\n")
    payload = first_line[len(DOCUMENT_META_PREFIX):].strip()
    try:
        meta = json.loads(payload) if payload else {}
    except Exception:
        return {}
    return meta if isinstance(meta, dict) else {}


def _document_line_totals(lines, notes):
    subtotal = Decimal("0.00")
    taxable_subtotal = Decimal("0.00")

    for line in lines:
        line_total = _document_decimal(getattr(line, "line_total", "0.00"))
        subtotal += line_total
        item = getattr(line, "item", None)
        if item is None or getattr(item, "is_taxable", True):
            taxable_subtotal += line_total

    subtotal = subtotal.quantize(Decimal("0.01"))
    taxable_subtotal = taxable_subtotal.quantize(Decimal("0.01"))
    if subtotal <= Decimal("0.00"):
        return Decimal("0.00"), Decimal("0.00"), Decimal("0.00")

    meta = _document_meta_from_notes(notes)
    discount_type = str(meta.get("discount_type") or "flat").strip().lower()
    discount_value = max(Decimal("0.00"), _document_decimal(meta.get("discount_value"), "0.00").quantize(Decimal("0.01")))
    tax_rate_percent = max(Decimal("0.00"), _document_decimal(meta.get("tax_rate_percent"), "0.00"))
    tax_rate = (tax_rate_percent / Decimal("100")).quantize(Decimal("0.0001"))

    if discount_type == "percent":
        discount_amount = ((subtotal * discount_value) / Decimal("100")).quantize(Decimal("0.01"))
    else:
        discount_amount = discount_value

    discount_amount = max(Decimal("0.00"), min(discount_amount, subtotal))
    taxable_ratio = (taxable_subtotal / subtotal) if subtotal > Decimal("0.00") else Decimal("0.00")
    taxable_discount = (discount_amount * taxable_ratio).quantize(Decimal("0.01"))
    adjusted_taxable = max(Decimal("0.00"), taxable_subtotal - taxable_discount)
    tax_amount = (adjusted_taxable * tax_rate).quantize(Decimal("0.01"))
    total_amount = max(Decimal("0.00"), subtotal - discount_amount + tax_amount).quantize(Decimal("0.01"))
    return subtotal, tax_amount, total_amount


def _tenant_owner_id_for_user(user):
    if not user:
        return None
    if getattr(user, "is_superuser", False):
        return getattr(user, "id", None)

    profile = None
    if getattr(user, "pk", None):
        try:
            profile = (
                UserProfile.objects
                .select_related("parent_admin", "parent_admin__user")
                .filter(user_id=user.pk)
                .first()
            )
        except Exception:
            profile = None
    if profile is None:
        profile = getattr(user, "profile", None)

    effective_owner = getattr(profile, "effective_owner", None) if profile else None
    if effective_owner is not None:
        return getattr(effective_owner, "id", None)
    return getattr(user, "id", None)


def _validate_same_owner(errors, *, owner_id, related_obj, field_name, label, allow_superuser=False):
    if not owner_id or related_obj is None:
        return

    related_owner_id = getattr(related_obj, "owner_id", None)
    if related_owner_id is None:
        return

    if related_owner_id == owner_id:
        return

    if allow_superuser and getattr(related_obj, "is_superuser", False):
        return

    errors[field_name] = f"{label} belongs to a different admin account."


def _validate_actor_in_owner_tenant(errors, *, owner_id, actor, field_name, label):
    if not owner_id or actor is None:
        return

    actor_owner_id = _tenant_owner_id_for_user(actor)
    if actor_owner_id is None:
        return
    if actor_owner_id != owner_id and not getattr(actor, "is_superuser", False):
        errors[field_name] = f"{label} belongs to a different admin account."


# ----------------------------
# Core reference models
# ----------------------------
class Brand(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="brands", null=True, blank=True)
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]
        unique_together = ("owner", "name")

    def __str__(self):
        return f"{self.name} ({self.owner.username})"


class Category(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="categories", null=True, blank=True)
    name = models.CharField(max_length=100)

    class Meta:
        ordering = ["name"]
        verbose_name_plural = "Categories"
        unique_together = ("owner", "name")

    def __str__(self):
        return f"{self.name} ({self.owner.username})"


class Supplier(models.Model):
    TYPE_LOCAL = "local"
    TYPE_INTERNATIONAL = "international"
    SUPPLIER_TYPE_CHOICES = (
        (TYPE_LOCAL, "Local supplier"),
        (TYPE_INTERNATIONAL, "International supplier"),
    )

    name = models.CharField(max_length=200)
    contact_name = models.CharField(max_length=200, blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    supplier_type = models.CharField(
        max_length=20,
        choices=SUPPLIER_TYPE_CHOICES,
        default=TYPE_LOCAL,
        db_index=True,
    )
    country_code = models.CharField(
        max_length=3,
        choices=COUNTRY_CHOICES,
        default="JM",
    )
    address = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="suppliers",
    )
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archived_suppliers",
    )
    archive_reason = models.TextField(blank=True, default="")

    def __str__(self):
        return self.name


class Customer(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="customers",
        on_delete=models.CASCADE,
    )
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=30, blank=True, null=True)
    physical_address = models.TextField(blank=True, default="")
    business_address = models.TextField(blank=True, default="")
    notes = models.TextField(blank=True, default="")
    credit_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(auto_now_add=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archived_customers",
    )
    archive_reason = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["owner"], name="customer_owner_idx"),
            models.Index(fields=["email"], name="customer_email_idx"),
            models.Index(fields=["phone"], name="customer_phone_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(credit_balance__gte=Decimal("0.00")),
                name="customer_credit_nonnegative",
            ),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        is_new = self.pk is None
        tracks_credit = is_new or update_fields is None or "credit_balance" in update_fields
        previous_balance = Decimal("0.00")

        with transaction.atomic():
            if self.pk and tracks_credit:
                previous_balance = (
                    type(self).objects.select_for_update()
                    .filter(pk=self.pk)
                    .values_list("credit_balance", flat=True)
                    .first()
                    or Decimal("0.00")
                )

            self.credit_balance = Decimal(self.credit_balance or "0.00").quantize(Decimal("0.01"))
            if self.credit_balance < Decimal("0.00"):
                raise ValidationError({"credit_balance": "Customer credit balance cannot be negative."})

            super().save(*args, **kwargs)

            credit_delta = (self.credit_balance - previous_balance).quantize(Decimal("0.01"))
            if tracks_credit and credit_delta:
                CustomerCreditMovement.objects.create(
                    customer=self,
                    amount=credit_delta,
                    movement_type=(
                        CustomerCreditMovement.TYPE_OPENING_BALANCE
                        if is_new and credit_delta > Decimal("0.00")
                        else CustomerCreditMovement.TYPE_MANUAL_ADJUSTMENT
                    ),
                    reason="Opening customer credit balance." if is_new else "Direct customer credit balance adjustment.",
                )

    def _apply_credit_delta(
        self,
        amount,
        *,
        movement_type,
        created_by=None,
        payment=None,
        payment_reversal=None,
        credit_note=None,
        sale=None,
        reason="",
    ):
        if not self.pk:
            raise ValidationError({"credit_balance": "Save the customer before changing account credit."})

        amount = Decimal(amount or "0.00").quantize(Decimal("0.01"))
        if amount == Decimal("0.00"):
            return self.credit_balance

        with transaction.atomic():
            locked_customer = type(self).objects.select_for_update().get(pk=self.pk)
            next_balance = (locked_customer.credit_balance + amount).quantize(Decimal("0.01"))
            if next_balance < Decimal("0.00"):
                raise ValidationError({"credit_balance": "Customer credit cannot be reduced below zero."})

            type(self).objects.filter(pk=self.pk).update(credit_balance=next_balance)
            CustomerCreditMovement.objects.create(
                customer=locked_customer,
                amount=amount,
                movement_type=movement_type,
                created_by=created_by,
                payment=payment,
                payment_reversal=payment_reversal,
                credit_note=credit_note,
                sale=sale,
                reason=reason,
            )

        self.credit_balance = next_balance
        return next_balance

    def add_credit(
        self,
        amount,
        *,
        save=True,
        movement_type="manual_adjustment",
        created_by=None,
        payment=None,
        payment_reversal=None,
        credit_note=None,
        sale=None,
        reason="",
    ):
        amount = Decimal(amount or "0.00").quantize(Decimal("0.01"))
        if amount <= Decimal("0.00"):
            return self.credit_balance
        if not save:
            self.credit_balance = (self.credit_balance + amount).quantize(Decimal("0.01"))
            return self.credit_balance
        return self._apply_credit_delta(
            amount,
            movement_type=movement_type,
            created_by=created_by,
            payment=payment,
            payment_reversal=payment_reversal,
            credit_note=credit_note,
            sale=sale,
            reason=reason,
        )

    def use_credit(
        self,
        amount,
        *,
        save=True,
        movement_type="manual_adjustment",
        created_by=None,
        payment=None,
        payment_reversal=None,
        credit_note=None,
        sale=None,
        reason="",
    ):
        amount = Decimal(amount or "0.00").quantize(Decimal("0.01"))
        if amount <= Decimal("0.00"):
            return self.credit_balance
        if not save:
            next_balance = (self.credit_balance - amount).quantize(Decimal("0.01"))
            if next_balance < Decimal("0.00"):
                raise ValidationError({"credit_balance": "Customer credit cannot be reduced below zero."})
            self.credit_balance = next_balance
            return self.credit_balance
        return self._apply_credit_delta(
            -amount,
            movement_type=movement_type,
            created_by=created_by,
            payment=payment,
            payment_reversal=payment_reversal,
            credit_note=credit_note,
            sale=sale,
            reason=reason,
        )

    @property
    def ledger_credit_balance(self):
        total = self.credit_movements.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        return Decimal(total).quantize(Decimal("0.01"))


class CustomerNote(models.Model):
    customer = models.ForeignKey(
        Customer,
        on_delete=models.CASCADE,
        related_name="note_entries",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="customer_note_entries",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="customer_notes_created",
    )
    body = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["customer", "-created_at"], name="cust_note_customer_created_idx"),
            models.Index(fields=["owner"], name="cust_note_owner_idx"),
        ]

    def __str__(self):
        return f"Note for {self.customer.name} on {self.created_at:%Y-%m-%d %H:%M}"



class Location(models.Model):
    name = models.CharField(max_length=100)
    address = models.TextField(blank=True)
    is_warehouse = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    # This MUST always point to the Admin/Owner User
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="locations",
        on_delete=models.CASCADE,
        null=True, blank=True, # Temporarily allow null to break migration loop
    )
    
    country_code = models.CharField(
        max_length=3,
        choices=COUNTRY_CHOICES,
        default="JM",
    )
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archived_locations",
    )
    archive_reason = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("owner", "name")
        verbose_name = "Location/Branch"
        verbose_name_plural = "Locations/Branches"
        indexes = [
            # Removed redundant 'owner' index since Django builds it implicitly for ForeignKeys
            models.Index(fields=["name"], name="inventory_l_name_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_country_code_display()})"

    # --- SECURITY HELPERS ---

    def can_be_accessed_by(self, user):
        """
        Determines if a specific user has permission to view/interact with this branch.
        """
        profile = getattr(user, 'profile', None)
        if not profile:
            return self.owner == user # Superusers or profile-less owners get matched directly
                
        # 1. Admins can access all their own locations
        if profile.role == 'admin' and self.owner == user:
            return True
            
        # 2. Staff check
        if profile.parent_admin and profile.parent_admin.user == self.owner:
            # Managers can typically see all branches under their admin
            if profile.role == 'manager':
                return True
            # Cashiers are locked to their default_location
            return profile.default_location == self
            
        return False

    @property
    def active_staff(self):
        """
        Returns a queryset of all staff currently assigned to this branch.
        Safeguarded against missing reverse relation configuration on UserProfile.
        """
        if hasattr(self, 'default_users'):
            return self.default_users.filter(status='active')
        
        # Fallback to an empty queryset matching the system User architecture if relation isn't bound yet
        return settings.AUTH_USER_MODEL.objects.none()
    
    
class Item(models.Model):
    # Status Options
    STATUS_CHOICES = (
        ("active", "Active"),      # Visible in POS and Inventory
        ("archived", "Archived"),  # Hidden from POS, kept for records
        ("draft", "Draft"),        # Under construction
        ("out_of_stock", "Out of Stock"), # Low inventory flag
    )

    name = models.CharField(max_length=100)
    sku = models.CharField(max_length=50, blank=True, null=True)
    barcode = models.CharField(max_length=64, blank=True, null=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active", db_index=True)
    sync_token = models.CharField(max_length=64, blank=True, null=True)
    last_modified = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    sync_source = models.CharField(max_length=20, default="web")

    # Financials
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    price = models.DecimalField(max_digits=12, decimal_places=2, help_text="Shelf price (GCT Inclusive)")
    
    # Jamaican GCT Toggle
    is_taxable = models.BooleanField(
        default=True, 
        help_text="Uncheck for GCT Exempt/Zero-rated items (e.g., basic food items, medicine)."
    )

    # Relationships
    brand = models.ForeignKey(Brand, on_delete=models.CASCADE, related_name="items")
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name="items")
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="items",
    )
    
    # Core Multi-Tenant Foreign Key
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="inventory_items",
        null=True, # Added to resolve migration loop during field introduction
        blank=True,
    )

    class Meta:
        ordering = ["name"]
        
        # FIX: Explicitly enforce uniqueness within each tenant's account boundary
        unique_together = (
            ("owner", "sku"), 
            ("owner", "barcode")
        )
        
        indexes = [
            models.Index(fields=["owner", "name"], name="inv_owner_item_name_idx"),
            models.Index(fields=["owner", "status"], name="inv_owner_item_status_idx"),
            models.Index(fields=["owner", "sku"], name="inv_owner_sku_idx"),
            models.Index(fields=["owner", "barcode"], name="inv_owner_barcode_idx"),
        ]

    def __str__(self):
        return f"{self.name} ({self.sku}) | Owner: {self.owner.username}"
    
    @property
    def total_global_quantity(self):
        """Single source of truth: Aggregates across branch stock records dynamically."""
        return self.stock_at_locations.aggregate(total=Sum('quantity'))['total'] or 0

    @property
    def quantity(self):
        """Dynamic compatibility alias for templates and tests."""
        return self.total_global_quantity


    def mark_synced(self, token=None):
        self.sync_token = token or uuid.uuid4().hex
        self.save(update_fields=["sync_token", "last_modified"])

    @property
    def net_unit_price(self):
        """Calculates price without GCT for backend reporting."""
        # Assuming a default GCT rate of 15% for this calculation if not explicitly stored
        tax_divisor = Decimal("1.00") + (Decimal("0.15") if self.is_taxable else Decimal("0.00"))
        return (self.price / tax_divisor).quantize(Decimal("0.01"))

    def _scope_reference_data_to_owner(self):
        if not self.owner_id:
            return

        if self.category_id and self.category.owner_id != self.owner_id:
            self.category, _ = Category.objects.get_or_create(
                owner_id=self.owner_id,
                name=self.category.name,
            )

        if self.brand_id and self.brand.owner_id != self.owner_id:
            self.brand, _ = Brand.objects.get_or_create(
                owner_id=self.owner_id,
                name=self.brand.name,
            )

    def clean(self):
        errors = {}
        self._scope_reference_data_to_owner()

        if self.price is not None and self.price < 0:
            errors["price"] = "Selling price cannot be negative."
        if self.cost_price is not None and self.cost_price < 0:
            errors["cost_price"] = "Cost price cannot be negative."

        if self.owner_id:
            _validate_same_owner(
                errors,
                owner_id=self.owner_id,
                related_obj=self.category,
                field_name="category",
                label="Category",
            )
            _validate_same_owner(
                errors,
                owner_id=self.owner_id,
                related_obj=self.brand,
                field_name="brand",
                label="Brand",
            )
            _validate_same_owner(
                errors,
                owner_id=self.owner_id,
                related_obj=self.supplier,
                field_name="supplier",
                label="Supplier",
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        skip_validation = kwargs.pop("skip_validation", False)
        self._scope_reference_data_to_owner()

        # FIX: Scope the counter lookup directly to the specific owner's records
        if not self.sku and self.category:
            prefix = self.category.name[:3].upper()
            
            # Using row-level transaction locks to prevent duplicate SKU assignment under heavy loads
            with transaction.atomic():
                last_item = Item.objects.filter(
                    owner=self.owner, 
                    sku__startswith=f"{prefix}-"
                ).order_by('sku').select_for_update().last()

            if last_item and last_item.sku:
                try:
                    last_id_str = last_item.sku.split('-')[-1]
                    new_id = int(last_id_str) + 1
                except (ValueError, IndexError):
                    new_id = 1
            else:
                new_id = 1

            self.sku = f"{prefix}-{new_id:04d}"

        if not skip_validation:
            self.full_clean()

        super().save(*args, **kwargs)

class Payment(models.Model):
    PROVIDER_CHOICES = (
        ("wipay", "WiPay"),
    )
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("failed", "Failed"),
    )

    provider = models.CharField(max_length=20, choices=PROVIDER_CHOICES, default="wipay")
    order_id = models.CharField(max_length=64, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=10, default="JMD")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending")
    transaction_id = models.CharField(max_length=100, blank=True, null=True)
    response_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="payments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["order_id"], name="inventory_p_order_i_1ee56e_idx"),
            models.Index(fields=["status"], name="inventory_p_status_4659b1_idx"),
        ]

    def __str__(self):
        return f"Payment {self.order_id}"

class CashShiftQuerySet(models.QuerySet):
    PROTECTED_UPDATE_FIELDS = {
        "cashier",
        "cashier_id",
        "cashier_username",
        "location",
        "location_id",
        "location_name",
        "owner",
        "owner_id",
        "opened_at",
        "opening_cash",
        "total_sales",
        "expected_cash",
        "actual_cash",
        "end_time",
        "is_closed",
    }

    def update(self, **kwargs):
        if self.PROTECTED_UPDATE_FIELDS.intersection(kwargs):
            raise ValidationError(
                "Cash shifts cannot be changed through bulk updates; use the audited lifecycle methods."
            )
        return super().update(**kwargs)

    def delete(self):
        if self.exists():
            raise ProtectedError(
                "Cash shift history cannot be deleted; archive the related account or location instead.",
                self,
            )
        return super().delete()


class CashShift(models.Model):
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="shifts"
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_cash_shifts",
        null=True,
        blank=True,
    )
    cashier_username = models.CharField(max_length=150, blank=True, default="")
    location = models.ForeignKey(
        "Location",
        on_delete=models.PROTECT,
        related_name="shifts"
    )
    location_name = models.CharField(max_length=100, blank=True, default="")

    # Timing — Streamlined with singular indexing source of truth
    opened_at = models.DateTimeField(
        default=timezone.now, 
        db_index=True,
        help_text="The exact timestamp when the cashier opened the shift and drawer session."
    )
    end_time = models.DateTimeField(
        null=True, 
        blank=True,
        help_text="The exact timestamp when the shift session was finalized and closed."
    )

    # Money Tracking
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_sales = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    actual_cash = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    last_calculated_at = models.DateTimeField(null=True, blank=True)

    # Status
    is_closed = models.BooleanField(default=False, db_index=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ["-opened_at"]
        indexes = [
            models.Index(fields=["is_closed", "cashier", "location"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["cashier", "location"],
                condition=Q(is_closed=False),
                name="cashshift_one_open_per_operator_location",
            ),
            models.CheckConstraint(
                condition=Q(opening_cash__gte=Decimal("0.00")),
                name="cashshift_opening_cash_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(actual_cash__isnull=True) | Q(actual_cash__gte=Decimal("0.00")),
                name="cashshift_actual_cash_nonnegative",
            ),
        ]

    objects = CashShiftQuerySet.as_manager()

    def __str__(self):
        return f"Shift {self.id} - {self.cashier.username} ({self.location.name if self.location else 'Unknown'})"

    def clean(self):
        errors = {}
        cashier_owner_id = _tenant_owner_id_for_user(self.cashier)
        location_owner_id = getattr(self.location, "owner_id", None)
        if self.owner_id and location_owner_id and self.owner_id != location_owner_id:
            errors["owner"] = "Cash shift owner must match the register location owner."
        if cashier_owner_id:
            _validate_same_owner(
                errors,
                owner_id=cashier_owner_id,
                related_obj=self.location,
                field_name="location",
                label="Register location",
            )
        if self.opening_cash is not None and self.opening_cash < Decimal("0.00"):
            errors["opening_cash"] = "Opening cash cannot be negative."
        if self.pk is None:
            if getattr(self.location, "is_archived", False):
                errors["location"] = "Archived locations cannot open a cash shift."
            profile = getattr(self.cashier, "profile", None)
            if not getattr(self.cashier, "is_active", True) or (profile and (profile.is_archived or profile.status == "suspended")):
                errors["cashier"] = "Archived or inactive operators cannot open a cash shift."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        allow_integrity_update = kwargs.pop("_allow_integrity_update", False)
        if self.pk and not allow_integrity_update:
            original = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "cashier_id",
                "cashier_username",
                "location_id",
                "location_name",
                "owner_id",
                "opened_at",
                "opening_cash",
            )
            changed = [
                field
                for field in immutable_fields
                if getattr(original, field) != getattr(self, field)
            ]
            if changed:
                raise ValidationError(
                    f"Cash shift opening facts cannot be changed: {', '.join(changed)}."
                )
        if self.pk is None:
            if self.location_id and not self.owner_id:
                self.owner_id = self.location.owner_id
            if self.cashier_id and not self.cashier_username:
                self.cashier_username = self.cashier.get_username()
            if self.location_id and not self.location_name:
                self.location_name = self.location.name
            self.opening_cash = Decimal(self.opening_cash or "0.00").quantize(Decimal("0.01"))
            if self.expected_cash is None or (
                Decimal(self.expected_cash or "0.00") == Decimal("0.00")
                and self.opening_cash > Decimal("0.00")
            ):
                self.expected_cash = self.opening_cash
        self.full_clean()
        super().save(*args, **kwargs)
        
    @property
    def difference(self):
        """Calculates if the drawer is 'Short' or 'Over'."""
        if self.actual_cash is not None:
            return self.actual_cash - self.expected_cash
        return Decimal("0.00")

    def calculate_expected_balance(self, force=False):
        """
        Cached + safe recalculation of shift totals.
        Filters specifically for 'cash' transactions using an index-friendly exact match.
        """
        if not force and self.last_calculated_at and self.is_closed:
            return self.expected_cash

        totals = self.reconciliation_totals()
        self.total_sales = totals["total_sales"]
        self.expected_cash = totals["expected_cash"]
        self.last_calculated_at = timezone.now()

        return self.expected_cash

    def reconciliation_totals(self):
        """Derive tender totals from unreversed, non-voided financial records."""
        valid_sales = self.sales.exclude(receipt_status__in=("draft", "voided"))
        sale_totals = {
            row["tender"]: (row["total"] or Decimal("0.00"))
            for row in valid_sales.values("tender").annotate(total=Sum("total_price"))
        }
        invoice_totals = {}
        try:
            invoice_totals = {
                row["payment_method"]: (row["total"] or Decimal("0.00"))
                for row in SalesInvoicePayment.objects.unreversed()
                .filter(shift_id=self.pk)
                .values("payment_method")
                .annotate(total=Sum("amount"))
            }
        except (NameError, OperationalError, ProgrammingError):
            invoice_totals = {}

        tender_totals = {}
        for source in (sale_totals, invoice_totals):
            for tender, amount in source.items():
                tender_totals[tender] = (
                    tender_totals.get(tender, Decimal("0.00")) + Decimal(amount or "0.00")
                ).quantize(Decimal("0.01"))

        cash_sales = sum(
            (amount for tender, amount in sale_totals.items() if tender == "cash"),
            Decimal("0.00"),
        )
        cash_invoice_payments = sum(
            (amount for tender, amount in invoice_totals.items() if tender == "cash"),
            Decimal("0.00"),
        )
        try:
            cash_in = self.cash_movements.filter(direction=CashMovement.DIRECTION_IN).aggregate(
                total=Sum("amount")
            )["total"] or Decimal("0.00")
            cash_out = self.cash_movements.filter(direction=CashMovement.DIRECTION_OUT).aggregate(
                total=Sum("amount")
            )["total"] or Decimal("0.00")
        except (NameError, OperationalError, ProgrammingError):
            cash_in = cash_out = Decimal("0.00")

        total_sales = sum(tender_totals.values(), Decimal("0.00")).quantize(Decimal("0.01"))
        expected_cash = (
            Decimal(self.opening_cash or "0.00")
            + cash_sales
            + cash_invoice_payments
            + Decimal(cash_in)
            - Decimal(cash_out)
        ).quantize(Decimal("0.01"))
        return {
            "tenders": tender_totals,
            "total_sales": total_sales,
            "cash_sales": (cash_sales + cash_invoice_payments).quantize(Decimal("0.01")),
            "cash_in": Decimal(cash_in).quantize(Decimal("0.01")),
            "cash_out": Decimal(cash_out).quantize(Decimal("0.01")),
            "expected_cash": expected_cash,
        }

    def _authorized_actor(self, actor):
        if actor is None:
            raise ValidationError("An authorized actor is required for shift reconciliation and close.")
        owner_id = self.owner_id or getattr(self.location, "owner_id", None)
        actor_owner_id = _tenant_owner_id_for_user(actor)
        if actor_owner_id != owner_id and not getattr(actor, "is_superuser", False):
            raise ValidationError("The actor does not belong to this shift tenant.")
        if actor == self.cashier:
            return
        role = getattr(getattr(actor, "profile", None), "role", None)
        if role not in {"admin", "manager"} and not getattr(actor, "is_superuser", False):
            raise ValidationError("Only the shift operator, manager, or administrator may close this shift.")

    def reconcile(self, counted_cash, *, actor, denominations=None, explanation=""):
        """Create one immutable reconciliation snapshot under the shift lock."""
        with transaction.atomic():
            shift = type(self).objects.select_for_update().get(pk=self.pk)
            shift._authorized_actor(actor)
            if shift.is_closed:
                return shift.reconciliation
            counted = Decimal(str(counted_cash)).quantize(Decimal("0.01"))
            if counted < Decimal("0.00"):
                raise ValidationError("Counted cash cannot be negative.")
            normalized_denominations = {}
            if denominations:
                for raw_value, raw_quantity in denominations.items():
                    value = str(raw_value).replace("qty_", "")
                    denomination = Decimal(value).quantize(Decimal("0.01"))
                    quantity = int(raw_quantity or 0)
                    if denomination <= 0 or quantity < 0:
                        raise ValidationError("Cash denominations must have positive values and non-negative quantities.")
                    normalized_denominations[str(raw_value)] = quantity
                denomination_total = sum(
                    Decimal(str(key).replace("qty_", "")) * quantity
                    for key, quantity in normalized_denominations.items()
                ).quantize(Decimal("0.01"))
                if denomination_total != counted:
                    raise ValidationError("Cash denomination total must equal counted closing cash.")

            shift.calculate_expected_balance(force=True)
            variance = (counted - shift.expected_cash).quantize(Decimal("0.01"))
            existing = CashReconciliation.objects.filter(shift=shift).first()
            if existing:
                if (
                    existing.counted_cash != counted
                    or existing.denominations != normalized_denominations
                ):
                    raise ValidationError("This shift already has a different reconciliation record.")
                return existing
            reconciliation = CashReconciliation.objects.create(
                shift=shift,
                owner_id=shift.owner_id,
                location=shift.location,
                location_name=shift.location_name or shift.location.name,
                expected_cash=shift.expected_cash,
                counted_cash=counted,
                variance=variance,
                denominations=normalized_denominations,
                reconciled_by=actor,
                reconciled_by_username=actor.get_username(),
                explanation=(explanation or "").strip(),
            )
            shift.actual_cash = counted
            shift.save(
                update_fields=["actual_cash", "expected_cash", "total_sales", "last_calculated_at"],
                _allow_integrity_update=True,
            )
            return reconciliation

    def close_shift(self, final_count=None, notes=None, *, actor=None, denominations=None):
        """Atomically reconcile and close a shift with durable close evidence."""
        with transaction.atomic():
            shift = type(self).objects.select_for_update().get(pk=self.pk)
            shift._authorized_actor(actor)
            if shift.is_closed:
                return shift
            if final_count is not None and not CashReconciliation.objects.filter(shift=shift).exists():
                shift.reconcile(
                    final_count,
                    actor=actor,
                    denominations=denominations,
                    explanation=notes or "",
                )
                shift.refresh_from_db()
            if not CashReconciliation.objects.filter(shift=shift).exists():
                raise ValidationError("A cash reconciliation is required before closing the shift.")
            shift.end_time = timezone.now()
            shift.is_closed = True
            if notes:
                shift.notes = notes
            shift.save(
                update_fields=["end_time", "is_closed", "notes"],
                _allow_integrity_update=True,
            )
            AuditLog.objects.create(
                user=actor,
                action="register",
                message="Cash shift closed with immutable reconciliation evidence.",
                metadata={
                    "shift_id": shift.id,
                    "owner_id": shift.owner_id,
                    "location_id": shift.location_id,
                    "location_name": shift.location_name,
                    "cashier_id": shift.cashier_id,
                    "cashier_username": shift.cashier_username,
                    "reconciliation_id": shift.reconciliation.id,
                    "expected_cash": str(shift.reconciliation.expected_cash),
                    "counted_cash": str(shift.reconciliation.counted_cash),
                    "variance": str(shift.reconciliation.variance),
                },
                severity="warn" if shift.reconciliation.variance else "info",
            )
            return shift

    @classmethod
    def get_active_shift(cls, user, location=None):
        """Return the operator's open session, optionally scoped to a location."""
        queryset = cls.objects.filter(cashier=user, is_closed=False)
        if location is not None:
            queryset = queryset.filter(location=location)
        return queryset.select_related("location").first()


class CashMovementQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Cash movements are immutable; record a compensating movement.")

    def delete(self):
        if self.exists():
            raise ProtectedError("Cash movement history cannot be deleted.", self)
        return super().delete()


class CashMovement(models.Model):
    DIRECTION_IN = "in"
    DIRECTION_OUT = "out"
    DIRECTION_CHOICES = (
        (DIRECTION_IN, "Cash In"),
        (DIRECTION_OUT, "Cash Out"),
    )
    TYPE_CASH_IN = "cash_in"
    TYPE_CASH_OUT = "cash_out"
    TYPE_REFUND = "refund"
    TYPE_CHOICES = (
        (TYPE_CASH_IN, "Cash In"),
        (TYPE_CASH_OUT, "Cash Out"),
        (TYPE_REFUND, "Cash Refund"),
    )

    shift = models.ForeignKey(CashShift, related_name="cash_movements", on_delete=models.PROTECT)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="cash_movements", on_delete=models.PROTECT)
    location = models.ForeignKey(Location, related_name="cash_movements", on_delete=models.PROTECT)
    location_name = models.CharField(max_length=100, blank=True, default="")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="recorded_cash_movements",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    recorded_by_username = models.CharField(max_length=150, blank=True, default="")
    direction = models.CharField(max_length=3, choices=DIRECTION_CHOICES)
    movement_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default=TYPE_CASH_OUT)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    reference = models.CharField(max_length=120, blank=True, default="")
    reversal_of = models.ForeignKey(
        "self",
        related_name="compensating_movements",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    objects = CashMovementQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(condition=Q(amount__gt=Decimal("0.00")), name="cash_movement_amount_positive"),
            models.UniqueConstraint(
                fields=["owner", "shift", "reference"],
                condition=~Q(reference=""),
                name="cash_movement_reference_once_per_shift",
            ),
        ]
        indexes = [
            models.Index(fields=["shift", "direction", "created_at"], name="cash_move_shift_dir_idx"),
            models.Index(fields=["owner", "created_at"], name="cash_move_owner_date_idx"),
        ]

    def clean(self):
        errors = {}
        if self.amount is None or self.amount <= Decimal("0.00"):
            errors["amount"] = "Cash movement amount must be positive."
        if not (self.reason or "").strip():
            errors["reason"] = "A reason is required for every cash movement."
        if self.movement_type == self.TYPE_REFUND and self.direction != self.DIRECTION_OUT:
            errors["direction"] = "Cash refunds must be cash-out movements."
        if self.shift_id:
            if self.owner_id and self.shift.owner_id and self.owner_id != self.shift.owner_id:
                errors["owner"] = "Cash movement owner must match the shift owner."
            if self.location_id != self.shift.location_id:
                errors["location"] = "Cash movement location must match the shift location."
            if self.pk is None and self.shift.is_closed:
                errors["shift"] = "Closed shifts cannot accept cash movements."
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=self.shift.owner_id,
                actor=self.recorded_by,
                field_name="recorded_by",
                label="Cash movement actor",
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Cash movements cannot be changed; record a compensating movement.")
        self.owner_id = self.shift.owner_id
        self.location_id = self.shift.location_id
        self.location_name = self.shift.location_name or self.shift.location.name
        if self.recorded_by and not self.recorded_by_username:
            self.recorded_by_username = self.recorded_by.get_username()
        self.amount = Decimal(self.amount or "0.00").quantize(Decimal("0.01"))
        self.reason = (self.reason or "").strip()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Cash movements cannot be deleted; record a compensating movement.", {self})


class CashReconciliation(models.Model):
    shift = models.OneToOneField(
        CashShift,
        related_name="reconciliation",
        on_delete=models.PROTECT,
    )
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="cash_reconciliations", on_delete=models.PROTECT)
    location = models.ForeignKey(Location, related_name="cash_reconciliations", on_delete=models.PROTECT)
    location_name = models.CharField(max_length=100, blank=True, default="")
    expected_cash = models.DecimalField(max_digits=12, decimal_places=2)
    counted_cash = models.DecimalField(max_digits=12, decimal_places=2)
    variance = models.DecimalField(max_digits=12, decimal_places=2)
    denominations = models.JSONField(default=dict, blank=True)
    reconciled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="performed_cash_reconciliations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    reconciled_by_username = models.CharField(max_length=150, blank=True, default="")
    explanation = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.CheckConstraint(condition=Q(counted_cash__gte=Decimal("0.00")), name="cash_recon_counted_nonnegative"),
        ]
        indexes = [
            models.Index(fields=["owner", "created_at"], name="cash_recon_owner_date_idx"),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Cash reconciliation evidence cannot be changed.")
        self.location_name = self.location_name or self.location.name
        self.reconciled_by_username = self.reconciled_by_username or (
            self.reconciled_by.get_username() if self.reconciled_by else ""
        )
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Cash reconciliation evidence cannot be deleted.", {self})


class DailyCashCount(models.Model):
    CASH_ENTRY_CURRENCY_CHOICES = (
        ("JMD", "JMD"),
        ("USD", "USD"),
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="daily_cash_counts",
    )
    summary_date = models.DateField(db_index=True)
    opening_cash_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    cash_expenditure_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    cash_expenditure_party_name = models.CharField(max_length=255, blank=True)
    cash_expenditure_currency = models.CharField(
        max_length=3,
        choices=CASH_ENTRY_CURRENCY_CHOICES,
        default="JMD",
    )
    cash_expenditure_receipt_reference = models.CharField(max_length=120, blank=True)
    cash_expenditure_notes = models.CharField(max_length=255, blank=True)
    cash_lodgement_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    cash_lodgement_notes = models.CharField(max_length=255, blank=True)
    qty_5000 = models.PositiveIntegerField(default=0)
    qty_2000 = models.PositiveIntegerField(default=0)
    qty_1000 = models.PositiveIntegerField(default=0)
    qty_500 = models.PositiveIntegerField(default=0)
    qty_100 = models.PositiveIntegerField(default=0)
    qty_50 = models.PositiveIntegerField(default=0)
    qty_20 = models.PositiveIntegerField(default=0)
    qty_10 = models.PositiveIntegerField(default=0)
    qty_5 = models.PositiveIntegerField(default=0)
    qty_1 = models.PositiveIntegerField(default=0)
    ncb_machine_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    bns_machine_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="updated_daily_cash_counts",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-summary_date"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "summary_date"], name="uniq_daily_cash_count_owner_date"),
        ]
        indexes = [
            models.Index(fields=["owner", "summary_date"], name="daily_cash_owner_date_idx"),
        ]

    def __str__(self):
        return f"{self.owner.username} cash count {self.summary_date}"

    @property
    def actual_cash(self):
        return (
            Decimal(self.qty_5000 * 5000)
            + Decimal(self.qty_2000 * 2000)
            + Decimal(self.qty_1000 * 1000)
            + Decimal(self.qty_500 * 500)
            + Decimal(self.qty_100 * 100)
            + Decimal(self.qty_50 * 50)
            + Decimal(self.qty_20 * 20)
            + Decimal(self.qty_10 * 10)
            + Decimal(self.qty_5 * 5)
            + Decimal(self.qty_1)
        ).quantize(Decimal("0.01"))


class Sale(models.Model):
    TENDER_CHOICES = (
        ("cash", "Cash"),
        ("jamdex", "JAM-DEX"),
        ("card", "Debit/Credit"),
        ("bank_transfer", "Bank Transfer"),
        ("mobile_money", "Mobile Money"),
        ("other", "Other"),
    )
    STATUS_CHOICES = (
        ("draft", "Draft"),
        ("finalized", "Finalized"),
        ("revised", "Revised"),
        ("emailed", "Emailed"),
        ("voided", "Voided"),
    )

    # --- Financial Totals ---
    # subtotal (Net) + gct_amount = total_price (Gross)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    gct_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    change_due = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    # --- Metadata ---
    timestamp = models.DateTimeField(default=timezone.now)
    sync_token = models.CharField(max_length=64, blank=True, null=True)
    last_modified = models.DateTimeField(auto_now=True)
    is_synced = models.BooleanField(default=False)
    sync_source = models.CharField(max_length=20, default="web")
    tender = models.CharField(max_length=20, choices=TENDER_CHOICES, default="cash")
    receipt_no = models.PositiveIntegerField(blank=True, null=True)
    receipt_status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="finalized", db_index=True)
    customer_name = models.CharField(max_length=255, blank=True)
    customer_email = models.EmailField(blank=True)
    customer_phone = models.CharField(max_length=50, blank=True)
    notes = models.TextField(blank=True)
    payment_reference = models.CharField(max_length=120, blank=True)
    revision_number = models.PositiveIntegerField(default=1)
    previous_revision = models.ForeignKey(
        "self",
        related_name="next_revisions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    void_reason = models.TextField(blank=True)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="voided_sales",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    last_emailed_at = models.DateTimeField(null=True, blank=True)
    last_emailed_to = models.EmailField(blank=True)
    last_emailed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="emailed_sales",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    download_count = models.PositiveIntegerField(default=0)
    printed_count = models.PositiveIntegerField(default=0)
    email_count = models.PositiveIntegerField(default=0)

    # --- Relationships ---
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_records",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    location = models.ForeignKey(
        Location,
        related_name="sales",
        on_delete=models.PROTECT,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_owned",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    shift = models.ForeignKey(
        'CashShift', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="sales"
    )

    class Meta:
        ordering = ["-timestamp"]
        indexes = [
            models.Index(fields=["timestamp"], name="sale_timestamp_idx"),
            models.Index(fields=["cashier"], name="sale_cashier_idx"),
            models.Index(fields=["location"], name="sale_location_idx"),
            models.Index(fields=["owner"], name="sale_owner_idx"),
            models.Index(fields=["receipt_no"], name="sale_receipt_idx"),
            models.Index(fields=["owner", "-timestamp"], name="sale_owner_ts_desc_idx"),
            models.Index(fields=["owner", "location", "-timestamp"], name="sale_owner_loc_ts_idx"),
            models.Index(fields=["owner", "receipt_no"], name="sale_owner_receipt_idx"),
            models.Index(fields=["shift", "tender"], name="sale_shift_tender_idx"),
            models.Index(fields=["receipt_status"], name="sale_receipt_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "sync_token"],
                condition=Q(sync_token__isnull=False) & ~Q(sync_token=""),
                name="sale_owner_sync_token_uniq",
            ),
        ]

    def __str__(self):
        return f"Sale #{self.id} | {self.location.name}"

    def clean(self):
        errors = {}

        for field_name in (
            "subtotal",
            "gct_amount",
            "total_price",
            "discount",
            "amount_paid",
            "change_due",
        ):
            value = getattr(self, field_name, None)
            if value is not None and value < 0:
                errors[field_name] = "Amount cannot be negative."

        if self.owner_id:
            _validate_same_owner(
                errors,
                owner_id=self.owner_id,
                related_obj=self.location,
                field_name="location",
                label="Location",
            )
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=self.owner_id,
                actor=self.cashier,
                field_name="cashier",
                label="Cashier",
            )

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        skip_validation = kwargs.pop("skip_validation", False)
        if not skip_validation:
            self.full_clean()

        super().save(*args, **kwargs)

    def recalculate_totals(self, save=True):
        """
        Queries the database for all related SaleItem records, sums up the 
        net and tax totals, applies any global transaction discounts, 
        and updates the parent Sale record.
        """
        # We wrap this in transaction.atomic to ensure the read + write is isolated
        with transaction.atomic():
            # If there are no items yet, aggregate returns None, so we default to 0.00
            totals = self.items.aggregate(
                sum_net=Sum('net_amount'),
                sum_tax=Sum('tax_amount'),
                sum_total=Sum('total_price')
            )

            raw_subtotal = Decimal(totals['sum_net'] or "0.00").quantize(Decimal("0.01"))
            raw_tax = Decimal(totals['sum_tax'] or "0.00").quantize(Decimal("0.01"))
            raw_total = Decimal(totals['sum_total'] or "0.00").quantize(Decimal("0.01"))

            # 1. Apply global discount to the gross shelf price
            # max() guarantees your total price never drops below zero if a discount is entered incorrectly
            self.total_price = max(raw_total - self.discount, Decimal("0.00")).quantize(
                Decimal("0.01")
            )

            # 2. Assign calculated net subtotal and collected GCT amounts
            self.subtotal = raw_subtotal
            self.gct_amount = raw_tax

            # 3. Persist back to database if requested
            if save:
                self.save(update_fields=['subtotal', 'gct_amount', 'total_price'], skip_validation=True)

    def mark_synced(self, token=None):
        self.is_synced = True
        self.sync_token = token or uuid.uuid4().hex
        self.save(update_fields=["is_synced", "sync_token", "last_modified"])


class ReceiptRevision(models.Model):
    sale = models.ForeignKey(Sale, related_name="receipt_revisions", on_delete=models.PROTECT)
    revision_number = models.PositiveIntegerField()
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="receipt_revisions",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    reason = models.TextField()
    before_data = models.JSONField(default=dict, blank=True)
    after_data = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["sale", "revision_number"], name="receipt_rev_sale_rev_idx"),
            models.Index(fields=["created_at"], name="receipt_rev_created_idx"),
        ]

    def __str__(self):
        return f"Receipt #{self.sale.receipt_no or self.sale_id} revision {self.revision_number}"


class ReceiptEmailLog(models.Model):
    DELIVERY_STATUS_CHOICES = (
        ("pending", "Pending"),
        ("sent", "Sent"),
        ("failed", "Failed"),
    )

    sale = models.ForeignKey(Sale, related_name="receipt_email_logs", on_delete=models.PROTECT)
    recipient_email = models.EmailField()
    sender_email = models.EmailField(blank=True)
    subject = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=16, choices=DELIVERY_STATUS_CHOICES, default="pending")
    error_message = models.TextField(blank=True)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="receipt_email_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    sent_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-sent_at"]
        indexes = [
            models.Index(fields=["sale", "status"], name="receipt_email_sale_status_idx"),
            models.Index(fields=["sent_at"], name="receipt_email_sent_idx"),
        ]

    def __str__(self):
        return f"Receipt email #{self.sale.receipt_no or self.sale_id} -> {self.recipient_email}"


class SaleItem(models.Model):
    sale = models.ForeignKey(Sale, related_name="items", on_delete=models.PROTECT)
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)
    
    unit_price = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        help_text="Price per unit (GCT Inclusive) at time of sale"
    )

    unit_cost = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Cost per unit at the time of sale for accurate profit reporting."
    )
    
    # --- AUDIT FIX: HISTORIC TAX SNAPSHOT ---
    tax_rate_applied = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        default=Decimal("15.00"),
        help_text="The exact GCT percentage applied at the moment of checkout (e.g., 15.00)."
    )
    
    net_amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal("0.00"),
        help_text="Total price minus GCT"
    )
    tax_amount = models.DecimalField(
        max_digits=12, 
        decimal_places=2, 
        default=Decimal("0.00"),
        help_text="Total GCT collected for this line"
    )
    total_price = models.DecimalField(
        max_digits=12, 
        decimal_places=2,
        help_text="Final gross price (Quantity * Unit Price)"
    )

    class Meta:
        verbose_name = "Sale Item"
        verbose_name_plural = "Sale Items"

    def clean(self):
        errors = {}
        if self.quantity is None or self.quantity <= 0:
            errors["quantity"] = "Quantity must be greater than zero."
        if self.unit_price is not None and self.unit_price < 0:
            errors["unit_price"] = "Unit price cannot be negative."
        if self.unit_cost is not None and self.unit_cost < 0:
            errors["unit_cost"] = "Unit cost cannot be negative."
        if self.tax_rate_applied is not None and not Decimal("0.00") <= self.tax_rate_applied <= Decimal("100.00"):
            errors["tax_rate_applied"] = "Tax rate must be between 0 and 100."
        sale_owner_id = getattr(self.sale, "owner_id", None)
        if sale_owner_id:
            _validate_same_owner(
                errors,
                owner_id=sale_owner_id,
                related_obj=self.item,
                field_name="item",
                label="Item",
            )

        if self.sale_id and self.item_id and self.sale.location_id:
            stock_location_owner_id = getattr(self.sale.location, "owner_id", None)
            item_owner_id = getattr(self.item, "owner_id", None)
            if stock_location_owner_id and item_owner_id and stock_location_owner_id != item_owner_id:
                errors["sale"] = "Sale location belongs to a different admin account than the item."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.unit_price = self.unit_price or Decimal("0.00")
        self.quantity = self.quantity or 0
        is_new = self.pk is None

        self.total_price = (
            self.unit_price * self.quantity
        ).quantize(Decimal("0.01"))

        self.full_clean()

        if self.item.is_taxable:
            tax_factor = Decimal("1.00") + (
                self.tax_rate_applied / Decimal("100.00")
            )

            self.net_amount = (
                self.total_price / tax_factor
            ).quantize(Decimal("0.01"))

            self.tax_amount = (
                self.total_price - self.net_amount
            ).quantize(Decimal("0.01"))
        else:
            self.tax_rate_applied = Decimal("0.00")
            self.net_amount = self.total_price
            self.tax_amount = Decimal("0.00")

        with transaction.atomic():

            if is_new:
                stock_record = StockRecord.get_or_create_locked(
                    item=self.item,
                    location=self.sale.location
                )

                if stock_record.quantity < self.quantity:
                    raise ValidationError("Insufficient stock")

                StockRecord.objects.filter(
                    pk=stock_record.pk
                ).update(
                    quantity=models.F('quantity') - self.quantity
                )

            super().save(*args, **kwargs)

class PurchaseOrderQuerySet(models.QuerySet):
    PROTECTED_FIELDS = (
        "item_id",
        "supplier_id",
        "location_id",
        "quantity_received",
        "unit_cost",
        "total_cost",
        "order_date",
    )

    def update(self, **kwargs):
        if set(self.PROTECTED_FIELDS).intersection(kwargs):
            raise ValidationError("Purchase order financial facts cannot be changed after receipt.")
        return super().update(**kwargs)

    def delete(self):
        if self.exists():
            raise ProtectedError("Purchase order history cannot be deleted.", self)
        return super().delete()


class PurchaseOrder(models.Model):
    item = models.ForeignKey(Item, on_delete=models.PROTECT)
    supplier = models.ForeignKey(Supplier, related_name="orders", on_delete=models.PROTECT)
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="purchase_orders",
    )
    quantity_received = models.PositiveIntegerField()
    unit_cost = models.DecimalField(max_digits=10, decimal_places=2)
    total_cost = models.DecimalField(max_digits=10, decimal_places=2)
    order_date = models.DateTimeField(default=timezone.now)

    objects = PurchaseOrderQuerySet.as_manager()

    def __str__(self):
        return f"PO #{self.id}"

    def clean(self):
        errors = {}
        item_owner_id = getattr(self.item, "owner_id", None)
        if item_owner_id:
            _validate_same_owner(
                errors,
                owner_id=item_owner_id,
                related_obj=self.supplier,
                field_name="supplier",
                label="Supplier",
            )
            _validate_same_owner(
                errors,
                owner_id=item_owner_id,
                related_obj=self.location,
                field_name="location",
                label="Location",
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            changed = [
                field for field in PurchaseOrderQuerySet.PROTECTED_FIELDS
                if getattr(original, field) != getattr(self, field)
            ]
            if changed:
                raise ValidationError(
                    f"Purchase order financial facts cannot be changed: {', '.join(sorted(changed))}."
                )
        if self.unit_cost is not None and self.quantity_received is not None:
            self.total_cost = (Decimal(str(self.unit_cost)) * self.quantity_received).quantize(Decimal("0.01"))
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Purchase order history cannot be deleted.", {self})


class SupplierInvoiceQuerySet(models.QuerySet):
    PROTECTED_FIELDS = {
        "supplier", "supplier_id", "location", "location_id", "invoice_no", "amount", "date_issued",
        "status", "void_reason", "voided_at", "voided_by", "voided_by_id",
    }

    def update(self, **kwargs):
        if "paid_amount" in kwargs:
            raise ValidationError(
                "Supplier invoice paid_amount is derived from the payment ledger and cannot be updated directly."
            )
        if self.PROTECTED_FIELDS.intersection(kwargs):
            if self.filter(Q(payments__isnull=False) | Q(adjustments__isnull=False)).exists():
                raise ValidationError("Supplier invoice financial facts cannot be changed after activity is posted.")
        return super().update(**kwargs)

    def delete(self):
        if self.exists():
            raise ProtectedError(
                "Posted supplier invoices cannot be deleted; void them with a compensating adjustment.",
                self,
            )
        return super().delete()


class SupplierInvoice(models.Model):
    supplier = models.ForeignKey(Supplier, related_name="invoices", on_delete=models.PROTECT)
    location = models.ForeignKey(
        Location,
        related_name="supplier_invoices",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    invoice_no = models.CharField(max_length=100, unique=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(
        max_length=20,
        choices=(
            ("Paid", "Paid"),
            ("Pending", "Pending"),
            ("Void", "Void"),
        ),
        default="Pending",
    )
    date_issued = models.DateField()
    created_at = models.DateTimeField(auto_now_add=True)
    void_reason = models.TextField(blank=True, default="")
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="voided_invoices",
    )

    objects = SupplierInvoiceQuerySet.as_manager()
    all_objects = models.Manager()

    def __str__(self):
        return f"Invoice {self.invoice_no or self.id}"

    def clean(self):
        errors = {}
        supplier_owner_id = getattr(self.supplier, "owner_id", None)
        if supplier_owner_id:
            _validate_same_owner(
                errors,
                owner_id=supplier_owner_id,
                related_obj=self.location,
                field_name="location",
                label="Location",
            )
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=supplier_owner_id,
                actor=self.voided_by,
                field_name="voided_by",
                label="Voided by user",
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).all_objects.get(pk=self.pk)
            original_paid_amount = original.paid_amount
            if (
                Decimal(self.paid_amount or "0.00").quantize(Decimal("0.01"))
                != Decimal(original_paid_amount or "0.00").quantize(Decimal("0.01"))
            ):
                raise ValidationError(
                    "Supplier invoice paid_amount is derived from the payment ledger and cannot be changed directly."
                )
            has_activity = (
                type(self).all_objects.filter(pk=self.pk)
                .filter(Q(payments__isnull=False) | Q(adjustments__isnull=False))
                .exists()
            )
            if has_activity:
                for field in (
                    "supplier_id",
                    "location_id",
                    "invoice_no",
                    "amount",
                    "date_issued",
                    "status",
                    "void_reason",
                    "voided_at",
                    "voided_by_id",
                ):
                    if getattr(original, field) != getattr(self, field):
                        raise ValidationError(
                            "Supplier invoice financial facts cannot be changed after activity is posted."
                        )
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "Posted supplier invoices cannot be deleted; void them with a compensating adjustment.",
            {self},
        )

    @property
    def total_paid_amount(self):
        prefetched_payments = getattr(self, "_prefetched_objects_cache", {}).get("payments")
        if prefetched_payments is not None:
            total = sum(
                (payment.amount or Decimal("0.00"))
                for payment in prefetched_payments
                if not hasattr(payment, "reversal")
            )
        else:
            try:
                total = self.payments.unreversed().aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            except (OperationalError, ProgrammingError):
                total = self.paid_amount or Decimal("0.00")
        return Decimal(total).quantize(Decimal("0.01"))

    @property
    def total_credit_amount(self):
        prefetched_adjustments = getattr(self, "_prefetched_objects_cache", {}).get("adjustments")
        if prefetched_adjustments is not None:
            total = sum(
                (adjustment.amount or Decimal("0.00"))
                for adjustment in prefetched_adjustments
            )
        else:
            try:
                total = self.adjustments.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            except (OperationalError, ProgrammingError):
                total = self.amount if self.status == "Void" else Decimal("0.00")
        return Decimal(total).quantize(Decimal("0.01"))

    @property
    def total_refund_amount(self):
        prefetched_refunds = getattr(self, "_prefetched_objects_cache", {}).get("refunds")
        if prefetched_refunds is not None:
            total = sum((refund.amount or Decimal("0.00")) for refund in prefetched_refunds)
        else:
            try:
                total = self.refunds.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            except (OperationalError, ProgrammingError):
                total = Decimal("0.00")
        return Decimal(total).quantize(Decimal("0.01"))

    @property
    def net_paid_amount(self):
        return max(self.total_paid_amount - self.total_refund_amount, Decimal("0.00")).quantize(Decimal("0.01"))

    @property
    def effective_amount(self):
        amount = Decimal(self.amount or "0.00").quantize(Decimal("0.01"))
        return max(amount - self.total_credit_amount, Decimal("0.00")).quantize(Decimal("0.01"))

    @property
    def net_balance(self):
        amount = Decimal(self.amount or "0.00").quantize(Decimal("0.01"))
        return (amount - self.total_credit_amount - self.net_paid_amount).quantize(Decimal("0.01"))

    @property
    def balance_due(self):
        return max(self.net_balance, Decimal("0.00")).quantize(Decimal("0.01"))

    @property
    def supplier_credit_balance(self):
        return max(-self.net_balance, Decimal("0.00")).quantize(Decimal("0.01"))


class SupplierFinancialAppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Posted supplier financial records cannot be updated.")

    def delete(self):
        raise ProtectedError("Posted supplier financial records cannot be deleted.", self)


class SupplierInvoicePaymentQuerySet(SupplierFinancialAppendOnlyQuerySet):
    def unreversed(self):
        return self.filter(reversal__isnull=True)


class SupplierInvoicePayment(models.Model):
    invoice = models.ForeignKey(
        SupplierInvoice,
        related_name="payments",
        on_delete=models.PROTECT,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="supplier_invoice_payment_ledger",
        on_delete=models.PROTECT,
    )
    supplier = models.ForeignKey(
        Supplier,
        related_name="payment_ledger",
        on_delete=models.PROTECT,
    )
    location = models.ForeignKey(
        Location,
        related_name="supplier_invoice_payment_ledger",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    paid_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="supplier_invoice_payments_recorded",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    paid_by_username = models.CharField(max_length=150, blank=True, default="")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    payment_date = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SupplierInvoicePaymentQuerySet.as_manager()

    class Meta:
        ordering = ["-payment_date", "-id"]
        indexes = [
            models.Index(fields=["invoice"], name="supplier_pay_invoice_idx"),
            models.Index(fields=["owner", "payment_date"], name="supplier_pay_owner_date_idx"),
            models.Index(fields=["supplier", "payment_date"], name="supplier_pay_vendor_date_idx"),
            models.Index(fields=["location", "payment_date"], name="supplier_pay_loc_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="supplier_pay_amount_positive",
            ),
        ]

    def __str__(self):
        return f"{self.invoice.invoice_no or self.invoice_id} payment {self.amount}"

    def clean(self):
        errors = {}
        invoice_owner_id = getattr(getattr(self.invoice, "supplier", None), "owner_id", None)
        if invoice_owner_id:
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=invoice_owner_id,
                actor=self.paid_by,
                field_name="paid_by",
                label="Paid by user",
            )
            if self.owner_id != invoice_owner_id:
                errors["owner"] = "Payment owner must match the supplier invoice owner."
            if self.supplier_id != self.invoice.supplier_id:
                errors["supplier"] = "Payment supplier snapshot must match the invoice supplier."
            if self.location_id != self.invoice.location_id:
                errors["location"] = "Payment location snapshot must match the invoice location."
        if Decimal(self.amount or "0.00").quantize(Decimal("0.01")) <= Decimal("0.00"):
            errors["amount"] = "Supplier payment ledger entries must have a positive amount."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Posted supplier payments cannot be changed.")
        self.owner_id = self.invoice.supplier.owner_id
        self.supplier_id = self.invoice.supplier_id
        self.location_id = self.invoice.location_id
        if self.paid_by and not self.paid_by_username:
            self.paid_by_username = self.paid_by.get_username()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "Posted supplier payments cannot be deleted; create a reversal instead.",
            {self},
        )


class SupplierInvoicePaymentReversal(models.Model):
    payment = models.OneToOneField(
        SupplierInvoicePayment,
        related_name="reversal",
        on_delete=models.PROTECT,
    )
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="supplier_invoice_payment_reversals",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    reversed_by_username = models.CharField(max_length=150, blank=True, default="")
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SupplierFinancialAppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["created_at"], name="supplier_rev_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(reason=""),
                name="supplier_rev_reason_required",
            ),
        ]

    def __str__(self):
        return f"Reversal for supplier payment {self.payment_id}"

    def clean(self):
        errors = {}
        self.reason = (self.reason or "").strip()
        if not self.reason:
            errors["reason"] = "A reversal reason is required."
        payment_owner_id = getattr(self.payment, "owner_id", None)
        if payment_owner_id:
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=payment_owner_id,
                actor=self.reversed_by,
                field_name="reversed_by",
                label="Reversed by user",
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Supplier payment reversal records cannot be changed.")
        if self.reversed_by and not self.reversed_by_username:
            self.reversed_by_username = self.reversed_by.get_username()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Supplier payment reversal records cannot be deleted.", {self})


class SupplierInvoiceAdjustment(models.Model):
    TYPE_VOID_CREDIT = "void_credit"
    TYPE_SUPPLIER_CREDIT = "supplier_credit"
    TYPE_CHOICES = (
        (TYPE_VOID_CREDIT, "Void Credit"),
        (TYPE_SUPPLIER_CREDIT, "Supplier Credit Note"),
    )

    invoice = models.ForeignKey(
        SupplierInvoice,
        related_name="adjustments",
        on_delete=models.PROTECT,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="supplier_invoice_adjustment_ledger",
        on_delete=models.PROTECT,
    )
    supplier = models.ForeignKey(
        Supplier,
        related_name="invoice_adjustment_ledger",
        on_delete=models.PROTECT,
    )
    location = models.ForeignKey(
        Location,
        related_name="supplier_invoice_adjustment_ledger",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    adjustment_type = models.CharField(max_length=24, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reason = models.TextField()
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="supplier_invoice_adjustments_created",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_by_username = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SupplierFinancialAppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["invoice"], name="supplier_adj_invoice_idx"),
            models.Index(fields=["owner", "created_at"], name="supplier_adj_owner_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="supplier_adj_amount_positive",
            ),
            models.CheckConstraint(
                condition=~Q(reason=""),
                name="supplier_adj_reason_required",
            ),
            models.UniqueConstraint(
                fields=["invoice"],
                condition=Q(adjustment_type="void_credit"),
                name="supplier_adj_invoice_void_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.invoice.invoice_no or self.invoice_id} {self.adjustment_type} {self.amount}"

    def clean(self):
        errors = {}
        self.reason = (self.reason or "").strip()
        invoice_owner_id = getattr(getattr(self.invoice, "supplier", None), "owner_id", None)
        if invoice_owner_id:
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=invoice_owner_id,
                actor=self.created_by,
                field_name="created_by",
                label="Adjustment actor",
            )
            if self.owner_id != invoice_owner_id:
                errors["owner"] = "Adjustment owner must match the supplier invoice owner."
            if self.supplier_id != self.invoice.supplier_id:
                errors["supplier"] = "Adjustment supplier snapshot must match the invoice supplier."
            if self.location_id != self.invoice.location_id:
                errors["location"] = "Adjustment location snapshot must match the invoice location."
        if Decimal(self.amount or "0.00").quantize(Decimal("0.01")) <= Decimal("0.00"):
            errors["amount"] = "Supplier invoice adjustments must have a positive amount."
        if self.adjustment_type == self.TYPE_SUPPLIER_CREDIT and self.invoice_id:
            existing_credit = (
                SupplierInvoiceAdjustment.objects.filter(invoice=self.invoice)
                .exclude(pk=self.pk)
                .aggregate(total=Sum("amount"))["total"]
                or Decimal("0.00")
            )
            if Decimal(self.amount or "0.00") > max(
                Decimal(self.invoice.amount or "0.00") - existing_credit,
                Decimal("0.00"),
            ):
                errors["amount"] = "Supplier credit cannot exceed the remaining invoice value."
        if not self.reason:
            errors["reason"] = "An adjustment reason is required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Supplier invoice adjustment records cannot be changed.")
        self.owner_id = self.invoice.supplier.owner_id
        self.supplier_id = self.invoice.supplier_id
        self.location_id = self.invoice.location_id
        if self.created_by and not self.created_by_username:
            self.created_by_username = self.created_by.get_username()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Supplier invoice adjustment records cannot be deleted.", {self})


class SupplierInvoiceRefund(models.Model):
    invoice = models.ForeignKey(
        SupplierInvoice,
        related_name="refunds",
        on_delete=models.PROTECT,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="supplier_invoice_refund_ledger",
        on_delete=models.PROTECT,
    )
    supplier = models.ForeignKey(
        Supplier,
        related_name="refund_ledger",
        on_delete=models.PROTECT,
    )
    location = models.ForeignKey(
        Location,
        related_name="supplier_invoice_refund_ledger",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="supplier_invoice_refunds_received",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    received_by_username = models.CharField(max_length=150, blank=True, default="")
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference = models.CharField(max_length=80, blank=True, default="")
    reason = models.TextField()
    refund_date = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SupplierFinancialAppendOnlyQuerySet.as_manager()

    class Meta:
        ordering = ["-refund_date", "-id"]
        indexes = [
            models.Index(fields=["invoice"], name="supplier_refund_invoice_idx"),
            models.Index(fields=["owner", "refund_date"], name="supplier_refund_owner_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="supplier_refund_amount_positive",
            ),
            models.CheckConstraint(
                condition=~Q(reason=""),
                name="supplier_refund_reason_required",
            ),
            models.UniqueConstraint(
                fields=["invoice", "reference"],
                condition=~Q(reference=""),
                name="supplier_refund_invoice_reference_uniq",
            ),
        ]

    def __str__(self):
        return f"{self.invoice.invoice_no or self.invoice_id} refund {self.amount}"

    def clean(self):
        errors = {}
        self.reason = (self.reason or "").strip()
        invoice_owner_id = getattr(getattr(self.invoice, "supplier", None), "owner_id", None)
        if invoice_owner_id:
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=invoice_owner_id,
                actor=self.received_by,
                field_name="received_by",
                label="Refund actor",
            )
            if self.owner_id != invoice_owner_id:
                errors["owner"] = "Refund owner must match the supplier invoice owner."
            if self.supplier_id != self.invoice.supplier_id:
                errors["supplier"] = "Refund supplier snapshot must match the invoice supplier."
            if self.location_id != self.invoice.location_id:
                errors["location"] = "Refund location snapshot must match the invoice location."
        if Decimal(self.amount or "0.00").quantize(Decimal("0.01")) <= Decimal("0.00"):
            errors["amount"] = "Supplier refunds must have a positive amount."
        if self.invoice_id:
            refundable = self.invoice.total_paid_amount - self.invoice.total_refund_amount
            if Decimal(self.amount or "0.00").quantize(Decimal("0.01")) > refundable:
                errors["amount"] = "Supplier refund cannot exceed the unreversed paid balance."
        if not self.reason:
            errors["reason"] = "A refund reason is required."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Supplier refund records cannot be changed.")
        self.owner_id = self.invoice.supplier.owner_id
        self.supplier_id = self.invoice.supplier_id
        self.location_id = self.invoice.location_id
        if self.received_by and not self.received_by_username:
            self.received_by_username = self.received_by.get_username()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Supplier refund records cannot be deleted.", {self})


class SalesQuotation(models.Model):
    STATUS_CHOICES = (
        ("draft", "Draft"),
        ("converted", "Converted"),
        ("cancelled", "Cancelled"),
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_quotations_owned",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_quotations_created",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    customer = models.ForeignKey(
        Customer,
        related_name="sales_quotations",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    quote_no = models.CharField(max_length=32, unique=True, blank=True, null=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="draft")
    valid_until = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    converted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner"], name="sales_quote_owner_idx"),
            models.Index(fields=["status"], name="sales_quote_status_idx"),
        ]

    def __str__(self):
        return self.quote_no or f"Quote {self.id}"
    
    def clean(self):
        errors = {}
        if self.owner_id:
            _validate_same_owner(
                errors,
                owner_id=self.owner_id,
                related_obj=self.customer,
                field_name="customer",
                label="Customer",
            )
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=self.owner_id,
                actor=self.created_by,
                field_name="created_by",
                label="Created by user",
            )
        if errors:
            raise ValidationError(errors)

    def recalculate_totals(self, save=True):
        subtotal, tax_amount, total_amount = _document_line_totals(
            self.items.select_related("item").all(),
            self.notes,
        )

        self.subtotal = subtotal
        self.tax_amount = tax_amount
        self.total_amount = total_amount

        if save:
            self.save(
                update_fields=[
                    "subtotal",
                    "tax_amount",
                    "total_amount",
                ]
            )

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        self.full_clean()
        super().save(*args, **kwargs)
        if is_new and not self.quote_no:
            self.quote_no = f"QTE-{self.id:06d}"
            super().save(update_fields=["quote_no"])


class SalesQuotationItem(models.Model):
    quotation = models.ForeignKey(
        SalesQuotation,
        related_name="items",
        on_delete=models.CASCADE,
    )
    item = models.ForeignKey(Item, related_name="quotation_items", on_delete=models.PROTECT)
    item_name = models.CharField(max_length=120, blank=True, default="")
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.quotation.quote_no or self.quotation_id}: {self.item_name or self.item.name}"

    def clean(self):
        errors = {}
        quotation_owner_id = getattr(self.quotation, "owner_id", None)
        if quotation_owner_id:
            _validate_same_owner(
                errors,
                owner_id=quotation_owner_id,
                related_obj=self.item,
                field_name="item",
                label="Item",
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        if not self.item_name:
            self.item_name = self.item.name
        self.line_total = (Decimal(self.quantity or 0) * (self.unit_price or Decimal("0.00"))).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)


class SalesInvoiceQuerySet(models.QuerySet):
    PROTECTED_FIELDS = {
        "owner", "owner_id", "customer", "customer_id", "location", "location_id",
        "invoice_no", "client_reference", "notes", "subtotal", "tax_amount", "total_amount", "issued_at",
    }

    def update(self, **kwargs):
        if self.PROTECTED_FIELDS.intersection(kwargs):
            if self.filter(Q(payments__isnull=False) | Q(credit_notes__isnull=False)).exists():
                raise ValidationError("Posted sales invoice facts cannot be changed after financial activity.")
        return super().update(**kwargs)

    def delete(self):
        if self.exists():
            raise ProtectedError("Sales invoice history cannot be deleted.", self)
        return super().delete()


class SalesInvoice(models.Model):
    STATUS_CHOICES = (
        ("draft", "Draft"),
        ("issued", "Issued"),
        ("paid", "Paid"),
        ("void", "Void"),
    )
    COLLECTION_STATUS_CHOICES = (
        ("untracked", "Pickup not recorded"),
        ("pending_payment", "Pending full payment"),
        ("awaiting_collection", "Remaining in store"),
        ("collected_immediately", "Collected same time"),
        ("collected_after_hold", "Delivered from uncollected"),
    )

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_invoices_owned",
        on_delete=models.CASCADE,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_invoices_created",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    quotation = models.OneToOneField(
        SalesQuotation,
        related_name="converted_invoice",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    customer = models.ForeignKey(
        Customer,
        related_name="sales_invoices",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    location = models.ForeignKey(
        Location,
        related_name="sales_invoices",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    invoice_no = models.CharField(max_length=32, unique=True, blank=True, null=True)
    client_reference = models.CharField(max_length=64, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="issued")
    collection_status = models.CharField(
        max_length=24,
        choices=COLLECTION_STATUS_CHOICES,
        default="pending_payment",
        db_index=True,
    )
    collection_status_changed_at = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField(null=True, blank=True)
    collection_recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_invoice_collections_recorded",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    notes = models.TextField(blank=True, default="")
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    issued_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["owner"], name="sales_inv_owner_idx"),
            models.Index(fields=["status"], name="sales_inv_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "client_reference"],
                condition=~Q(client_reference=""),
                name="sales_inv_owner_client_ref_uniq",
            ),
        ]

    def __str__(self):
        return self.invoice_no or f"Invoice {self.id}"

    @property
    def has_financial_activity(self):
        return self.payments.exists() or self.credit_notes.exists()

    def clean(self):
        errors = {}
        if self.owner_id:
            _validate_same_owner(
                errors,
                owner_id=self.owner_id,
                related_obj=self.quotation,
                field_name="quotation",
                label="Quotation",
            )
            _validate_same_owner(
                errors,
                owner_id=self.owner_id,
                related_obj=self.customer,
                field_name="customer",
                label="Customer",
            )
            _validate_same_owner(
                errors,
                owner_id=self.owner_id,
                related_obj=self.location,
                field_name="location",
                label="Location",
            )
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=self.owner_id,
                actor=self.created_by,
                field_name="created_by",
                label="Created by user",
            )
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=self.owner_id,
                actor=self.collection_recorded_by,
                field_name="collection_recorded_by",
                label="Collection recorded by user",
            )
        if errors:
            raise ValidationError(errors)
    
    def recalculate_totals(self, save=True):
        subtotal, tax_amount, total_amount = _document_line_totals(
            self.items.select_related("item").all(),
            self.notes,
        )

        if self.pk and self.has_financial_activity:
            current = (
                Decimal(self.subtotal or "0.00").quantize(Decimal("0.01")),
                Decimal(self.tax_amount or "0.00").quantize(Decimal("0.01")),
                Decimal(self.total_amount or "0.00").quantize(Decimal("0.01")),
            )
            calculated = (subtotal, tax_amount, total_amount)
            if calculated != current:
                raise ValidationError("Posted sales invoice totals are historical facts and cannot be recalculated.")

        self.subtotal = subtotal
        self.tax_amount = tax_amount
        self.total_amount = total_amount

        if save:
            self.save(
                update_fields=[
                    "subtotal",
                    "tax_amount",
                    "total_amount",
                ]
            )

    def save(self, *args, **kwargs):
        is_new = self.pk is None
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if original.has_financial_activity:
                for field in SalesInvoiceQuerySet.PROTECTED_FIELDS:
                    if getattr(original, field.rstrip("_id"), None) != getattr(self, field.rstrip("_id"), None):
                        raise ValidationError("Posted sales invoice facts cannot be changed after financial activity.")
        self.full_clean()
        super().save(*args, **kwargs)
        if is_new and not self.invoice_no:
            self.invoice_no = f"SINV-{self.id:06d}"
            super().save(update_fields=["invoice_no"])

    def delete(self, *args, **kwargs):
        raise ProtectedError("Sales invoice history cannot be deleted.", {self})

    @property
    def total_paid_amount(self):
        recorded_total = self.payments.unreversed().aggregate(total=models.Sum("amount"))["total"] or Decimal("0.00")
        return recorded_total.quantize(Decimal("0.01"))

    @property
    def balance_due(self):
        remaining = self.effective_total_amount - self.total_paid_amount
        return max(remaining, Decimal("0.00")).quantize(Decimal("0.01"))

    @property
    def credit_note_total_amount(self):
        prefetched_credit_notes = getattr(self, "_prefetched_objects_cache", {}).get("credit_notes")
        if prefetched_credit_notes is not None:
            total = sum((note.amount or Decimal("0.00")) for note in prefetched_credit_notes)
        else:
            try:
                total = self.credit_notes.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
            except (OperationalError, ProgrammingError):
                total = Decimal("0.00")
        return Decimal(total).quantize(Decimal("0.01"))

    @property
    def effective_total_amount(self):
        adjusted_total = (self.total_amount or Decimal("0.00")) - self.credit_note_total_amount
        return max(adjusted_total, Decimal("0.00")).quantize(Decimal("0.01"))

    objects = SalesInvoiceQuerySet.as_manager()


class SalesInvoiceItemQuerySet(models.QuerySet):
    PROTECTED_FIELDS = {
        "invoice", "invoice_id", "item", "item_id", "item_name", "quantity", "unit_price", "line_total",
    }

    def update(self, **kwargs):
        if self.PROTECTED_FIELDS.intersection(kwargs):
            if self.filter(Q(invoice__payments__isnull=False) | Q(invoice__credit_notes__isnull=False)).exists():
                raise ValidationError("Posted sales invoice lines cannot be changed.")
        return super().update(**kwargs)

    def delete(self):
        if self.filter(Q(invoice__payments__isnull=False) | Q(invoice__credit_notes__isnull=False)).exists():
            raise ProtectedError("Posted sales invoice lines cannot be deleted.", self)
        return super().delete()


class SalesInvoiceItem(models.Model):
    invoice = models.ForeignKey(
        SalesInvoice,
        related_name="items",
        on_delete=models.CASCADE,
    )
    item = models.ForeignKey(Item, related_name="invoice_items", on_delete=models.PROTECT)
    item_name = models.CharField(max_length=120, blank=True, default="")
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    line_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    objects = SalesInvoiceItemQuerySet.as_manager()

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return f"{self.invoice.invoice_no or self.invoice_id}: {self.item_name or self.item.name}"

    def clean(self):
        errors = {}
        invoice_owner_id = getattr(self.invoice, "owner_id", None)
        if invoice_owner_id:
            _validate_same_owner(
                errors,
                owner_id=invoice_owner_id,
                related_obj=self.item,
                field_name="item",
                label="Item",
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.get(pk=self.pk)
            if original.invoice.has_financial_activity:
                for field in ("invoice_id", "item_id", "item_name", "quantity", "unit_price", "line_total"):
                    if getattr(original, field) != getattr(self, field):
                        raise ValidationError("Posted sales invoice lines cannot be changed.")
        elif self.invoice.has_financial_activity:
            raise ValidationError("Posted sales invoice lines cannot be added.")
        self.full_clean()
        if not self.item_name:
            self.item_name = self.item.name
        self.line_total = (Decimal(self.quantity or 0) * (self.unit_price or Decimal("0.00"))).quantize(Decimal("0.01"))
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.invoice.has_financial_activity:
            raise ProtectedError("Posted sales invoice lines cannot be deleted.", {self})
        return super().delete(*args, **kwargs)


class SalesInvoicePaymentQuerySet(models.QuerySet):
    PROTECTED_FIELDS = {
        "invoice",
        "invoice_id",
        "owner",
        "owner_id",
        "customer",
        "customer_id",
        "location",
        "location_id",
        "shift",
        "shift_id",
        "received_by",
        "received_by_id",
        "received_by_username",
        "amount",
        "applied_customer_credit",
        "credited_customer_overpayment",
        "payment_method",
        "payment_date",
    }

    def unreversed(self):
        return self.filter(reversal__isnull=True)

    def update(self, **kwargs):
        protected = self.PROTECTED_FIELDS.intersection(kwargs)
        if protected:
            raise ValidationError(
                f"Posted payment fields cannot be updated: {', '.join(sorted(protected))}."
            )
        return super().update(**kwargs)

    def delete(self):
        raise ProtectedError(
            "Posted payment records cannot be deleted; create a reversal instead.",
            self,
        )


class SalesInvoicePayment(models.Model):
    METHOD_CHOICES = (
        ("account_credit", "Account Credit"),
        ("cash", "Cash"),
        ("debit_card", "Debit Card"),
        ("credit_card", "Credit Card"),
        ("card", "Card (Legacy)"),
        ("jamdex", "JAM-DEX"),
        ("bank_transfer", "Bank Transfer"),
        ("mobile_money", "Mobile Money"),
        ("cheque", "Cheque"),
        ("other", "Other"),
    )

    invoice = models.ForeignKey(
        SalesInvoice,
        related_name="payments",
        on_delete=models.PROTECT,
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_invoice_payment_ledger",
        on_delete=models.PROTECT,
    )
    customer = models.ForeignKey(
        Customer,
        related_name="sales_invoice_payment_ledger",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    location = models.ForeignKey(
        Location,
        related_name="sales_invoice_payment_ledger",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    shift = models.ForeignKey(
        CashShift,
        related_name="invoice_payments",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_invoice_payments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    received_by_username = models.CharField(max_length=150, blank=True, default="")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    applied_customer_credit = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    credited_customer_overpayment = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    payment_method = models.CharField(max_length=20, choices=METHOD_CHOICES, default="cash")
    reference = models.CharField(max_length=80, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    payment_date = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = SalesInvoicePaymentQuerySet.as_manager()

    class Meta:
        ordering = ["-payment_date", "-id"]
        indexes = [
            models.Index(fields=["invoice"], name="sales_pay_invoice_idx"),
            models.Index(fields=["payment_date"], name="sales_pay_date_idx"),
            models.Index(fields=["owner", "payment_date"], name="sales_pay_owner_date_idx"),
            models.Index(fields=["location", "payment_date"], name="sales_pay_loc_date_idx"),
            models.Index(fields=["shift", "payment_date"], name="sales_pay_shift_date_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="sales_pay_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(applied_customer_credit__gte=Decimal("0.00")),
                name="sales_pay_credit_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(credited_customer_overpayment__gte=Decimal("0.00")),
                name="sales_pay_overpay_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(applied_customer_credit__lte=F("amount")),
                name="sales_pay_credit_lte_amount",
            ),
            models.CheckConstraint(
                condition=Q(credited_customer_overpayment__lte=F("amount")),
                name="sales_pay_overpay_lte_amount",
            ),
        ]

    def __str__(self):
        return f"{self.invoice.invoice_no or self.invoice_id} payment {self.amount}"

    def clean(self):
        errors = {}
        invoice_owner_id = getattr(self.invoice, "owner_id", None)
        if invoice_owner_id:
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=invoice_owner_id,
                actor=self.received_by,
                field_name="received_by",
                label="Received by user",
            )
            if not self.pk:
                if self.owner_id and self.owner_id != invoice_owner_id:
                    errors["owner"] = "Payment owner must match the invoice owner."
                if self.customer_id != getattr(self.invoice, "customer_id", None):
                    errors["customer"] = "Payment customer snapshot must match the invoice customer."
                if self.location_id != getattr(self.invoice, "location_id", None):
                    errors["location"] = "Payment location snapshot must match the invoice location."
        amount = Decimal(self.amount or "0.00").quantize(Decimal("0.01"))
        applied_credit = Decimal(self.applied_customer_credit or "0.00").quantize(Decimal("0.01"))
        credited_overpayment = Decimal(self.credited_customer_overpayment or "0.00").quantize(Decimal("0.01"))
        if amount <= Decimal("0.00"):
            errors["amount"] = "Payment ledger entries must have a positive amount."
        if applied_credit < Decimal("0.00"):
            errors["applied_customer_credit"] = "Applied customer credit cannot be negative."
        if credited_overpayment < Decimal("0.00"):
            errors["credited_customer_overpayment"] = "Credited customer overpayment cannot be negative."
        if applied_credit > amount:
            errors["applied_customer_credit"] = "Applied customer credit cannot exceed the payment amount."
        if credited_overpayment > amount:
            errors["credited_customer_overpayment"] = "Credited overpayment cannot exceed the payment amount."
        if credited_overpayment > Decimal("0.00") and not getattr(self.invoice, "customer_id", None):
            errors["credited_customer_overpayment"] = (
                "Credited overpayments require an invoice customer account."
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            original = type(self).objects.only(
                "invoice_id",
                "owner_id",
                "customer_id",
                "location_id",
                "shift_id",
                "received_by_id",
                "received_by_username",
                "amount",
                "applied_customer_credit",
                "credited_customer_overpayment",
                "payment_method",
                "payment_date",
            ).get(pk=self.pk)
            changed_fields = [
                field_name
                for field_name in (
                    "invoice_id",
                    "owner_id",
                    "customer_id",
                    "location_id",
                    "shift_id",
                    "received_by_id",
                    "received_by_username",
                    "amount",
                    "applied_customer_credit",
                    "credited_customer_overpayment",
                    "payment_method",
                    "payment_date",
                )
                if getattr(original, field_name) != getattr(self, field_name)
            ]
            if changed_fields:
                raise ValidationError(
                    f"Posted payment fields cannot be changed: {', '.join(changed_fields)}."
                )
        else:
            self.owner_id = self.invoice.owner_id
            self.customer_id = self.invoice.customer_id
            self.location_id = self.invoice.location_id
            if self.shift_id:
                if self.shift.owner_id and self.shift.owner_id != self.owner_id:
                    raise ValidationError("Payment shift must belong to the invoice tenant.")
                if self.shift.location_id != self.location_id:
                    raise ValidationError("Payment shift must match the invoice location.")
                if self.shift.is_closed:
                    raise ValidationError("Payments cannot be posted to a closed shift.")
            if self.received_by and not self.received_by_username:
                self.received_by_username = self.received_by.get_username()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError(
            "Posted payment records cannot be deleted; create a reversal instead.",
            {self},
        )


class AppendOnlyFinancialQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Posted financial records cannot be updated.")

    def delete(self):
        raise ProtectedError("Posted financial records cannot be deleted.", self)


class SalesInvoiceCreditNote(models.Model):
    REASON_CHOICES = (
        ("pricing_adjustment", "Pricing Adjustment"),
        ("returned_items", "Returned Items"),
        ("damaged_goods", "Damaged Goods"),
        ("billing_error", "Billing Error"),
        ("goodwill", "Goodwill Credit"),
        ("other", "Other"),
    )

    invoice = models.ForeignKey(
        SalesInvoice,
        related_name="credit_notes",
        on_delete=models.PROTECT,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_invoice_credit_notes",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_by_username = models.CharField(max_length=150, blank=True, default="")
    amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    reason = models.CharField(max_length=32, choices=REASON_CHOICES, default="pricing_adjustment")
    notes = models.TextField(blank=True, default="")
    credited_customer_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyFinancialQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["invoice"], name="sales_credit_invoice_idx"),
            models.Index(fields=["created_at"], name="sales_credit_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(amount__gt=Decimal("0.00")),
                name="sales_credit_amount_positive",
            ),
            models.CheckConstraint(
                condition=Q(credited_customer_amount__gte=Decimal("0.00")),
                name="sales_credit_customer_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(credited_customer_amount__lte=F("amount")),
                name="sales_credit_customer_lte_amount",
            ),
        ]

    def __str__(self):
        return f"{self.invoice.invoice_no or self.invoice_id} credit {self.amount}"

    def clean(self):
        errors = {}
        invoice_owner_id = getattr(self.invoice, "owner_id", None)
        if invoice_owner_id:
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=invoice_owner_id,
                actor=self.created_by,
                field_name="created_by",
                label="Created by user",
            )
        if (self.amount or Decimal("0.00")) <= Decimal("0.00"):
            errors["amount"] = "Credit note amount must be greater than zero."
        credited_customer_amount = Decimal(self.credited_customer_amount or "0.00").quantize(Decimal("0.01"))
        amount = Decimal(self.amount or "0.00").quantize(Decimal("0.01"))
        if credited_customer_amount < Decimal("0.00"):
            errors["credited_customer_amount"] = "Credited customer amount cannot be negative."
        if credited_customer_amount > amount:
            errors["credited_customer_amount"] = "Credited customer amount cannot exceed the credit note amount."
        if credited_customer_amount > Decimal("0.00") and not getattr(self.invoice, "customer_id", None):
            errors["credited_customer_amount"] = "Customer credit requires an invoice customer."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Posted credit notes cannot be changed.")
        if self.created_by and not self.created_by_username:
            self.created_by_username = self.created_by.get_username()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Posted credit notes cannot be deleted.", {self})


class SalesInvoicePaymentReversal(models.Model):
    payment = models.OneToOneField(
        SalesInvoicePayment,
        related_name="reversal",
        on_delete=models.PROTECT,
    )
    reversed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sales_invoice_payment_reversals",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    reversed_by_username = models.CharField(max_length=150, blank=True, default="")
    reason = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyFinancialQuerySet.as_manager()

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["created_at"], name="sales_rev_created_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(reason=""),
                name="sales_rev_reason_required",
            ),
        ]

    def __str__(self):
        return f"Reversal for payment {self.payment_id}"

    def clean(self):
        errors = {}
        reason = (self.reason or "").strip()
        if not reason:
            errors["reason"] = "A reversal reason is required."
        payment_owner_id = getattr(self.payment, "owner_id", None) or getattr(
            getattr(self.payment, "invoice", None),
            "owner_id",
            None,
        )
        if payment_owner_id:
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=payment_owner_id,
                actor=self.reversed_by,
                field_name="reversed_by",
                label="Reversed by user",
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Payment reversal records cannot be changed.")
        self.reason = (self.reason or "").strip()
        if self.reversed_by and not self.reversed_by_username:
            self.reversed_by_username = self.reversed_by.get_username()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Payment reversal records cannot be deleted.", {self})


class CustomerCreditMovement(models.Model):
    TYPE_OPENING_BALANCE = "opening_balance"
    TYPE_MANUAL_ADJUSTMENT = "manual_adjustment"
    TYPE_INVOICE_OVERPAYMENT = "invoice_overpayment"
    TYPE_INVOICE_CREDIT_APPLICATION = "invoice_credit_application"
    TYPE_INVOICE_CREDIT_NOTE = "invoice_credit_note"
    TYPE_PAYMENT_REVERSAL = "payment_reversal"
    TYPE_POS_CREDIT_APPLICATION = "pos_credit_application"
    TYPE_POS_OVERPAYMENT = "pos_overpayment"
    TYPE_CHOICES = (
        (TYPE_OPENING_BALANCE, "Opening Balance"),
        (TYPE_MANUAL_ADJUSTMENT, "Manual Adjustment"),
        (TYPE_INVOICE_OVERPAYMENT, "Invoice Overpayment"),
        (TYPE_INVOICE_CREDIT_APPLICATION, "Invoice Credit Application"),
        (TYPE_INVOICE_CREDIT_NOTE, "Invoice Credit Note"),
        (TYPE_PAYMENT_REVERSAL, "Payment Reversal"),
        (TYPE_POS_CREDIT_APPLICATION, "POS Credit Application"),
        (TYPE_POS_OVERPAYMENT, "POS Overpayment"),
    )

    customer = models.ForeignKey(
        Customer,
        related_name="credit_movements",
        on_delete=models.PROTECT,
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    movement_type = models.CharField(max_length=32, choices=TYPE_CHOICES)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="customer_credit_movements",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_by_username = models.CharField(max_length=150, blank=True, default="")
    payment = models.ForeignKey(
        SalesInvoicePayment,
        related_name="customer_credit_movements",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    payment_reversal = models.ForeignKey(
        SalesInvoicePaymentReversal,
        related_name="customer_credit_movements",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    credit_note = models.ForeignKey(
        SalesInvoiceCreditNote,
        related_name="customer_credit_movements",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    sale = models.ForeignKey(
        Sale,
        related_name="customer_credit_movements",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
    )
    reason = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = AppendOnlyFinancialQuerySet.as_manager()

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["customer", "created_at"], name="cust_credit_ledger_idx"),
            models.Index(fields=["movement_type"], name="cust_credit_type_idx"),
        ]
        constraints = [
            models.CheckConstraint(
                condition=~Q(amount=Decimal("0.00")),
                name="cust_credit_move_nonzero",
            ),
        ]

    def __str__(self):
        return f"{self.customer_id}: {self.amount} ({self.movement_type})"

    def clean(self):
        errors = {}
        if Decimal(self.amount or "0.00").quantize(Decimal("0.01")) == Decimal("0.00"):
            errors["amount"] = "Customer credit movements cannot be zero."
        _validate_actor_in_owner_tenant(
            errors,
            owner_id=self.customer.owner_id,
            actor=self.created_by,
            field_name="created_by",
            label="Created by user",
        )

        payment_types = {
            self.TYPE_INVOICE_OVERPAYMENT,
            self.TYPE_INVOICE_CREDIT_APPLICATION,
        }
        sale_types = {
            self.TYPE_POS_CREDIT_APPLICATION,
            self.TYPE_POS_OVERPAYMENT,
        }
        if self.movement_type in payment_types and not self.payment_id:
            errors["payment"] = "Invoice credit movements require a source payment."
        if self.movement_type == self.TYPE_INVOICE_CREDIT_NOTE and not self.credit_note_id:
            errors["credit_note"] = "Credit-note movements require a source credit note."
        if self.movement_type == self.TYPE_PAYMENT_REVERSAL and not self.payment_reversal_id:
            errors["payment_reversal"] = "Reversal movements require a source reversal."
        if self.movement_type in sale_types and not self.sale_id:
            errors["sale"] = "POS credit movements require a source sale."

        for source_name in ("payment", "credit_note"):
            source = getattr(self, source_name, None)
            source_customer_id = getattr(source, "customer_id", None)
            if source_customer_id and source_customer_id != self.customer_id:
                errors[source_name] = "Credit movement customer must match its source customer."
        if self.payment_reversal_id:
            source_customer_id = getattr(self.payment_reversal.payment, "customer_id", None)
            if source_customer_id and source_customer_id != self.customer_id:
                errors["payment_reversal"] = "Credit movement customer must match the reversed payment."

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Customer credit movements cannot be changed.")
        if self.created_by and not self.created_by_username:
            self.created_by_username = self.created_by.get_username()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ProtectedError("Customer credit movements cannot be deleted.", {self})


class UserProfile(models.Model):
    STATUS_CHOICES = (
        ("pending", "Pending"),
        ("active", "Active"),
        ("suspended", "Suspended"),
    )
    ROLE_CHOICES = (
        ("admin", "Admin"),      
        ("manager", "Manager"),  
        ("cashier", "Cashier"),  
    )
    PLAN_CHOICES = (
        ("TRIAL", "Trial"),
        ("FREE", "Free"),
        ("PRO", "Professional"),
    )
    THEME_CHOICES = (
        ("system", "System Default"),
        ("light", "Light"),
        ("dark", "Dark"),
    )

    # --- Core Authentication Relationship ---
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        related_name="profile", 
        on_delete=models.CASCADE
    )
    
    # --- Hierarchy & Multi-Tenancy Ownership ---
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="admin")
    parent_admin = models.ForeignKey(
        'self', 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True, 
        related_name="staff_profiles",
        help_text="The Admin account profile this staff member belongs to."
    )

    # --- Subscription & Billing State ---
    plan = models.CharField(max_length=10, choices=PLAN_CHOICES, default="TRIAL")
    plan_start = models.DateTimeField(default=timezone.now)
    plan_end = models.DateTimeField(null=True, blank=True) 
    pro_expires = models.DateField(null=True, blank=True) 

    # --- UI Preferences & Location Locks ---
    theme = models.CharField(max_length=10, choices=THEME_CHOICES, default="system")
    pos_config = models.TextField(default="", blank=True)
    default_location = models.ForeignKey(
        'Location', 
        related_name="default_users",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    can_edit_daily_summary = models.BooleanField(
        default=False,
        help_text="Allows a manager or cashier to use Daily Summary when granted by an admin or superuser.",
    )
    
    # --- Receipt Customization & Branding ---
    receipt_brand_name = models.CharField(max_length=150, blank=True, default="")
    receipt_logo_url = models.URLField(blank=True, default="")
    receipt_contact_email = models.CharField(max_length=120, blank=True, default="")
    receipt_contact_phone = models.CharField(max_length=50, blank=True, default="")
    receipt_address = models.CharField(max_length=255, blank=True, default="")
    receipt_logo = models.ImageField(upload_to='logos/', blank=True, null=True)

    # --- Security, Audit Logs & Compliance ---
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="pending", db_index=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    archived_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="archived_profiles",
    )
    archive_reason = models.TextField(blank=True, default="")
    terms_accepted = models.BooleanField(default=False)
    terms_accepted_at = models.DateTimeField(null=True, blank=True)
    terms_version = models.CharField(max_length=20, default="", blank=True)
    signup_ip = models.GenericIPAddressField(null=True, blank=True)

    class Meta:
        ordering = ["user__username"]
        indexes = [
            models.Index(fields=["role"], name="inventory_u_role_72f4b8_idx"),
            models.Index(fields=["plan"], name="inventory_u_plan_0278ab_idx"),
            models.Index(fields=["status"], name="inventory_u_status_idx"),
        ]

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    # -------------------------------------------------------------------------
    # Properties & Domain Methods
    # -------------------------------------------------------------------------

    @property
    def effective_owner(self):
        """
        Always resolves to the top-level Admin standard Django User object.
        Used extensively to scope database queries dynamically.
        """
        # If I am the admin, or I have no parent, I am the owner.
        if self.role == 'admin' or not self.parent_admin:
            return self.user
            
        # If I am staff, return my parent admin's auth User instance safely
        return self.parent_admin.user

    @classmethod
    def for_user(cls, user):
        """Safely fetches a user profile, gracefully fallback creating one if absent."""
        profile, _ = cls.objects.get_or_create(user=user)
        return profile

    def get_effective_plan_owner(self):
        """Returns the specific UserProfile object containing the billing tier details."""
        return self.parent_admin if self.parent_admin else self

    def get_staff(self):
        """Returns a pre-fetched queryset of all operational staff under this Admin."""
        if self.role != 'admin':
            return UserProfile.objects.none()
        return self.staff_profiles.all().select_related('user', 'default_location')

    def is_trial_active(self):
        """Validates if the underlying tenant is in an open, unexpired trial state."""
        if self.status != 'active':
            return False
            
        owner = self.get_effective_plan_owner()
        if owner.plan != "TRIAL" or not owner.plan_end:
            return False
        return timezone.now() <= owner.plan_end
    
    def is_pro_active(self):
        """
        Evaluates whether this profile has access to premium app workflows.
        Gracefully handles Superuser bypass and local authorization suspensions.
        """
        if self.user.is_superuser:
            return True
        
        # Security Guard: Stop suspended or pending personnel immediately
        if self.status != 'active':
            return False
        
        # Cascade state to the account manager/billing owner
        sub_holder = self.get_effective_plan_owner()
        
        if sub_holder.plan == 'PRO':
            now_date = timezone.now().date()
            now_dt = timezone.now()

            # Security Fix: Paid accounts must have an expiration date to be considered active
            if not sub_holder.pro_expires and not sub_holder.plan_end:
                return False

            # Prefer the dedicated paid expiry field when present.
            if sub_holder.pro_expires:
                return sub_holder.pro_expires >= now_date

            if sub_holder.plan_end and sub_holder.plan_end < now_dt:
                return False
            return True

        return False

    @property
    def plan_badge_state(self):
        """Return a compact UI state for plan badges."""
        if self.is_pro_active():
            return "pro"
        if self.is_trial_active():
            return "trial"
        if self.plan in {"PRO", "TRIAL"}:
            return "expired"
        return "basic"

    @property
    def plan_badge_label(self):
        """Return the label that should appear in compact plan badges."""
        if self.is_pro_active():
            return "PRO"
        if self.is_trial_active():
            return "TRIAL"
        if self.plan == "PRO":
            return "EXPIRED"
        if self.plan == "TRIAL":
            return "TRIAL EXPIRED"
        if self.plan == "FREE":
            return "FREE"
        return (self.get_plan_display() or self.plan or "").upper()
    
from django.db import models
from django.contrib.auth.models import User

class UserVerification(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="verification")
    code = models.CharField(max_length=6)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} - {self.code}"
    

class AuditLog(models.Model):
    ACTION_CHOICES = (
        ("login", "Login"),
        ("logout", "Logout"),
        ("sale", "Sale"),
        ("payment", "Payment"),
        ("staff", "Staff"),
        ("inventory", "Inventory"),
        ("supplier", "Supplier"),
        ("invoice", "Invoice"),
        ("settings", "Settings"),
    )
    SEVERITY_CHOICES = (
        ("info", "Info"),
        ("warn", "Warning"),
        ("error", "Error"),
    )

    action = models.CharField(max_length=30, choices=ACTION_CHOICES)
    severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES, default="info")
    message = models.CharField(max_length=255)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sync_id = models.UUIDField(default=uuid.uuid4, db_index=True, null=True)
    sync_type = models.CharField(max_length=20, blank=True, null=True)
    direction = models.CharField(max_length=20, blank=True, null=True)
    status = models.CharField(max_length=20, blank=True, null=True)
    items_pushed = models.PositiveIntegerField(default=0)
    items_pulled = models.PositiveIntegerField(default=0)
    items_failed = models.PositiveIntegerField(default=0)
    conflicts_detected = models.PositiveIntegerField(default=0)
    conflicts_resolved = models.PositiveIntegerField(default=0)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True, null=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="audit_logs",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["action"], name="inventory_a_action_d6359b_idx"),
            models.Index(fields=["created_at"], name="inventory_a_created_1da8ce_idx"),
        ]

    def __str__(self):
        return f"{self.action}: {self.message}"

class StockRecord(models.Model):
    item = models.ForeignKey(
        'Item', 
        on_delete=models.CASCADE, 
        related_name="stock_at_locations"
    )
    location = models.ForeignKey(
        'Location', 
        on_delete=models.CASCADE, 
        related_name="stock_records"
    )
    quantity = models.PositiveIntegerField(
        default=0,
        help_text="The actual physical inventory count at this specific branch location."
    )
    low_stock_threshold = models.PositiveIntegerField(
        default=5,
        help_text="Alert point for low inventory notifications at this branch."
    )
    last_restocked = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("item", "location")
        verbose_name = "Stock Record"
        verbose_name_plural = "Stock Records"
        indexes = [
            models.Index(fields=["item", "location"], name="stock_item_loc_idx"),
            models.Index(fields=["location", "quantity"], name="stock_loc_qty_idx"),
        ]

    def __str__(self):
        return f"{self.item.name} at {self.location.name}: {self.quantity}"

    def clean(self):
        errors = {}
        item_owner_id = getattr(self.item, "owner_id", None)
        if item_owner_id:
            _validate_same_owner(
                errors,
                owner_id=item_owner_id,
                related_obj=self.location,
                field_name="location",
                label="Location",
            )
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @classmethod
    def get_or_create_locked(cls, item, location):
        """
        Defensive design: Fetches or creates a stock record using an atomic 
        transaction boundary to protect against onboarding signal sync gaps.
        """
        with transaction.atomic():
            # Select for update ensures concurrency protection at checkout
            record, created = cls.objects.select_for_update().get_or_create(
                item=item,
                location=location,
                defaults={'quantity': 0}
            )
            return record

class StockTransfer(models.Model):
    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    from_location = models.ForeignKey(Location, related_name="transfers_out", on_delete=models.CASCADE)
    to_location = models.ForeignKey(Location, related_name="transfers_in", on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    STATUS_CHOICES = [('PENDING', 'Pending'), ('COMPLETED', 'Completed')]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    timestamp = models.DateTimeField(auto_now_add=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="stock_transfers",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )

    def __str__(self):
        return f"Transfer {self.quantity} of {self.item}"

    def clean(self):
        errors = {}
        item_owner_id = getattr(self.item, "owner_id", None)
        if item_owner_id:
            _validate_same_owner(
                errors,
                owner_id=item_owner_id,
                related_obj=self.from_location,
                field_name="from_location",
                label="Source location",
            )
            _validate_same_owner(
                errors,
                owner_id=item_owner_id,
                related_obj=self.to_location,
                field_name="to_location",
                label="Destination location",
            )
            _validate_actor_in_owner_tenant(
                errors,
                owner_id=item_owner_id,
                actor=self.user,
                field_name="user",
                label="Transfer user",
            )
        if self.from_location_id and self.to_location_id and self.from_location_id == self.to_location_id:
            errors["to_location"] = "Destination location must differ from source location."
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)



# =====================================================
# SIGNALS
# =====================================================

@receiver(post_save, sender=SalesQuotationItem)
def quotation_item_saved(sender, instance, **kwargs):
    instance.quotation.recalculate_totals()


@receiver(post_delete, sender=SalesQuotationItem)
def quotation_item_deleted(sender, instance, **kwargs):
    instance.quotation.recalculate_totals()


@receiver(post_save, sender=SalesInvoiceItem)
def invoice_item_saved(sender, instance, **kwargs):
    instance.invoice.recalculate_totals()


@receiver(post_delete, sender=SalesInvoiceItem)
def invoice_item_deleted(sender, instance, **kwargs):
    instance.invoice.recalculate_totals()
