from datetime import timedelta

from django.db import transaction
from django.utils import timezone


BILLING_CYCLE_DAYS = {
    "monthly": 30,
    "yearly": 365,
}


def normalize_billing_cycle(value):
    return "monthly" if str(value).lower() == "monthly" else "yearly"


def get_subscription_owner(profile):
    """
    Return the company profile that owns the subscription.

    Managers and cashiers inherit subscription access from their
    parent administrator.
    """
    if not profile:
        raise ValueError("A user profile is required.")

    return profile.get_effective_plan_owner()


@transaction.atomic
def initialize_trial_subscription(profile, *, days=14):
    """
    Prepare a newly registered account for email verification.
    The account remains pending and inactive.
    """
    now = timezone.now()

    profile.plan = "TRIAL"
    profile.status = "pending"
    profile.plan_start = now
    profile.plan_end = now + timedelta(days=days)
    profile.pro_expires = None

    profile.save(
        update_fields=[
            "plan",
            "status",
            "plan_start",
            "plan_end",
            "pro_expires",
        ]
    )

    return profile


@transaction.atomic
def activate_verified_account(profile):
    """
    Activate an account after successful email verification.
    """
    now = timezone.now()

    if profile.plan == "TRIAL":
        if not profile.plan_start:
            profile.plan_start = now

        if not profile.plan_end:
            profile.plan_end = now + timedelta(days=14)

        profile.pro_expires = None

    profile.status = "active"

    profile.save(
        update_fields=[
            "status",
            "plan_start",
            "plan_end",
            "pro_expires",
        ]
    )

    profile.user.is_active = True
    profile.user.save(update_fields=["is_active"])

    return profile


@transaction.atomic
def activate_pro_subscription(
    profile,
    *,
    billing_cycle="yearly",
    extend_existing=True,
):
    """
    Activate or renew Pro on the effective company subscription owner.
    """
    owner = get_subscription_owner(profile)
    cycle = normalize_billing_cycle(billing_cycle)
    days = BILLING_CYCLE_DAYS[cycle]
    today = timezone.localdate()

    if (
        extend_existing
        and owner.plan == "PRO"
        and owner.pro_expires
        and owner.pro_expires >= today
    ):
        term_start = owner.pro_expires
    else:
        term_start = today

    owner.plan = "PRO"
    owner.status = "active"
    owner.plan_start = timezone.now()
    owner.plan_end = None
    owner.pro_expires = term_start + timedelta(days=days)

    owner.save(
        update_fields=[
            "plan",
            "status",
            "plan_start",
            "plan_end",
            "pro_expires",
        ]
    )

    owner.user.is_active = True
    owner.user.save(update_fields=["is_active"])

    return owner


@transaction.atomic
def activate_trial_subscription(profile, *, days=14):
    """
    Start a fresh active trial for the effective company owner.
    """
    owner = get_subscription_owner(profile)
    now = timezone.now()

    owner.plan = "TRIAL"
    owner.status = "active"
    owner.plan_start = now
    owner.plan_end = now + timedelta(days=days)
    owner.pro_expires = None

    owner.save(
        update_fields=[
            "plan",
            "status",
            "plan_start",
            "plan_end",
            "pro_expires",
        ]
    )

    owner.user.is_active = True
    owner.user.save(update_fields=["is_active"])

    return owner


@transaction.atomic
def activate_starter_subscription(profile):
    """
    Move the effective company owner to Starter access.
    """
    owner = get_subscription_owner(profile)

    owner.plan = "FREE"
    owner.status = "active"
    owner.plan_start = None
    owner.plan_end = None
    owner.pro_expires = None

    owner.save(
        update_fields=[
            "plan",
            "status",
            "plan_start",
            "plan_end",
            "pro_expires",
        ]
    )

    owner.user.is_active = True
    owner.user.save(update_fields=["is_active"])

    return owner
