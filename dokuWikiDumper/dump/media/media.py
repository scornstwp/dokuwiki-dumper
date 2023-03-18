import os
import re
import threading
import time
import urllib.parse as urlparse

from bs4 import BeautifulSoup
import requests

from dokuWikiDumper.utils.util import smkdirs, uopen
from dokuWikiDumper.utils.util import print_with_lock as print


def getFiles(url, ns: str = '',  dumpDir: str = '', session=None):
    """ Return a list of media filenames of a wiki """

    if dumpDir and os.path.exists(dumpDir + '/dumpMeta/files.txt'):
        with uopen(dumpDir + '/dumpMeta/files.txt', 'r') as f:
            files = f.read().splitlines()
            if files[-1] == '--END--':
                print('Loaded %d files from %s' %
                      (len(files) - 1, dumpDir + '/dumpMeta/files.txt'))
                return files[:-1]  # remove '--END--'

    files = set()
    ajax = urlparse.urljoin(url, 'lib/exe/ajax.php')
    medialist = BeautifulSoup(
        session.post(ajax, {
            'call': 'medialist',
            'ns': ns,
            'do': 'media'
        }).text, 'lxml')
    medians = BeautifulSoup(
        session.post(ajax, {
            'call': 'medians',
            'ns': ns,
            'do': 'media'
        }).text, 'lxml')
    imagelinks = medialist.findAll(
        'a',
        href=lambda x: x and re.findall(
            '[?&](media|image)=',
            x))
    for a in imagelinks:
        query = urlparse.parse_qs(urlparse.urlparse(a['href']).query)
        key = 'media' if 'media' in query else 'image'
        files.add(query[key][0])
    files = list(files)
    namespacelinks = medians.findAll('a', {'class': 'idx_dir', 'href': True})
    for a in namespacelinks:
        query = urlparse.parse_qs(urlparse.urlparse(a['href']).query)
        files += getFiles(url, query['ns'][0], session=session)
    print('Found %d files in namespace %s' % (len(files), ns or '(all)'))

    if dumpDir:
        smkdirs(dumpDir + '/dumpMeta')
        with uopen(dumpDir + '/dumpMeta/files.txt', 'w') as f:
            f.write('\n'.join(files))
            f.write('\n--END--\n')

    return files


def dumpMedia(url: str = '', dumpDir: str = '', session=None, threads: int = 1):
    if not dumpDir:
        raise ValueError('dumpDir must be set')

    smkdirs(dumpDir + '/media')
    # smkdirs(dumpDir + '/media_attic')
    # smkdirs(dumpDir + '/media_meta')

    fetch = urlparse.urljoin(url, 'lib/exe/fetch.php')

    files = getFiles(url, dumpDir=dumpDir, session=session)
    for title in files:
        while threading.active_count() > threads:
            time.sleep(0.1)

        def download(title, session: requests.Session):
            child_path = title.replace(':', '/')
            child_path = child_path.lstrip('/')
            child_path = '/'.join(child_path.split('/')[:-1])
            smkdirs(dumpDir + '/media/' + child_path)
            file = dumpDir + '/media/' + title.replace(':', '/')
            local_size = 0
            if os.path.exists(file):
                local_size = os.path.getsize(file)
            with session.get(fetch, params={'media': title}, stream=True) as r:
                r.raise_for_status()

                remote_size = int(r.headers['Content-Length'])
                if local_size == remote_size:
                    print(threading.current_thread().name, 'File [[%s]] exists (%d bytes)' % (title, local_size))
                else:
                    r.raw.decode_content = True
                    with open(file, 'wb') as f:
                        for chunk in r.iter_content(chunk_size=8192):
                            f.write(chunk)
                        print(threading.current_thread().name, 'File [[%s]] Done' % title)
                # modify mtime based on Last-Modified header
                last_modified = r.headers.get('Last-Modified', None)
                if last_modified:
                    mtime = time.mktime(time.strptime(last_modified, '%a, %d %b %Y %H:%M:%S %Z'))
                    atime = os.stat(file).st_atime
                    os.utime(file, times=(atime, mtime)) # atime is not modified
                    # print(atime, mtime)
                
            # time.sleep(1.5)

        t = threading.Thread(target=download, daemon=True,
                             args=(title, session))
        t.start()

    while threading.active_count() > 1:
        time.sleep(2)
        print('Waiting for %d threads to finish' %
              (threading.active_count() - 1), end='\r')
