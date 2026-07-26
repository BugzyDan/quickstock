# inventory/tokens.py
from django.contrib.auth.tokens import PasswordResetTokenGenerator

class AccountActivationTokenGenerator(PasswordResetTokenGenerator):
    """
    Custom token generator for activating user accounts via email links.

    This is similar to Django's PasswordResetTokenGenerator but is used
    specifically for account activation, ensuring the token is invalidated
    once the user is active.
    """
    def _make_hash_value(self, user, timestamp):
        """
        Generate a hash value used to create the token.

        Args:
            user (User): Django user instance
            timestamp (int): Current timestamp used in token generation

        Returns:
            str: Hashable string combining user primary key, timestamp, and activation state
        """
        return str(user.pk) + str(timestamp) + str(user.is_active)


# Singleton instance to be imported wherever needed
token_generator = AccountActivationTokenGenerator()
