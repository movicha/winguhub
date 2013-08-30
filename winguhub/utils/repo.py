# -*- coding: utf-8 -*-
from seaserv import wingufile_api
from winguhub.utils import EMPTY_SHA1

def list_dir_by_path(cmmt, path):
    if cmmt.root_id == EMPTY_SHA1:
        return []
    else:
        return wingufile_api.list_dir_by_commit_and_path(cmmt.id, path)
    
