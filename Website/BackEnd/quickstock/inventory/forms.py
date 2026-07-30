from django import forms
from .models import Supplier
from django import forms
from .models import Customer



class SupplierForm(forms.ModelForm):
    """
    Form for creating or updating Supplier instances.

    Features:
        - Custom widgets for better UI/UX.
        - Validation for required fields and proper formatting.
    """
    class Meta:
        model = Supplier
        fields = [
            "supplier_type",
            "name",
            "contact_name",
            "phone",
            "email",
            "country_code",
            "address",
        ]

        labels = {
            "supplier_type": "Supplier type",
            "country_code": "Country / Market",
            "address": "Business Address / City or Region",
        }

        # Custom widgets for Bootstrap styling and placeholders
        widgets = {
            "supplier_type": forms.RadioSelect(
                attrs={"class": "supplier-type-input"}
            ),
            "name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter company name",
                    "required": True,  # HTML5 required attribute
                }
            ),
            "contact_name": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter contact person name (e.g. Sales Rep)",
                }
            ),
            "phone": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter phone number",
                }
            ),
            "email": forms.EmailInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Enter email address",
                }
            ),
            "country_code": forms.Select(
                attrs={
                    "class": "form-control",
                    "autocomplete": "country",
                }
            ),
            "address": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "placeholder": "Street, city, parish, state, or region",
                    "rows": 3,
                    "autocomplete": "street-address",
                }
            ),
        }

    # ----------------------------
    # Field-specific validation
    # ----------------------------

    def clean_name(self):
        """
        Ensure supplier name is provided and trimmed of extra spaces.
        """
        name = self.cleaned_data.get("name")
        if not name:
            raise forms.ValidationError("Supplier name is required.")
        return name.strip()

    def clean_contact_name(self):
        """
        Trim spaces from contact name if provided.
        """
        contact = self.cleaned_data.get("contact_name")
        return contact.strip() if contact else contact

    def clean_phone(self):
        """
        Validate that phone contains at least one digit and trim spaces.
        """
        phone = self.cleaned_data.get("phone")
        if phone:
            phone = phone.strip()
            if not any(char.isdigit() for char in phone):
                raise forms.ValidationError("Phone number must contain digits.")
        return phone

    def clean_email(self):
        """
        Normalize email to lowercase and trim spaces.
        """
        email = self.cleaned_data.get("email")
        if email:
            email = email.lower().strip()
        return email


class CustomerForm(forms.ModelForm):
    new_note = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "class": "form-input",
                "rows": 4,
                "placeholder": "Add a dated account note without overwriting older entries.",
            }
        ),
    )

    class Meta:
        model = Customer
        fields = ['name', 'email', 'phone', 'trn', 'is_tax_exempt', 'physical_address', 'business_address', 'notes']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-input', 'placeholder': 'Full Name'}),
            'email': forms.EmailInput(attrs={'class': 'form-input', 'placeholder': 'email@example.com'}),
            'phone': forms.TextInput(attrs={'class': 'form-input', 'placeholder': '876-000-0000'}),
            'trn': forms.TextInput(attrs={'class': 'form-input', 'placeholder': '123-456-789', 'inputmode': 'numeric', 'maxlength': 11}),
            'is_tax_exempt': forms.CheckboxInput(attrs={'class': 'form-checkbox'}),
            'physical_address': forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': 'Home, delivery, or physical location address.'}),
            'business_address': forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': 'Company, billing, or workplace address.'}),
            'notes': forms.Textarea(attrs={'class': 'form-input', 'rows': 5, 'placeholder': 'Billing preferences, delivery instructions, or standing account details.'}),
        }

        labels = {
            'physical_address': 'Physical Address',
            'business_address': 'Business Address',
            'trn': 'TRN / Tax ID',
            'is_tax_exempt': 'Exempt from 15% GCT',
            'notes': 'Account profile notes',
        }

    def clean_new_note(self):
        return (self.cleaned_data.get("new_note") or "").strip()
