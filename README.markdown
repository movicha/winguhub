Introduction
==========

Seahub is the web frontend for Seafle.

Preparation
==========

* Build and deploy Seafile server from source. See <https://github.com/haiwen/wingufile/wiki/Build-and-deploy-wingufile-server-from-source>

* Django 1.3, download from <https://www.djangoproject.com/download/1.3.5/tarball/>

* Djblets

    easy_install --upgrade Djblets

Getting it
==========

You can grab souce code from GitHub.

    $ git clone git://github.com/haiwen/winguhub.git

Configuration
==========

Modify `CCNET_CONF_DIR`, `WINGUFILE_CONF_DIR` and `PYTHONPATH` in `setenv.sh.template` to fit your path.

`CCNET_CONF_DIR` is the directory contains `ccnet.conf`.

`WINGUFILE_CONF_DIR` is the directory contains `wingufile.conf`.

Run and Verify
==========

Run as:

    ./run-winguhub.sh.template

Then open your browser, and input `http://localhost:8000/`, there should be a Login page. You can create admin account using `winguhub-admin.py` script under `tools/` directory.

Internationalization (I18n)
==========

You are welcome to add translation in your language. For example, if you like to add Russian translation.

First, you need to add your language code to Settings.py. In this case, add `('ru', gettext_noop(u'Русский')),` to LANGUAGES tuple.

Then, run this command:

    django-admin.py makemessages -l ru -e py,html

There will be a file named `django.po` under `locale/ru/LC_MESSAGES`.

**NOTE:** If you install your Django source under thirdpart, you need to ignore all files under Django directory, otherwise the po file will become large.

    django-admin.py makemessages -l ru -e py,html -i "thirdpart/Django-1.3-py2.7-egg/*"

After you modified `django.po`, you can run `./i18n.sh compile-all`, this will create `.mo` file under same directory with `django.po`.

That's it. After restart Seahub, you can select popup button at right top, and your translations are ready for use.

