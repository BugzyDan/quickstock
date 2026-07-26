from functools import wraps
from django.shortcuts import redirect
from django.contrib import messages

def _get_billing_owner_profile(user):
    """
    Safely retrieves the account owner. 
    Staff roles (manager/cashier) inherit the PRO status of their parent_admin.
    """
    profile = getattr(user, 'profile', None)
    if not profile:
        return None
    
    # If the user is staff, check if they are linked to a parent_admin
    if profile.role in ['manager', 'cashier'] and profile.parent_admin:
        return profile.parent_admin
    
    return profile

def pro_required(view_func):
    """
    Feature gate decorator: Restricts access to users belonging to active PRO accounts.
    """
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        if request.user.is_superuser:
            return view_func(request, *args, **kwargs)

        billing_profile = _get_billing_owner_profile(request.user)
        
        # Verify billing profile exists and PRO status is active
        if billing_profile and billing_profile.is_pro_active():
            return view_func(request, *args, **kwargs)

        # Handle unauthorized access
        msg = "This is a QuickStock JA PRO feature. Upgrade to unlock."
        storage = messages.get_messages(request)
        
        # Prevent flash message duplication
        if not any(m.message == msg for m in storage):
            messages.info(request, msg)

        return redirect('upgrade')
    return wrapper

def role_required(allowed_roles):
    """
    RBAC decorator: Restricts access based on UserProfile roles.
    """
    if isinstance(allowed_roles, str):
        allowed_roles = {allowed_roles}
    else:
        allowed_roles = set(allowed_roles)

    def decorator(view_func):
        @wraps(view_func)
        def wrapper(request, *args, **kwargs):
            if request.user.is_superuser:
                return view_func(request, *args, **kwargs)

            profile = getattr(request.user, 'profile', None)
            
            # Strict role validation
            if profile and profile.role in allowed_roles:
                return view_func(request, *args, **kwargs)

            messages.error(request, "Insufficient permissions to access this area.")
            return redirect('dashboard')
        return wrapper
    return decorator
