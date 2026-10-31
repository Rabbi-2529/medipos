from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrPhoneBackend(ModelBackend):
    """Authenticate with an email address or a phone number."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        User = get_user_model()
        identifier = (username or kwargs.get(User.USERNAME_FIELD) or "").strip()
        if not identifier or password is None:
            return None
        matches = list(
            User._default_manager.filter(Q(email__iexact=identifier) | Q(phone=identifier))[:2]
        )
        if len(matches) != 1:
            # Run the hasher anyway so unknown and ambiguous logins take the same time.
            User().set_password(password)
            return None
        user = matches[0]
        if user.check_password(password) and self.user_can_authenticate(user):
            return user
        return None
