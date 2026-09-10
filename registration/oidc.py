"""Keycloak claim mapping for the server-rendered Django application."""

from django.conf import settings
from django.contrib.auth import get_user_model
from mozilla_django_oidc.auth import OIDCAuthenticationBackend


class FasilkomOIDCAuthenticationBackend(OIDCAuthenticationBackend):
    """Create or update the local account from verified Keycloak UserInfo claims."""

    def _username(self, claims):
        configured_claim = getattr(settings, 'KEYCLOAK_USERNAME_CLAIM', 'username')
        username = claims.get(configured_claim) or claims.get('username') or claims.get('preferred_username') or claims.get('sub')
        if not username:
            username = claims.get('email')
        return str(username or '').strip().lower()[:150]

    def _npm(self, claims):
        configured_claim = getattr(settings, 'KEYCLOAK_NPM_CLAIM', 'kodeIdentitas')
        for claim_name in (configured_claim, 'kodeIdentitas', 'npm', 'student_id', 'studentid', 'kode_identitas'):
            value = claims.get(claim_name)
            if value:
                return str(value).strip()
        return ''

    def _roles(self, claims):
        configured_claim = getattr(settings, 'KEYCLOAK_ROLE_CLAIM', 'role')
        role_values = claims.get(configured_claim, claims.get('role', claims.get('roles', [])))
        if isinstance(role_values, str):
            role_values = [role_values]
        roles = {str(role).strip().lower() for role in (role_values or []) if role}

        realm_roles = (claims.get('realm_access') or {}).get('roles', [])
        roles.update(str(role).strip().lower() for role in realm_roles if role)

        for client_data in (claims.get('resource_access') or {}).values():
            roles.update(str(role).strip().lower() for role in client_data.get('roles', []) if role)
        return roles

    def _role_names(self, setting_name, default):
        configured_roles = getattr(settings, setting_name, default)
        return {role.strip().lower() for role in configured_roles.split(',') if role.strip()}

    def _user_type(self, claims):
        roles = self._roles(claims)
        lecturer_roles = self._role_names('KEYCLOAK_LECTURER_ROLES', 'dosen')
        alumni_roles = self._role_names('KEYCLOAK_ALUMNI_ROLES', 'alumni,staf')
        student_roles = self._role_names('KEYCLOAK_STUDENT_ROLES', 'mahasiswa')

        if roles & lecturer_roles:
            return 'LECTURER'
        if roles & alumni_roles:
            return 'ALUMNI'
        if roles & student_roles:
            return 'STUDENT'
        return 'ALUMNI'

    def _sync_user(self, user, claims):
        email = str(claims.get('email') or '').strip().lower()
        npm = self._npm(claims)
        configured_claim = getattr(settings, 'KEYCLOAK_NAME_CLAIM', 'given_name')
        full_name = str(
            claims.get(configured_claim)
            or claims.get('given_name')
            or claims.get('given name')
            or claims.get('name')
            or claims.get('username')
            or claims.get('preferred_username')
            or ''
        ).strip()
        update_fields = []

        if email and user.email.lower() != email:
            user.email = email
            update_fields.append('email')
        user_type = self._user_type(claims)
        if user.user_type != user_type:
            user.user_type = user_type
            update_fields.append('user_type')
        if npm and user.npm != npm:
            user.npm = npm
            update_fields.append('npm')
        if full_name:
            first_name, _, last_name = full_name.partition(' ')
            if user.first_name != first_name:
                user.first_name = first_name
                update_fields.append('first_name')
            if user.last_name != last_name:
                user.last_name = last_name
                update_fields.append('last_name')
        if update_fields:
            user.save(update_fields=list(dict.fromkeys(update_fields)))
        return user

    def verify_claims(self, claims):
        """An email is required to safely match a Keycloak identity to a local account."""
        return bool(claims.get('email'))

    def filter_users_by_claims(self, claims):
        email = str(claims.get('email') or '').strip()
        username = self._username(claims)
        if username:
            username_matches = self.UserModel.objects.filter(username__iexact=username).order_by('id')
            if username_matches.count() == 1:
                return username_matches

        if email:
            email_matches = self.UserModel.objects.filter(email__iexact=email).order_by('id')
            if email_matches.count() == 1:
                return email_matches

        return self.UserModel.objects.none()

    def create_user(self, claims):
        email = str(claims.get('email') or '').strip().lower()
        username = self._username(claims)
        if not username:
            raise ValueError('Claim username dari Keycloak tidak tersedia.')

        user = get_user_model().objects.create_user(
            username=username,
            email=email,
            user_type=self._user_type(claims),
            npm=self._npm(claims) or None,
        )
        user.set_unusable_password()
        user.save(update_fields=['password'])
        return self._sync_user(user, claims)

    def update_user(self, user, claims):
        return self._sync_user(user, claims)
