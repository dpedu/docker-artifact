import ZODB
import ZODB.FileStorage
import persistent
import BTrees.OOBTree
from repobot.provider import providers
import os
from repobot.common import plist, pmap


class Repo(persistent.Persistent):
    def __init__(self, name, provider):
        self.name = name
        self.provider = provider
        self.packages = pmap()
        self.data = pmap()

    def get_package(self, name, version):
        if name not in self.packages:
            self.packages[name] = pmap()
        if version not in self.packages[name]:
            self.packages[name][version] = RepoPackage(name, version)
        return self.packages[name][version]


class RepoPackage(persistent.Persistent):
    def __init__(self, name, version):
        self.name = name
        self.version = version
        self.data = pmap()


class RepoDb(object):
    def __init__(self, db_path, data_root):
        self.storage = ZODB.FileStorage.FileStorage(db_path)
        self.db = ZODB.DB(self.storage)
        self.data_root = data_root

        with self.db.transaction() as c:
            if "repos" not in c.root():
                c.root.repos = BTrees.OOBTree.BTree()

    def add_package(self, provider, reponame, pkgname, pkgversion, fname, fobj, params):
        with self.db.transaction() as c:
            repo = self._get_repo(c, provider, reponame)
            datadir = os.path.join(self.data_root, provider, reponame)
            provider = providers[repo.provider](self.db, repo, datadir)
            provider.add_package(repo.get_package(pkgname, pkgversion), fname, fobj, params)

    def _get_repo(self, c, provider, name):
        if provider not in c.root.repos:
            c.root.repos[provider] = pmap()
        if name not in c.root.repos[provider]:
            c.root.repos[provider][name] = Repo(name, provider)
        return c.root.repos[provider][name]

    def browse_repo(self, provider, reponame, args):
        with self.db.transaction() as c:
            repo = c.root.repos[provider][reponame]
            datadir = os.path.join(self.data_root, provider, reponame)
            provider = providers[repo.provider](self.db, repo, datadir)
            return provider.browse(args)
