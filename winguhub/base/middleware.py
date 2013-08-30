from django.core.cache import cache

from seaserv import get_binding_peerids, get_orgs_by_user

from winguhub.notifications.models import Notification
from winguhub.notifications.utils import refresh_cache
try:
    from winguhub.settings import CLOUD_MODE
except ImportError:
    CLOUD_MODE = False

class BaseMiddleware(object):
    """
    Middleware that add organization info to request when user in organization
    context.
    """
    
    def process_request(self, request):
        if CLOUD_MODE:
            request.cloud_mode = True
            
            # Get all orgs user created.
            # orgs = get_orgs_by_user(request.user.username)
            # request.user.orgs = orgs
        else:
            request.cloud_mode = False

        request.user.org = None
        request.user.orgs = None
            
        return None

    def process_response(self, request, response):
        return response
    
class InfobarMiddleware(object):
    """Query info bar close status, and store into reqeust."""

    def get_from_db(self):
        ret = Notification.objects.all().filter(primary=1)
        refresh_cache()
        return ret

    def process_request(self, request):
        topinfo_close = request.COOKIES.get('info_id', '')

        cur_note = cache.get('CUR_TOPINFO') if cache.get('CUR_TOPINFO') else \
            self.get_from_db()
        if not cur_note:
            request.cur_note = None
        else:
            if str(cur_note[0].id) in topinfo_close.split('_'):
                request.cur_note = None
            else:
                request.cur_note = cur_note[0]

        return None
            
    def process_response(self, request, response):
        return response
