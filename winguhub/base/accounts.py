# encoding: utf-8
from django import forms
from django.utils.encoding import smart_str
from django.utils.hashcompat import md5_constructor, sha_constructor
from django.utils.translation import ugettext_lazy as _
from django.conf import settings
from django.contrib.sites.models import RequestSite
from django.contrib.sites.models import Site

from winguhub.auth.models import get_hexdigest, check_password
from winguhub.auth import login
from registration import signals
#from registration.forms import RegistrationForm
from seaserv import ccnet_threaded_rpc, unset_repo_passwd, is_passwd_set

from winguhub.profile.models import Profile


UNUSABLE_PASSWORD = '!' # This will never be a valid hash

class UserManager(object):
    def create_user(self, email, password=None, is_staff=False, is_active=False):
        """
        Creates and saves a User with given username and password.
        """
        # Normalize the address by lowercasing the domain part of the email
        # address.
        try:
            email_name, domain_part = email.strip().split('@', 1)
        except ValueError:
            pass
        else:
            email = '@'.join([email_name, domain_part.lower()])

        user = User(email=email)
        user.is_staff = is_staff
        user.is_active = is_active
        user.set_password(password)
        user.save()

        return self.get(email=email)

    def create_superuser(self, email, password):
        u = self.create_user(email, password, is_staff=True, is_active=True)
        return u
    
    def get(self, email=None, id=None):
        if not email and not id:
            raise User.DoesNotExist, 'User matching query does not exits.'
            
        if email:
            emailuser = ccnet_threaded_rpc.get_emailuser(email)
        if id:
            emailuser = ccnet_threaded_rpc.get_emailuser_by_id(id)
        if not emailuser:
            raise User.DoesNotExist, 'User matching query does not exits.'
    
        user = User(emailuser.email)
        user.id = emailuser.id
        user.is_staff = emailuser.is_staff
        user.is_active = emailuser.is_active
        user.ctime = emailuser.ctime
        user.org = emailuser.org
        return user

class User(object):
    is_staff = False
    is_active = False
    is_superuser = False
    groups = []
    org = None
    objects = UserManager()

    class DoesNotExist(Exception):
        pass
    
    def __init__(self, email):
        self.username = email
        self.email = email

    def __unicode__(self):
        return self.username

    def is_anonymous(self):
        """
        Always returns False. This is a way of comparing User objects to
        anonymous users.
        """
        return False
    
    def is_authenticated(self):
        """
        Always return True. This is a way to tell if the user has been
        authenticated in templates.
        """
        return True

    def save(self):
        emailuser = ccnet_threaded_rpc.get_emailuser(self.username)
        if emailuser:
            if not hasattr(self, 'password'):
                self.set_unusable_password()
            ccnet_threaded_rpc.update_emailuser(emailuser.id,
                                                self.password,
                                                int(self.is_staff),
                                                int(self.is_active))
        else:
            ccnet_threaded_rpc.add_emailuser(self.username, self.password,
                                             int(self.is_staff),
                                             int(self.is_active))

    def delete(self):
        """
        When delete user, we should also delete group relationships.
        """
        # TODO: what about repo and org?
        ccnet_threaded_rpc.remove_emailuser(self.username)
        ccnet_threaded_rpc.remove_group_user(self.username)
        Profile.objects.filter(user=self.username).delete()

    def get_and_delete_messages(self):
        messages = []
        return messages
    
    def set_password(self, raw_password):
        if raw_password is None:
            self.set_unusable_password()
        else:
            self.password = '%s' % raw_password
    
    def check_password(self, raw_password):
        """
        Returns a boolean of whether the raw_password was correct. Handles
        encryption formats behind the scenes.
        """
        # Backwards-compatibility check. Older passwords won't include the
        # algorithm or salt.

        # if '$' not in self.password:
        #     is_correct = (self.password == \
        #                       get_hexdigest('sha1', '', raw_password))
        #     return is_correct
        return (ccnet_threaded_rpc.validate_emailuser(self.username, raw_password) == 0)

    def set_unusable_password(self):
        # Sets a value that will never be a valid hash
        self.password = UNUSABLE_PASSWORD
    
    def email_user(self, subject, message, from_email=None):
        "Sends an e-mail to this User."
        from django.core.mail import send_mail
        send_mail(subject, message, from_email, [self.email])

    def remove_repo_passwds(self):
        """
        Remove all repo decryption passwords stored on server.
        """
        from winguhub.utils import get_user_repos
        owned_repos, shared_repos, groups_repos, public_repos = get_user_repos(self.email)

        def has_repo(repos, repo):
            for r in repos:
                if repo.id == r.id:
                    return True
            return False

        passwd_setted_repos = []
        for r in owned_repos + groups_repos:
            if not has_repo(passwd_setted_repos, r) and r.encrypted and \
                    is_passwd_set(r.id, self.email):
                passwd_setted_repos.append(r)
        for r in shared_repos + public_repos:
            # For compatibility with diffrent fields names in Repo and
            # SharedRepo objects.
            r.id = r.repo_id
            r.name = r.repo_name
            r.desc = r.repo_desc

            if not has_repo(passwd_setted_repos, r) and r.encrypted and \
                    is_passwd_set(r.id, self.email):
                passwd_setted_repos.append(r)

        for r in passwd_setted_repos:
            unset_repo_passwd(r.id, self.email)

class AuthBackend(object):
    def get_user(self, username):
        try:
            user = User.objects.get(email=username)
        except User.DoesNotExist:
            user = None
        return user

    def authenticate(self, username=None, password=None):
        try:
            user = User.objects.get(email=username)
            if user.check_password(password):
                return user
        except User.DoesNotExist:
            return None
            
class RegistrationBackend(object):
    """
    A registration backend which follows a simple workflow:

    1. User signs up, inactive account is created.

    2. Email is sent to user with activation link.

    3. User clicks activation link, account is now active.

    Using this backend requires that

    * ``registration`` be listed in the ``INSTALLED_APPS`` setting
      (since this backend makes use of models defined in this
      application).

    * The setting ``ACCOUNT_ACTIVATION_DAYS`` be supplied, specifying
      (as an integer) the number of days from registration during
      which a user may activate their account (after that period
      expires, activation will be disallowed).

    * The creation of the templates
      ``registration/activation_email_subject.txt`` and
      ``registration/activation_email.txt``, which will be used for
      the activation email. See the notes for this backends
      ``register`` method for details regarding these templates.

    Additionally, registration can be temporarily closed by adding the
    setting ``REGISTRATION_OPEN`` and setting it to
    ``False``. Omitting this setting, or setting it to ``True``, will
    be interpreted as meaning that registration is currently open and
    permitted.

    Internally, this is accomplished via storing an activation key in
    an instance of ``registration.models.RegistrationProfile``. See
    that model and its custom manager for full documentation of its
    fields and supported operations.
    
    """
    def register(self, request, **kwargs):
        """
        Given a username, email address and password, register a new
        user account, which will initially be inactive.

        Along with the new ``User`` object, a new
        ``registration.models.RegistrationProfile`` will be created,
        tied to that ``User``, containing the activation key which
        will be used for this account.

        An email will be sent to the supplied email address; this
        email should contain an activation link. The email will be
        rendered using two templates. See the documentation for
        ``RegistrationProfile.send_activation_email()`` for
        information about these templates and the contexts provided to
        them.

        After the ``User`` and ``RegistrationProfile`` are created and
        the activation email is sent, the signal
        ``registration.signals.user_registered`` will be sent, with
        the new ``User`` as the keyword argument ``user`` and the
        class of this backend as the sender.

        """
        email, password = kwargs['email'], kwargs['password1']
        username = email
        if Site._meta.installed:
            site = Site.objects.get_current()
        else:
            site = RequestSite(request)

        from registration.models import RegistrationProfile
        if settings.ACTIVATE_AFTER_REGISTRATION == True:
            # since user will be activated after registration,
            # so we will not use email sending, just create acitvated user
            new_user = RegistrationProfile.objects.create_active_user(username, email,
                                                                        password, site,
                                                                        send_email=False)
            # login the user
            new_user.backend=settings.AUTHENTICATION_BACKENDS[0]
            
            login(request, new_user)
        else:
            # create inactive user, user can be activated by admin, or through activated email
            new_user = RegistrationProfile.objects.create_inactive_user(username, email,
                                                                        password, site,
                                                                        send_email=settings.REGISTRATION_SEND_MAIL)

        userid = kwargs['userid']
        if userid:
            ccnet_threaded_rpc.add_binding(new_user.username, userid)

        signals.user_registered.send(sender=self.__class__,
                                     user=new_user,
                                     request=request)
        return new_user

    def activate(self, request, activation_key):
        """
        Given an an activation key, look up and activate the user
        account corresponding to that key (if possible).

        After successful activation, the signal
        ``registration.signals.user_activated`` will be sent, with the
        newly activated ``User`` as the keyword argument ``user`` and
        the class of this backend as the sender.
        
        """
        from registration.models import RegistrationProfile
        activated = RegistrationProfile.objects.activate_user(activation_key)
        if activated:
            signals.user_activated.send(sender=self.__class__,
                                        user=activated,
                                        request=request)
            # login the user
            activated.backend=settings.AUTHENTICATION_BACKENDS[0]
            login(request, activated)

        return activated

    def registration_allowed(self, request):
        """
        Indicate whether account registration is currently permitted,
        based on the value of the setting ``REGISTRATION_OPEN``. This
        is determined as follows:

        * If ``REGISTRATION_OPEN`` is not specified in settings, or is
          set to ``True``, registration is permitted.

        * If ``REGISTRATION_OPEN`` is both specified and set to
          ``False``, registration is not permitted.
        
        """
        return getattr(settings, 'REGISTRATION_OPEN', True)

    def get_form_class(self, request):
        """
        Return the default form class used for user registration.
        
        """
        return RegistrationForm

    def post_registration_redirect(self, request, user):
        """
        Return the name of the URL to redirect to after successful
        user registration.
        
        """
        return ('registration_complete', (), {})

    def post_activation_redirect(self, request, user):
        """
        Return the name of the URL to redirect to after successful
        account activation.
        
        """
        return ('myhome', (), {})


class RegistrationForm(forms.Form):
    """
    Form for registering a new user account.
    
    Validates that the requested email is not already in use, and
    requires the password to be entered twice to catch typos.    
    """
    attrs_dict = { 'class': 'required' }

    email = forms.EmailField(widget=forms.TextInput(attrs=dict(attrs_dict,
                                                               maxlength=75)),
                             label=_("Email address"))
    userid = forms.RegexField(regex=r'^\w+$',
                              max_length=40,
                              required=False,
                              widget=forms.TextInput(),
                              label=_("Username"),
                              error_messages={ 'invalid': _("This value must be of length 40") })

    password1 = forms.CharField(widget=forms.PasswordInput(attrs=attrs_dict, render_value=False),
                                label=_("Password"))
    password2 = forms.CharField(widget=forms.PasswordInput(attrs=attrs_dict, render_value=False),
                                label=_("Password (again)"))

    def clean_email(self):
        email = self.cleaned_data['email']
        emailuser = ccnet_threaded_rpc.get_emailuser(email)
        if not emailuser:
            return self.cleaned_data['email']
        else:
            raise forms.ValidationError(_("A user with this email already"))

    def clean_userid(self):
        if self.cleaned_data['userid'] and len(self.cleaned_data['userid']) != 40:
            raise forms.ValidationError(_("Invalid user id."))
        return self.cleaned_data['userid']

    def clean(self):
        """
        Verifiy that the values entered into the two password fields
        match. Note that an error here will end up in
        ``non_field_errors()`` because it doesn't apply to a single
        field.
        
        """
        if 'password1' in self.cleaned_data and 'password2' in self.cleaned_data:
            if self.cleaned_data['password1'] != self.cleaned_data['password2']:
                raise forms.ValidationError(_("The two password fields didn't match."))
        return self.cleaned_data

