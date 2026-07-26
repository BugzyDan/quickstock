# inventory/utils.py

def get_effective_owner(user):
    """
    Standardizes data ownership across the platform.
    Ensures that Cashiers and Managers act on behalf of the Business Owner.
    """
    if not user or user.is_anonymous:
        return None
        
    # UserProfile declares related_name="profile". Keep the legacy relation
    # fallback for compatibility with older deployments that may still expose it.
    profile = getattr(user, "profile", None) or getattr(user, "userprofile", None)
    
    if profile and profile.effective_owner:
        # If this is a staff member, return their boss (the primary account)
        return profile.effective_owner
        
    # If there is no 'effective_owner' assigned, this user is the primary owner
    return user
