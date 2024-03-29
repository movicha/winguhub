# -*- coding: utf-8 -*-
import os
import stat
import urllib2

from django.core.urlresolvers import reverse
from django.utils.http import urlquote
from django.utils.encoding import smart_str

import seaserv
from seaserv import wingufile_api
from pysearpc import SearpcError
from winguhub.utils import EMPTY_SHA1
from winguhub.utils.slugify import slugify
from winguhub.utils import render_error, render_permission_error, string2list, \
    gen_file_get_url, get_file_type_and_ext, get_file_contributors, \
    gen_inner_file_get_url
from winguhub.utils.file_types import IMAGE
from models import WikiPageMissing, WikiDoesNotExist, GroupWiki, PersonalWiki


__all__ = ["get_wiki_dirent", "clean_page_name"]



SLUG_OK = "!@#$%^&()_+-,.;'"
def normalize_page_name(page_name):
    # Remove special characters. Lower page name and replace spaces with '-'.
    return slugify(page_name, ok=SLUG_OK)

def clean_page_name(page_name):
    # Remove special characters. Do not lower page name and spaces are allowed.
    return slugify(page_name, ok=SLUG_OK, lower=False, spaces=True)

def get_wiki_dirent(repo_id, page_name):
    file_name = page_name + ".md"
    repo = seaserv.get_repo(repo_id)
    if not repo:
        raise WikiDoesNotExist
    cmmt = seaserv.get_commits(repo.id, 0, 1)[0]
    if cmmt is None:
        raise WikiPageMissing
    dirs = wingufile_api.list_dir_by_commit_and_path(cmmt.id, "/")
    if dirs:
        for e in dirs:
            if stat.S_ISDIR(e.mode):
                continue    # skip directories
            if normalize_page_name(file_name) == normalize_page_name(e.obj_name):
                return e
    raise WikiPageMissing

def get_file_url(repo, obj_id, file_name):
    repo_id = repo.id
    access_token = seaserv.seafserv_rpc.web_get_access_token(repo_id, obj_id,
                                                     'view', '')
    url = gen_file_get_url(access_token, file_name)
    return url

def get_inner_file_url(repo, obj_id, file_name):
    repo_id = repo.id
    access_token = seaserv.seafserv_rpc.web_get_access_token(repo_id, obj_id,
                                                     'view', '')
    url = gen_inner_file_get_url(access_token, file_name)
    return url

def get_personal_wiki_repo(username):
    try:
        wiki = PersonalWiki.objects.get(username=username)
    except PersonalWiki.DoesNotExist:
        raise WikiDoesNotExist
    repo = seaserv.get_repo(wiki.repo_id)
    if not repo:
        raise WikiDoesNotExist
    return repo

def get_group_wiki_repo(group, username):
    try:
        groupwiki = GroupWiki.objects.get(group_id=group.id)
    except GroupWiki.DoesNotExist:
        raise WikiDoesNotExist
        
    repos = seaserv.get_group_repos(group.id, username)
    for repo in repos:
        if repo.id == groupwiki.repo_id:
            return repo
    raise WikiDoesNotExist
    
def get_personal_wiki_page(username, page_name):
    repo = get_personal_wiki_repo(username)
    dirent = get_wiki_dirent(repo.id, page_name)
    url = get_inner_file_url(repo, dirent.obj_id, dirent.obj_name)
    file_response = urllib2.urlopen(url)
    content = file_response.read()
    return content, repo, dirent

def get_group_wiki_page(username, group, page_name):
    repo = get_group_wiki_repo(group, username)
    dirent = get_wiki_dirent(repo.id, page_name)
    url = get_inner_file_url(repo, dirent.obj_id, dirent.obj_name)
    file_response = urllib2.urlopen(url)
    content = file_response.read()
    return content, repo, dirent

def get_wiki_pages(repo):
    """
    return pages in hashtable {normalized_name: page_name}
    """
    dir_id = seaserv.seafserv_threaded_rpc.get_dir_id_by_path(repo.id, '/')
    dirs = wingufile_api.list_dir_by_dir_id(dir_id)
    pages = {}
    for e in dirs:
        if stat.S_ISDIR(e.mode):
            continue            # skip directories
        name, ext = os.path.splitext(e.obj_name)
        if ext == '.md':
            key = normalize_page_name(name)
            pages[key] = name
    return pages

def convert_wiki_link(content, url_prefix, repo_id, username):
    import re

    def repl(matchobj):
        if matchobj.group(2):   # return origin string in backquotes
            return matchobj.group(2)

        page_alias = page_name = matchobj.group(1).strip()
        if len(page_name.split('|')) > 1:
            page_alias = page_name.split('|')[0]
            page_name = page_name.split('|')[1]
        
        filetype, fileext = get_file_type_and_ext(page_name)
        if fileext == '':
            # convert page_name that extension is missing to a markdown page
            try:
                dirent = get_wiki_dirent(repo_id, page_name)
                a_tag = "<a href='%s'>%s</a>"
                return a_tag % (smart_str(url_prefix + normalize_page_name(page_name) + '/'), page_alias)
            except (WikiDoesNotExist, WikiPageMissing):
                a_tag = '''<a class="wiki-page-missing" href='%s'>%s</a>'''
                return a_tag % (smart_str(url_prefix + page_name.replace('/', '-') + '/'), page_alias)
        elif filetype == IMAGE:
            # load image to wiki page
            path = "/" + page_name
            filename = os.path.basename(path)
            obj_id = seaserv.get_file_id_by_path(repo_id, path)
            if not obj_id:
                # Replace '/' in page_name to '-', since wiki name can not
                # contain '/'.
                return '''<a class="wiki-page-missing" href='%s'>%s</a>''' % \
                    (url_prefix + '/' + page_name.replace('/', '-'), page_name)

            token = seaserv.web_get_access_token(repo_id, obj_id, 'view', username)
            ret = '<img class="wiki-image" src="%s" alt="%s" />' % (gen_file_get_url(token, filename), filename)
            return smart_str(ret)
        else:
            from winguhub.base.templatetags.winguhub_tags import file_icon_filter
            from django.conf import settings
            
            # convert other types of filelinks to clickable links
            path = "/" + page_name
            icon = file_icon_filter(page_name)
            s = reverse('repo_view_file', args=[repo_id]) + \
                '?p=' + urlquote(path)
            a_tag = '''<img src="%simg/file/%s" alt="%s" class="vam" /> <a href='%s' target='_blank' class="vam">%s</a>'''
            ret = a_tag % (settings.MEDIA_URL, icon, icon, smart_str(s), page_name)
            return smart_str(ret)

    return re.sub(r'\[\[(.+?)\]\]|(`.+?`)', repl, content)


