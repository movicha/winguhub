from django.conf import settings
from django.core.cache import cache
from django import template

from winguhub.avatar.settings import (GROUP_AVATAR_DEFAULT_SIZE, AVATAR_CACHE_TIMEOUT,
                             GROUP_AVATAR_DEFAULT_URL)
from winguhub.avatar.models import GroupAvatar
from winguhub.avatar.util import get_grp_cache_key

register = template.Library()

def get_default_group_avatar_url():
    base_url = getattr(settings, 'STATIC_URL', None)
    if not base_url:
        base_url = getattr(settings, 'MEDIA_URL', '') 
    # Don't use base_url if the default avatar url starts with http:// of https://
    if GROUP_AVATAR_DEFAULT_URL.startswith('http://') or GROUP_AVATAR_DEFAULT_URL.startswith('https://'):
        return GROUP_AVATAR_DEFAULT_URL
    # We'll be nice and make sure there are no duplicated forward slashes
    ends = base_url.endswith('/')
    begins = GROUP_AVATAR_DEFAULT_URL.startswith('/')
    if ends and begins:
        base_url = base_url[:-1]
    elif not ends and not begins:
        return '%s/%s' % (base_url, GROUP_AVATAR_DEFAULT_URL)
    return '%s%s' % (base_url, GROUP_AVATAR_DEFAULT_URL)

@register.simple_tag
def grp_avatar(group_id, size=GROUP_AVATAR_DEFAULT_SIZE):
    # Get from cache
    key = get_grp_cache_key(group_id, size)
    val = cache.get(key)
    if val:
        return val

    # Get from DB, and refresh cache
    grp_avatars = GroupAvatar.objects.filter(group_id=group_id)
    if grp_avatars:
        avatar = grp_avatars.order_by('-date_uploaded')[0]
    else:
        avatar = None

    if avatar:
        if not avatar.thumbnail_exists(size):
            avatar.create_thumbnail(size)
        url = avatar.avatar_url(size)
    else:
        url = get_default_group_avatar_url()

    img = """<img src="%s" alt="" width="%s" height="%s" class="avatar" />""" % (url, size, size)
    cache.set(key, img, AVATAR_CACHE_TIMEOUT)
    return img
