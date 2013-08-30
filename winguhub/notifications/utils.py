from django.core.cache import cache

from winguhub.notifications.models import Notification
from winguhub.notifications.settings import NOTIFICATION_CACHE_TIMEOUT
def refresh_cache():
    """
    Function to be called when change primary notification.
    """
    cache.set('CUR_TOPINFO', Notification.objects.all().filter(primary=1),
              NOTIFICATION_CACHE_TIMEOUT)
    
