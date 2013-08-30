from django.http import Http404
from django.shortcuts import render_to_response
from django.template import RequestContext
from seaserv import get_repo, is_passwd_set

from winguhub.utils import check_and_get_org_by_repo, check_and_get_org_by_group

def sys_staff_required(func):
    """
    Decorator for views that checks the user is system staff.
    """
    def _decorated(request, *args, **kwargs):
        if request.user.is_staff:
            return func(request, *args, **kwargs)
        raise Http404
    return _decorated
    
# def ctx_switch_required(func):
#     """
#     Decorator for views to change navigation bar automatically that render
#     same template when both in org context and personal context.
#     """
#     def _decorated(request, *args, **kwargs):
#         if not request.cloud_mode:
#             # no need to switch context when `CLOUD_MODE` is false
#             request.user.org = None
#             request.base_template = 'myhome_base.html'
#             return func(request, *args, **kwargs)
    
#         repo_id = kwargs.get('repo_id', '')
#         group_id = kwargs.get('group_id', '')
#         if repo_id and group_id:
#             return func(request, *args, **kwargs)
#         if not repo_id and not group_id:
#             return func(request, *args, **kwargs)
            
#         user = request.user.username
#         if repo_id:
#             org, base_template = check_and_get_org_by_repo(repo_id, user)

#         if group_id:
#             org, base_template = check_and_get_org_by_group(int(group_id), user)

#         if org:
#             request.user.org = org._dict
#         else:
#             request.user.org = None
#         request.base_template = base_template
#         return func(request, *args, **kwargs)
#     return _decorated

def repo_passwd_set_required(func):
    """
    Decorator for views to redirect user to repo decryption page if repo is
    encrypt and password is not set by user.
    """
    def _decorated(request, *args, **kwargs):
        repo_id = kwargs.get('repo_id', None)
        if not repo_id:
            raise Exception, 'Repo id is not found in url.'
        repo = get_repo(repo_id)
        if not repo:
            raise Http404
        username = request.user.username
        if repo.encrypted and not is_passwd_set(repo_id, username):
            # Redirect uesr to decrypt repo page.
            return render_to_response('decrypt_repo_form.html', {
                    'repo': repo,
                    'next': request.get_full_path(),
                    }, context_instance=RequestContext(request))
        return func(request, *args, **kwargs)
    return _decorated
            
