# encoding: utf-8
import os
import re
import urllib2
import uuid
import logging
import json
import tempfile
import locale
import ConfigParser
from datetime import datetime

import ccnet

from django.core.urlresolvers import reverse
from django.contrib.sites.models import RequestSite
from django.db import IntegrityError
from django.shortcuts import render_to_response
from django.template import RequestContext
from django.utils.hashcompat import sha_constructor, md5_constructor
from django.utils.translation import ugettext as _
from django.http import HttpResponseRedirect, HttpResponse
from django.utils.http import urlquote

from htmldiff import HtmlDiff

from pysearpc import SearpcError
from seaserv import wingufile_api
from seaserv import seafserv_rpc, ccnet_threaded_rpc, seafserv_threaded_rpc, \
    get_repo, get_commits, get_group_repoids, CCNET_SERVER_ADDR, \
    CCNET_SERVER_PORT, get_org_id_by_repo_id, get_org_by_id, is_org_staff, \
    get_org_id_by_group, list_share_repos, get_org_group_repos, \
    get_personal_groups_by_user, list_personal_repos_by_owner, get_group_repos, \
    list_org_repos_by_owner, get_org_groups_by_user, check_permission, \
    list_inner_pub_repos, list_org_inner_pub_repos, CCNET_CONF_PATH, SERVICE_URL
import winguhub.settings
try:
    from winguhub.settings import EVENTS_CONFIG_FILE
except ImportError:
    EVENTS_CONFIG_FILE = None
try:
    from winguhub.settings import EMAIL_HOST
    IS_EMAIL_CONFIGURED = True
except ImportError:
    IS_EMAIL_CONFIGURED = False
try:
    from winguhub.settings import CLOUD_MODE
except ImportError:
    CLOUD_MODE = False
try:
    from winguhub.settings import ENABLE_INNER_HTTPSERVER
except ImportError:
    ENABLE_INNER_HTTPSERVER = True
    
from file_types import *

# Get an instance of a logger
logger = logging.getLogger(__name__)

EMPTY_SHA1 = '0000000000000000000000000000000000000000'
MAX_INT = 2147483647 

PREVIEW_FILEEXT = {
    TEXT: ('ac', 'am', 'bat', 'c', 'cc', 'cmake', 'cpp', 'cs', 'css', 'diff', 'el', 'h', 'html', 'htm', 'java', 'js', 'json', 'less', 'make', 'org', 'php', 'pl', 'properties', 'py', 'rb', 'scala', 'script', 'sh', 'sql', 'txt', 'text', 'tex', 'vi', 'vim', 'xhtml', 'xml', 'log', 'csv', 'groovy', 'rst', 'patch', 'go'),
    IMAGE: ('gif', 'jpeg', 'jpg', 'png', 'ico'),
    DOCUMENT: ('doc', 'docx', 'odt', 'fodt', 'ppt', 'pptx', 'odp', 'fodp', 'xls', 'xlsx', 'ods', 'fods'),
    SVG: ('svg',),
    PDF: ('pdf',),
    MARKDOWN: ('markdown', 'md'),
    SF: ('seaf',),
    VIDEO: ('mp4', 'ogv', 'webm', 'flv'),
    AUDIO: ('mp3',),
    '3D': ('stl', 'obj'),
}

def gen_fileext_type_map():
    """
    Generate previewed file extension and file type relation map.
    
    """
    d = {}
    for filetype in PREVIEW_FILEEXT.keys():
        for fileext in PREVIEW_FILEEXT.get(filetype):
            d[fileext] = filetype

    return d
FILEEXT_TYPE_MAP = gen_fileext_type_map()

def render_permission_error(request, msg=None, extra_ctx=None):
    """
    Return permisson error page.

    """
    ctx = {}
    ctx['error_msg'] = msg or _('permission error')

    if extra_ctx:
        for k in extra_ctx:
            ctx[k] = extra_ctx[k]

    return render_to_response('permission_error.html', ctx,
                              context_instance=RequestContext(request))

def render_error(request, msg=None, extra_ctx=None):
    """
    Return normal error page.

    """
    ctx = {}
    ctx['error_msg'] = msg or _('Internal error')

    if extra_ctx:
        for k in extra_ctx:
            ctx[k] = extra_ctx[k]

    return render_to_response('error.html', ctx,
                              context_instance=RequestContext(request))

def list_to_string(l):
    """
    Return string of a list.

    """
    return ','.join(l)

def get_httpserver_root():
    """ Construct wingufile httpserver address and port.

    Returns:
    	Constructed httpserver root.
    """

    from winguhub.settings import HTTP_SERVER_ROOT

    assert HTTP_SERVER_ROOT is not None, "SERVICE_URL is not set in ccnet.conf."

    return HTTP_SERVER_ROOT

def get_inner_httpserver_root():
    """Construct inner wingufile httpserver address and port.

    Inner httpserver root allows Seahub access httpserver through local
    address, thus avoiding the overhead of DNS queries, as well as other
    related issues, for example, the server can not ping itself, etc.

    Returns:
    	http://127.0.0.1:<port>
    """

    return winguhub.settings.INNER_HTTP_SERVER_ROOT

def get_ccnetapplet_root():
    """
    Get ccnet applet address and port from settings.py,
    and cut out last '/'.

    """
    return winguhub.settings.CCNET_APPLET_ROOT.strip('/')

def gen_token(max_length=5):
    """
    Generate a random token.

    """
    
    return uuid.uuid4().hex[:max_length]

def normalize_cache_key(value, prefix=None):
    """Returns a cache key consisten of ``value`` and ``prefix``. Cache key
    must not include control characters or whitespace.
    """
    key = value if prefix is None else prefix + value
    return urlquote(key)
    
def validate_group_name(group_name):
    """
    Check whether group name is valid.
    A valid group name only contains alphanumeric character.

    """
    return re.match('^\w+$', group_name, re.U)

def get_repo_last_modify(repo):
    """ Get last modification time for a repo.

    If head commit id of a repo is provided, we use that commit as last commit,
    otherwise falls back to getting last commit of a repo which is time
    consuming.
    """
    if repo.head_cmmt_id is not None:
        last_cmmt = seafserv_threaded_rpc.get_commit(repo.head_cmmt_id)
    else:
        logger.info('[repo %s] head_cmmt_id is missing.' % repo.id)
        last_cmmt = get_commits(repo.id, 0, 1)[0]
    return last_cmmt.ctime if last_cmmt else 0

def calculate_repos_last_modify(repo_list):
    """ Get last modification time for repos.
    """
    for repo in repo_list:
        repo.latest_modify = get_repo_last_modify(repo)

def normalize_dir_path(path):
    """Add '/' at the end of directory path if necessary.
    """
    if path[-1] != '/':
        path = path + '/'
    return path

def normalize_file_path(path):
    """Remove '/' at the end of file path if necessary.
    """
    return path.rstrip('/')

SIMPLE_EMAIL_RE = re.compile(r'^\S+@\S+\.\S+$')
def is_valid_email(email):
    """A simple e-mail format validation.
    """
    return True if SIMPLE_EMAIL_RE.match(email) is not None else False
    
def check_filename_with_rename(repo_id, parent_dir, filename):
    cmmts = get_commits(repo_id, 0, 1)
    latest_commit = cmmts[0] if cmmts else None
    if not latest_commit:
        return ''
    # TODO: what if parrent_dir does not exist?
    dirents = wingufile_api.list_dir_by_commit_and_path(latest_commit.id,
                                                      parent_dir.encode('utf-8'))

    def no_duplicate(name):
        for dirent in dirents:
            if dirent.obj_name == name:
                return False
        return True

    def make_new_name(filename, i):
        base, ext = os.path.splitext(filename)
        if ext:
            new_base = "%s (%d)" % (base, i)
            return new_base + ext
        else:
            return "%s (%d)" % (filename, i)

    if no_duplicate(filename):
        return filename
    else:
        i = 1
        while True:
            new_name = make_new_name (filename, i)
            if no_duplicate(new_name):
                return new_name
            else:
                i += 1

def get_user_repos(username):
    """
    Get all repos that user can access, including owns, shared, public, and
    repo in groups.
    NOTE: collumn names in shared_repo struct are not same as owned or group
    repos.
    """
    email = username

    shared_repos = wingufile_api.get_share_in_repo_list(email, -1, -1)
    owned_repos = list_personal_repos_by_owner(email)
    groups_repos = []
    for group in get_personal_groups_by_user(email):
        groups_repos += get_group_repos(group.id, email)
    
    if CLOUD_MODE:
        public_repos = []
    else:
        public_repos = list_inner_pub_repos(email)

    return (owned_repos, shared_repos, groups_repos, public_repos)

def get_file_type_and_ext(filename):
    """
    Return file type and extension if the file can be previewd online,
    otherwise, return unknown type.
    """
    fileExt = os.path.splitext(filename)[1][1:].lower()
    filetype = FILEEXT_TYPE_MAP.get(fileExt)
    if filetype:
        return (filetype, fileExt)
    else:
        return ('Unknown', fileExt)
    
    
def get_file_revision_id_size (commit_id, path):
    """Given a commit and a file path in that commit, return the wingufile id
    and size of the file blob

    """
    dirname  = os.path.dirname(path)
    filename = os.path.basename(path)
    seafdir = wingufile_api.list_dir_by_commit_and_path (commit_id, dirname)
    for dirent in seafdir:
        if dirent.obj_name == filename:
            file_size = seafserv_threaded_rpc.get_file_size(dirent.obj_id)
            return dirent.obj_id, file_size

    return None, None

def gen_inner_file_get_url(token, filename):
    """Generate inner httpserver file url.

    If ``ENABLE_INNER_HTTPSERVER`` set to False(defaults to True), will
    returns outer httpserver file url.

    Arguments:
    - `token`:
    - `filename`:

    Returns:
    	e.g., http://127.0.0.1:<port>/files/<token>/<filename>
    """
    if ENABLE_INNER_HTTPSERVER:
        return '%s/files/%s/%s' % (get_inner_httpserver_root(), token,
                                   urlquote(filename))
    else:
        return gen_file_get_url(token, filename)
    
def gen_block_get_url(token, blkid):
    """
    Generate httpserver block url.
    Format: http://<domain:port>/blks/<token>/<blkid>
    """
    if blkid:
        return '%s/blks/%s/%s' % (get_httpserver_root(), token, blkid)
    else:
        return '%s/blks/%s/' % (get_httpserver_root(), token)

def gen_file_get_url(token, filename):
    """
    Generate httpserver file url.
    Format: http://<domain:port>/files/<token>/<filename>
    """
    return '%s/files/%s/%s' % (get_httpserver_root(), token, urlquote(filename))

def gen_file_upload_url(token, op):
    return '%s/%s/%s' % (get_httpserver_root(), op, token)

def get_ccnet_server_addr_port():
    """get ccnet server host and port"""
    return CCNET_SERVER_ADDR, CCNET_SERVER_PORT

def string2list(string):
    """
    Split strings contacted with diffent separator to a list, and remove
    duplicated string.
    """
    tmp_str = string.replace(';', ',').replace('\n', ',').replace('\r', ',')
    # Remove empty and duplicate strings
    s = set()
    for e in tmp_str.split(','):
        e = e.strip(' ')
        if not e:
            continue
        s.add(e)
    return [ x for x in s ]
        
# def get_cur_ctx(request):
#     ctx_dict = request.session.get('current_context', {
#             'base_template': 'myhome_base.html',
#             'org_dict': None})
#     return ctx_dict

# def set_cur_ctx(request, ctx_dict):
#     request.session['current_context'] = ctx_dict
#     request.user.org = ctx_dict.get('org_dict', None)

def check_and_get_org_by_repo(repo_id, user):
    """
    Check whether repo is org repo, get org info if it is, and set
    base template.
    """
    org_id = get_org_id_by_repo_id(repo_id)
    if org_id > 0:
        # this repo is org repo, get org info
        org = get_org_by_id(org_id)
        org._dict['is_staff'] = is_org_staff(org_id, user)
        org._dict['email'] = user
        base_template = 'org_base.html'
    else:
        org = None
        base_template = 'myhome_base.html'
    
    return org, base_template

def check_and_get_org_by_group(group_id, user):
    """
    Check whether repo is org repo, get org info if it is, and set
    base template.
    """
    org_id = get_org_id_by_group(group_id)
    if org_id > 0:
        # this repo is org repo, get org info
        org = get_org_by_id(org_id)
        org._dict['is_staff'] = is_org_staff(org_id, user)
        org._dict['email'] = user
        base_template = 'org_base.html'
    else:
        org = None
        base_template = 'myhome_base.html'
    
    return org, base_template

def calc_file_last_modified(repo_id, file_path, file_path_hash, file_id):
    try:
        # get the lastest one file revision
        commits = seafserv_threaded_rpc.list_file_revisions(repo_id, file_path, 1, -1)
    except SearpcError, e:
        return '', 0

    if not commits:
        return '', 0

    email, last_modified = commits[0].creator_name, commits[0].ctime

    info = FileLastModifiedInfo(repo_id=repo_id,
                                file_path=file_path,
                                file_path_hash=file_path_hash,
                                file_id=file_id,
                                last_modified=last_modified,
                                email=email)
    try:
        info.save()
    except IntegrityError:
        pass

    return email, last_modified

def get_file_last_modified(repo_id, file_path):
    email = ''
    last_modified = 0
    file_path_hash = calc_file_path_hash(file_path)
    file_id = seafserv_threaded_rpc.get_file_id_by_path(repo_id, file_path)
    try:
        fc = FileLastModifiedInfo.objects.get(repo_id=repo_id,
                                            file_path_hash=file_path_hash)
    except FileLastModifiedInfo.DoesNotExist:
        # has no cache yet
        user, last_modified = calc_file_last_modified(repo_id, file_path, file_path_hash, file_id)
    else:
        # cache found
        if fc.file_id != file_id:
            # but cache is outdated
            fc.delete()
            user, last_modified = calc_file_last_modified(repo_id, file_path, file_path_hash, file_id)
        else:
            # cache is valid
            user, last_modified = fc.email, fc.last_modified

    return user, last_modified

def get_file_contributors_from_revisions(repo_id, file_path):
    """Inspect the file history and get a list of users who have modified the
    it.

    """
    commits = []
    try:
        commits = seafserv_threaded_rpc.list_file_revisions(repo_id, file_path, -1, -1)
    except SearpcError, e:
        return [], 0, ''

    if not commits:
        return [], 0, ''

    # Commits are already sorted by date, so the user list is also sorted.
    users = [ commit.creator_name for commit in commits if commit.creator_name ]

    # Remove duplicate elements in a list
    email_list = []
    for user in users:
        if user not in email_list:
            email_list.append(user)

    return email_list, commits[0].ctime, commits[0].id

def get_file_contributors(repo_id, file_path, file_path_hash, file_id):
    """Get file contributors list and last modified time from database cache.
    If not found in cache, try to get it from seaf-server.

    """
    contributors = []
    last_modified = 0
    last_commit_id = ''
    try:
        # HACK: Fixed the unique key bug in 1.1
        # Should be removed in future
        fc = FileContributors.objects.filter(repo_id=repo_id,
                                file_path_hash=file_path_hash)
        if not fc:
            raise FileContributors.DoesNotExist
        else:
            if len(fc) > 1:
                for e in fc[1:]:
                    e.delete()
            fc = fc[0]
    except FileContributors.DoesNotExist:
        # has no cache yet
        contributors, last_modified, last_commit_id = get_file_contributors_from_revisions (repo_id, file_path)
        if not contributors:
            return [], 0, ''
        emails = ','.join(contributors)
        file_contributors = FileContributors(repo_id=repo_id,
                                             file_id=file_id,
                                             file_path=file_path,
                                             file_path_hash=file_path_hash,
                                             last_modified=last_modified,
                                             last_commit_id=last_commit_id,
                                             emails=emails)
        file_contributors.save()
    else:
        # cache found
        if fc.file_id != file_id or not fc.last_commit_id:
            # but cache is outdated
            fc.delete()
            contributors, last_modified, last_commit_id = get_file_contributors_from_revisions (repo_id, file_path)
            if not contributors:
                return [], 0, ''
            emails = ','.join(contributors)
            file_contributors = FileContributors(repo_id=repo_id,
                                                 file_id=file_id,
                                                 file_path=file_path,
                                                 file_path_hash=file_path_hash,
                                                 last_modified=last_modified,
                                                 last_commit_id=last_commit_id,
                                                 emails=emails)
            file_contributors.save()
        else:
            # cache is valid
            if fc.emails:
                contributors = fc.emails.split(',')
            else:
                contributors = []
            last_modified = fc.last_modified
            last_commit_id = fc.last_commit_id

    return contributors, last_modified, last_commit_id 

# events related    
if EVENTS_CONFIG_FILE:
    import seafevents

    EVENTS_ENABLED = True
    SeafEventsSession = seafevents.init_db_session_class(EVENTS_CONFIG_FILE)

    def _same_events(e1, e2):
        """Two events are equal should follow two rules:
        1. event1.username = event2.username
        2. event1.commit.desc = event2.commit.desc
        """
        if e1.username != e2.username:
            return False
        else:
            if hasattr(e1, 'commit') and hasattr(e2, 'commit'):
                if e1.commit.desc == e2.commit.desc:
                    return True
            return False

    def _get_events(username, start, count, org_id=None):
        ev_session = SeafEventsSession()

        valid_events = []
        total_used = 0
        try:
            next_start = start
            while True:
                events = _get_events_inner(ev_session, username, next_start, count)
                if not events:
                    break
                
                for e1 in events:
                    duplicate = False
                    for e2 in valid_events:
                        if _same_events(e1, e2): duplicate = True; break
                    if not duplicate:
                        valid_events.append(e1)
                    total_used = total_used + 1
                    if len(valid_events) == count:
                        break
                
                if len(valid_events) == count:
                    break
                next_start = next_start + len(events)
        finally:
            ev_session.close()

        for e in valid_events:            # parse commit description
            if hasattr(e, 'commit'):
                e.commit.converted_cmmt_desc = convert_cmmt_desc_link(e.commit)
                e.commit.more_files = more_files_in_commit(e.commit)
        return valid_events, start + total_used

    def _get_events_inner(ev_session, username, start, limit):
        '''Read events from seafevents database, and remove events that are
        no longer valid

        Return 'limit' events or less than 'limit' events if no more events remain
        '''
        valid_events = []
        next_start = start
        while True:
            events = seafevents.get_user_events(ev_session, username,
                                                next_start, limit)
            if not events:
                break

            for ev in events:
                if ev.etype == 'repo-update':
                    repo = get_repo(ev.repo_id)
                    if not repo:
                        # delete the update event for repo which has been deleted
                        seafevents.delete_event(ev_session, ev.uuid)
                        continue
                    if repo.encrypted:
                        repo.password_set = seafserv_rpc.is_passwd_set(repo.id, username)
                    ev.repo = repo
                    ev.commit = seafserv_threaded_rpc.get_commit(ev.commit_id)

                valid_events.append(ev)
                if len(valid_events) == limit:
                    break

            if len(valid_events) == limit:
                break            
            next_start = next_start + len(valid_events)

        return valid_events

    def get_user_events(username, start, count):
        """Return user events list and a new start.
        
        For example:
        ``get_user_events('foo@example.com', 0, 10)`` returns the first 10
        events.
        ``get_user_events('foo@example.com', 5, 10)`` returns the 6th through
        15th events.
        """
        return _get_events(username, start, count)
        
    def get_org_user_events(org_id, username, start, count):
        return _get_events(username, start, count, org_id=org_id)

else:
    EVENTS_ENABLED = False
    def get_user_events():
        pass
    def get_org_user_events():
        pass

class StarredFile(object):
    def format_path(self):
        if self.path == "/":
            return self.path

        # strip leading slash
        path = self.path[1:]
        if path[-1:] == '/':
            path = path[:-1]
        return path.replace('/', ' / ')

    def __init__(self, org_id, repo, path, is_dir, last_modified, size):
        # always 0 for non-org repo
        self.org_id = org_id
        self.repo = repo
        self.path = path
        self.formatted_path = self.format_path()
        self.is_dir = is_dir
        # always 0 for dir
        self.last_modified = last_modified
        self.size = size
        if not is_dir:
            self.name = path.split('/')[-1]


# org_id > 0: get starred files in org repos
# org_id < 0: get starred files in personal repos
def get_starred_files(email, org_id=-1):
    """Return a list of starred files for some user, sorted descending by the
    last modified time.

    """
    starred_files = UserStarredFiles.objects.filter(email=email, org_id=org_id)

    ret = []
    for sfile in starred_files:
        # repo still exists?
        try:
            repo = get_repo(sfile.repo_id)
        except SearpcError:
            continue

        if not repo:
            sfile.delete()
            continue

        # file still exists?
        file_id = ''
        size = -1
        if sfile.path != "/":
            try:
                file_id = seafserv_threaded_rpc.get_file_id_by_path(sfile.repo_id, sfile.path)
                size = seafserv_threaded_rpc.get_file_size(file_id)
            except SearpcError:
                continue

            if not file_id:
                sfile.delete()
                continue

        last_modified = 0
        if not sfile.is_dir:
            # last modified
            path_hash = md5_constructor(urllib2.quote(sfile.path.encode('utf-8'))).hexdigest()[:12]
            last_modified = get_file_contributors(sfile.repo_id, sfile.path, path_hash, file_id)[1]

        f = StarredFile(sfile.org_id, repo, sfile.path, sfile.is_dir, last_modified, size)

        ret.append(f)
    ret.sort(lambda x, y: cmp(y.last_modified, x.last_modified))

    return ret

def star_file(email, repo_id, path, is_dir, org_id=-1):
    if is_file_starred(email, repo_id, path, org_id):
        return

    f = UserStarredFiles(email=email,
                         org_id=org_id,
                         repo_id=repo_id,
                         path=path,
                         is_dir=is_dir)
    try:
        f.save()
    except IntegrityError, e:
        logger.warn(e)

def unstar_file(email, repo_id, path):
    # Should use "get", but here we use "filter" to fix the bug caused by no
    # unique constraint in the table
    result = UserStarredFiles.objects.filter(email=email,
                                             repo_id=repo_id,
                                             path=path)
    for r in result:
        r.delete()
            
def is_file_starred(email, repo_id, path, org_id=-1):
    # Should use "get", but here we use "filter" to fix the bug caused by no
    # unique constraint in the table
    result = UserStarredFiles.objects.filter(email=email,
                                             repo_id=repo_id,
                                             path=path,
                                             org_id=org_id)
    n = len(result)
    if n == 0:
        return False
    else:
        # Fix the bug caused by no unique constraint in the table
        if n > 1:
            for r in result[1:]:
                r.delete()
        return True

def get_dir_starred_files(email, repo_id, parent_dir, org_id=-1): 
    '''Get starred files under parent_dir.

    '''
    starred_files = UserStarredFiles.objects.filter(email=email,
                                         repo_id=repo_id,
                                         path__startswith=parent_dir,
                                         org_id=org_id)
    return [ f.path for f in starred_files ]

def calc_dir_files_last_modified(repo_id, parent_dir, parent_dir_hash, dir_id):
    try:
        ret_list = seafserv_threaded_rpc.calc_files_last_modified(repo_id, parent_dir.encode('utf-8'), 0)
    except:
        return {}

    # ret_list is like:
    # [
    #    {'file_name': 'xxx', 'last_modified': t1}
    #    {'file_name': 'yyy', 'last_modified': t2}
    # ]
    # and we transform it to:
    # {'xxx': t1, 'yyy': t2}
    last_modified_info = {}
    for entry in ret_list:
        key = entry.file_name
        value = entry.last_modified
        last_modified_info[key] = value
        
    info = DirFilesLastModifiedInfo(repo_id=repo_id,
                                    parent_dir=parent_dir,
                                    parent_dir_hash=parent_dir_hash,
                                    dir_id=dir_id,
                                    last_modified_info=json.dumps(last_modified_info))

    try:
        info.save()
    except IntegrityError, e:
        # If this record is already saved, skip this step.
        logger.warn(e)
        
    return last_modified_info

def get_dir_files_last_modified(repo_id, parent_dir, dir_id=None):
    '''Calc the last modified time of all the files under the directory
    <parent_dir> of the repo <repo_id>. Return a dict whose keys are the file
    names and values are their corresponding last modified timestamps.

    '''
    if not dir_id:
        dir_id = seafserv_threaded_rpc.get_dir_id_by_path(repo_id, parent_dir)
    parent_dir_hash = calc_file_path_hash(parent_dir)
    if not dir_id:
        return {}
        
    try:
        info = DirFilesLastModifiedInfo.objects.get(repo_id=repo_id,
                                                    parent_dir_hash=parent_dir_hash)
    except DirFilesLastModifiedInfo.DoesNotExist:
        # no cache yet
        return calc_dir_files_last_modified(repo_id, parent_dir, parent_dir_hash, dir_id)
    else:
        # cache exist
        if info.dir_id != dir_id:
            # cache is outdated
            info.delete()
            return calc_dir_files_last_modified(repo_id, parent_dir, parent_dir_hash, dir_id)
        else:
            # cache is valid
            return json.loads(info.last_modified_info)

def calc_file_path_hash(path, bits=12):
    if isinstance(path, unicode):
        path = path.encode('UTF-8')

    path_hash = md5_constructor(urllib2.quote(path)).hexdigest()[:bits]
    
    return path_hash

def get_service_url():
    """Get service url from seaserv.
    """
    return SERVICE_URL

def gen_dir_share_link(token):
    """Generate directory share link.
    """
    return gen_shared_link(token, 'd')

def gen_file_share_link(token):
    """Generate file share link.
    """
    return gen_shared_link(token, 'f')

def gen_shared_link(token, s_type):
    service_url = get_service_url()
    assert service_url is not None

    service_url = service_url.rstrip('/')
    if s_type == 'f':
        return '%s/f/%s/' % (service_url, token)
    else:
        return '%s/d/%s/' % (service_url, token)

def show_delete_days(request):
    if request.method == 'GET':
        days_str = request.GET.get('days', '')
    elif request.method == 'POST':
        days_str = request.POST.get('days', '')
    else:
        days_str = ''

    try:
        days = int(days_str)
    except ValueError:
        days = 7

    return days

def is_textual_file(file_type):
    """
    Check whether a file type is a textual file.
    """
    if file_type == TEXT or file_type == MARKDOWN or file_type == SF:
        return True
    else:
        return False

TRAFFIC_STATS_ENABLED = False
if hasattr(winguhub.settings, 'TRAFFIC_STATS_CONFIG_FILE'):
    import seafstats

    TRAFFIC_STATS_ENABLED = True
    SeafStatsSession = seafstats.init_db_session_class(winguhub.settings.TRAFFIC_STATS_CONFIG_FILE)
    def get_user_traffic_stat(username):
        session = SeafStatsSession()
        try:
            stat = seafstats.get_user_traffic_stat(session, username)
        finally:
            session.close()
        return stat
else:
    def get_user_traffic_stat(username):
        pass

def redirect_to_login(request):
    from django.conf import settings
    login_url = settings.LOGIN_URL
    path = urlquote(request.get_full_path())
    tup = login_url, redirect_field_name, path
    return HttpResponseRedirect('%s?%s=%s' % tup)

def mkstemp():
    '''Returns (fd, filepath), the same as tempfile.mkstemp, except the
    filepath is encoded in UTF-8

    '''
    fd, path = tempfile.mkstemp()
    system_encoding = locale.getdefaultlocale()[1]
    if system_encoding is not None:
        path_utf8 = path.decode(system_encoding).encode('UTF-8')
        return fd, path_utf8
    else:
        return fd, path

# File or directory operations
FILE_OP = ('Added', 'Modified', 'Renamed', 'Moved',
           'Added directory', 'Renamed directory', 'Moved directory')

OPS = '|'.join(FILE_OP)
CMMT_DESC_PATT = re.compile(r'(%s) "(.*)"\s?(and \d+ more (?:files|directories))?' % OPS)

API_OPS = '|'.join((OPS, 'Deleted', 'Removed'))
API_CMMT_DESC_PATT = r'(%s) "(.*)"\s?(and \d+ more (?:files|directories))?' % API_OPS


def convert_cmmt_desc_link(commit):
    """Wrap file/folder with ``<a></a>`` in commit description.
    """
    repo_id = commit.repo_id
    cmmt_id = commit.id
    conv_link_url = reverse('convert_cmmt_desc_link')    

    def link_repl(matchobj):
        op = matchobj.group(1)
        file_or_dir = matchobj.group(2)
        remaining = matchobj.group(3)

        tmp_str = '%s "<a href="%s?repo_id=%s&cmmt_id=%s&nm=%s">%s</a>"'
        if remaining:
            return (tmp_str + ' %s') % (op, conv_link_url, repo_id, cmmt_id, urlquote(file_or_dir),
                                        file_or_dir, remaining)
        else:
            return tmp_str % (op, conv_link_url, repo_id, cmmt_id, urlquote(file_or_dir), file_or_dir)

    return re.sub(CMMT_DESC_PATT, link_repl, commit.desc)

def api_tsstr_sec(value):
    """Turn a timestamp to string"""
    try:
        return datetime.fromtimestamp(value).strftime("%Y-%m-%d %H:%M:%S")
    except:
        return datetime.fromtimestamp(value/1000000).strftime("%Y-%m-%d %H:%M:%S")


def api_convert_desc_link(e):
    """Wrap file/folder with ``<a></a>`` in commit description.
    """
    commit = e.commit
    repo_id = commit.repo_id
    cmmt_id = commit.id

    def link_repl(matchobj):
        op = matchobj.group(1)
        file_or_dir = matchobj.group(2)
        remaining = matchobj.group(3)

        tmp_str = '%s "<span class="file-name">%s</span>"'
        if remaining:
            url = reverse('api_repo_history_changes', args=[repo_id])
            e.link = "%s?commit_id=%s" % (url, cmmt_id)
            e.dtime = api_tsstr_sec(commit.props.ctime)
            return (tmp_str + ' %s') % (op, file_or_dir, remaining)
        else:
            diff_result = seafserv_threaded_rpc.get_diff(repo_id, '', cmmt_id)
            if diff_result:
                for d in diff_result:
                    if file_or_dir not in d.name:
                        # skip to next diff_result if file/folder user clicked does not
                        # match the diff_result
                        continue            

                    if d.status == 'add' or d.status == 'mod':
                        e.link = "api://repo/%s/files/?p=/%s" % (repo_id, d.name)
                    elif d.status == 'mov':
                        e.link = "api://repo/%s/files/?p=/%s" % (repo_id, d.new_name)
                    elif d.status == 'newdir':
                        e.link = "api://repo/%s/dir/?p=/%s" % (repo_id, d.name)
                    else:
                        continue
            return tmp_str % (op, file_or_dir)
    e.desc = re.sub(API_CMMT_DESC_PATT, link_repl, commit.desc)

MORE_PATT = r'and \d+ more (?:files|directories)'
def more_files_in_commit(commit):
    """Check whether added/deleted/modified more files in commit description.
    """
    return True if re.search(MORE_PATT, commit.desc) else False

# office convert related
HAS_OFFICE_CONVERTER = False
if EVENTS_CONFIG_FILE:
    def check_office_converter_enabled():
        config = ConfigParser.ConfigParser()
        config.read(EVENTS_CONFIG_FILE)
        enabled = seafevents.is_office_converter_enabled(config)
        if enabled:
            logging.debug('office converter: enabled')
        else:
            logging.debug('office converter: not enabled')
        return enabled

    def get_office_converter_html_dir():
        config = ConfigParser.ConfigParser()
        config.read(EVENTS_CONFIG_FILE)
        return seafevents.get_office_converter_html_dir(config)

    def get_office_converter_limit():
        config = ConfigParser.ConfigParser()
        config.read(EVENTS_CONFIG_FILE)
        return seafevents.get_office_converter_limit(config)

    HAS_OFFICE_CONVERTER = check_office_converter_enabled()
    
if HAS_OFFICE_CONVERTER:

    OFFICE_HTML_DIR = get_office_converter_html_dir()
    OFFICE_PREVIEW_MAX_SIZE, OFFICE_PREVIEW_MAX_PAGES = get_office_converter_limit()

    from seafevents.office_converter import OfficeConverterRpcClient

    office_converter_rpc = None
    def _get_office_converter_rpc():
        global office_converter_rpc
        if office_converter_rpc is None:
            pool = ccnet.ClientPool(CCNET_CONF_PATH)
            office_converter_rpc = OfficeConverterRpcClient(pool)

        return office_converter_rpc

    def add_office_convert_task(file_id, doctype, url):
        rpc = _get_office_converter_rpc()
        return rpc.add_task(file_id, doctype, url)

    def query_office_convert_status(file_id):
        rpc = _get_office_converter_rpc()
        return rpc.query_convert_status(file_id)

    def query_office_file_pages(file_id):
        rpc = _get_office_converter_rpc()
        return rpc.query_file_pages(file_id)

    def get_converted_html_detail(file_id):    
        d = {}
        outline_file = os.path.join(OFFICE_HTML_DIR, file_id, 'file.outline')

        with open(outline_file, 'r') as fp:
            outline = fp.read()

        page_num = query_office_file_pages(file_id).count

        d['outline'] = outline
        d['page_num'] = page_num

        return d

    def prepare_converted_html(raw_path, obj_id, doctype, ret_dict):
        try:
            ret = add_office_convert_task(obj_id, doctype, raw_path)
        except:
            logging.exception('failed to add_office_convert_task:')
            return _(u'Internal error'), False
        else:
            if ret.exists:
                try:
                    ret_dict['html_detail'] = get_converted_html_detail(obj_id)
                except:
                    pass
            return None, ret.exists

# search realted
HAS_FILE_SEARCH = False
if EVENTS_CONFIG_FILE:
    def check_search_enabled():
        config = ConfigParser.ConfigParser()
        config.read(EVENTS_CONFIG_FILE)
        enabled = seafevents.is_search_enabled(config)
        if enabled:
            logging.debug('search: enabled')
        else:
            logging.debug('search: not enabled')
        return enabled

    HAS_FILE_SEARCH = check_search_enabled()

# Move to here to avoid circular import.
from winguhub.base.models import FileContributors, UserStarredFiles, DirFilesLastModifiedInfo, FileLastModifiedInfo
