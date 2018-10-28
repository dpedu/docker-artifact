from urllib.parse import urlparse, urlunsplit, urlencode
from time import sleep
from threading import Thread
from repobot.provider import providers
import logging
import os
from requests import post


log = logging.getLogger("replication")


class RepoReplicator(object):
    def __init__(self, db, data_root, neighbors):
        """
        :param neighbors: list of replication neighbor uris like 'http://1.2.3.4:8080'
        """
        self.db = db
        self.data_root = data_root
        self.neighbors = [urlparse(i) for i in neighbors]
        self.worker = None

    def start(self):
        if not self.neighbors:
            return
        self.worker = ReplicationWorker(self)
        self.worker.start()


class ReplicationWorker(Thread):
    def __init__(self, master):
        super().__init__()
        self.daemon = True
        self.master = master

    def run(self):
        while True:
            with self.master.db.db.transaction() as c:
                # for item in c.root.sendqueue:
                log.info("items in queue: %s", len(c.root.sendqueue))
                if len(c.root.sendqueue) > 0:
                    item = c.root.sendqueue[0]
                    if self.replicate(item):
                        c.root.sendqueue.pop(0)
                        log.info("Replication successful")
            sleep(5)

    def replicate(self, item):
        item_type, item = item
        if item_type == "package":
            return self.replicate_package(item)

    def replicate_package(self, item):
        repo, pkg, fname, params = item
        datadir = os.path.join(self.master.data_root, repo.provider, repo.name)
        provider = providers[repo.provider](self.master.db, repo, datadir)
        fpath = provider.get_path(pkg, fname)

        for neighbor in self.master.neighbors:
            q_params = {"provider": repo.provider,
                        "reponame": repo.name,
                        "name": pkg.name,
                        "version": pkg.version}
            q_params.update(**params)
            url = urlunsplit(["http", neighbor.netloc, "/addpkg", urlencode(q_params), None])
            with open(fpath, 'rb') as fitem:
                try:
                    r = post(url, files={'f': (fname, fitem)}, timeout=(10, 30))
                    if r.status_code not in (200, 409):
                        r.raise_for_status()
                except Exception as e:
                    log.warning("Failed replication of %s to %s: %s", pkg, neighbor.geturl(), str(e))
                    return False
            log.info("Replicated %s to %s", pkg, neighbor.geturl())

        return True
