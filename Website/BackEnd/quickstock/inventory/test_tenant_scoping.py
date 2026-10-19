"""Guard tests for centralized tenant scoping."""

from django.contrib.auth.models import User
from django.test import TestCase
from inventory.models import Category, Customer, Location
from inventory.tenant import TENANT_PATHS, TENANT_OWNED_MODELS, effective_owner, scoped


class TenantScopingTests(TestCase):
    def setUp(self):
        self.owner_a = User.objects.create_user("tenant-a", "a@example.com", "pw")
        self.owner_b = User.objects.create_user("tenant-b", "b@example.com", "pw")
        self.staff = User.objects.create_user("tenant-a-staff", "staff@example.com", "pw")

        profile = self.owner_a.profile
        profile.role, profile.status = "admin", "active"
        profile.save(update_fields=["role", "status"])

        staff_profile = self.staff.profile
        staff_profile.role, staff_profile.status = "cashier", "active"
        staff_profile.parent_admin = profile
        staff_profile.save(update_fields=["role", "status", "parent_admin"])

        self.customer_a = Customer.objects.create(owner=self.owner_a, name="Tenant A Customer")
        self.customer_b = Customer.objects.create(owner=self.owner_b, name="Tenant B Customer")

    def test_scoped_returns_only_own_tenant(self):
        result = scoped(Customer, self.owner_a)

        self.assertEqual(list(result), [self.customer_a])

    def test_staff_scope_follows_parent_admin(self):
        self.assertEqual(effective_owner(self.staff), self.owner_a)
        self.assertEqual(list(scoped(Customer, self.staff)), [self.customer_a])

    def test_anonymous_users_get_nothing(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertFalse(scoped(Customer, None).exists())
        self.assertFalse(scoped(Customer, AnonymousUser()).exists())

    def test_superuser_has_platform_oversight(self):
        admin = User.objects.create_superuser("platform", "platform@example.com", "pw")

        self.assertEqual(scoped(Customer, admin).count(), 2)

    def test_every_declared_model_supports_scoping(self):
        from django.apps import apps

        for model_name, path in TENANT_PATHS.items():
            model = apps.get_model("inventory", model_name)
            with self.subTest(model=model_name):
                # The lookup must resolve against the real schema.
                scoped(model, self.owner_a).count()

    def test_scoped_excludes_other_tenant_categories(self):
        mine = Category.objects.create(owner=self.owner_a, name="Mine")
        Category.objects.create(owner=self.owner_b, name="Theirs")

        self.assertEqual(list(scoped(Category, self.owner_a)), [mine])

    def test_scoped_locations_are_isolated(self):
        mine = Location.objects.create(owner=self.owner_a, name="A Branch")
        Location.objects.create(owner=self.owner_b, name="B Branch")

        self.assertEqual(list(scoped(Location, self.owner_a)), [mine])
