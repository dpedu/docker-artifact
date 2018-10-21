import os
import shutil
from repobot.common import plist, pmap
from jinja2 import Environment, FileSystemLoader, select_autoescape
import cherrypy


class PkgProvider(object):
    def __init__(self, db, repo, datadir):
        """
        Base package provider class
        """
        self.db = db
        self.repo = repo
        self.dir = datadir

    def render(self):
        """
        Respond to requests to browse the repo
        """
        raise NotImplementedError()

    def add_package(self, pkobj, fname, fobj, params):
        """
        Add a package to the repo
        """
        raise NotImplementedError()


class PyPiProvider(PkgProvider):
    def add_package(self, pkgobj, fname, fobj, params):
        if "files" not in pkgobj.data:
            pkgobj.data["files"] = plist()

        if fname in pkgobj.data["files"]:
            raise Exception("File {} already in package {}-{}".format(fname, pkgobj.name, pkgobj.version))

        pkgdir = os.path.join(self.dir, pkgobj.name)
        os.makedirs(pkgdir, exist_ok=True)
        # TODO handle duplicate files better
        pkgfilepath = os.path.join(pkgdir, fname)

        with open(pkgfilepath, "wb") as fdest:
            shutil.copyfileobj(fobj, fdest)

        pkgobj.data["files"].append(fname)

    def browse(self, args):
        tpl = Environment(loader=FileSystemLoader("templates"), autoescape=select_autoescape(['html', 'xml']))
        if len(args) == 0:  # repo root
            return tpl.get_template("pypi/root.html"). \
                render(reponame=self.repo.name,
                       packages=self.repo.packages.keys())
        elif len(args) == 1:  # single module dir
            files = []
            if args[0] not in self.repo.packages:
                raise cherrypy.HTTPError(404, 'Invalid package')
            for _, version in self.repo.packages[args[0]].items():
                files += version.data["files"]
            return tpl.get_template("pypi/project.html"). \
                render(reponame=self.repo.name,
                       modulename=args[0],
                       files=files)
        elif len(args) == 2:  # fetch file
            fpath = os.path.join(self.dir, args[0], args[1])
            return cherrypy.lib.static.serve_file(os.path.abspath(fpath), "application/octet-stream")


from subprocess import check_call, check_output, Popen, PIPE
from tempfile import NamedTemporaryFile, TemporaryDirectory
import json


class AptlyConfig(object):
    """
    Context manager providing an aptly config file
    """
    def __init__(self, rootdir):
        self.conf = {"rootDir": rootdir}  # , "gpgDisableSign": True, "gpgDisableVerify": True}
        self.file = None

    def __enter__(self):
        self.file = NamedTemporaryFile()
        with open(self.file.name, "w") as f:
            f.write(json.dumps(self.conf))
        return self.file.name

    def __exit__(self, *args):
        self.file.close()


class AptProvider(PkgProvider):
    def add_package(self, pkgobj, fname, fobj, params):
        # first package added sets the Distribution of the repo
        # subsequent package add MUST specify the same dist
        if "dist" not in self.repo.data:
            self.repo.data["dist"] = params["dist"]
        assert self.repo.data["dist"] == params["dist"]

        # Generate a GPG key to sign packages in this repo
        # TODO support passing keypath=... param to import existing keys and maybe other key generation options
        if not os.path.exists(self._gpg_dir):
            self._generate_gpg_key()

        if "files" not in pkgobj.data:
            pkgobj.data["files"] = plist()
        if fname in pkgobj.data["files"]:
            # raise Exception("File {} already in package {}-{}".format(fname, pkgobj.name, pkgobj.version))
            pass

        with AptlyConfig(self.dir) as conf:
            if not os.path.exists(os.path.join(self.dir, "db")):
                os.makedirs(self.dir, exist_ok=True)
                check_call(["aptly", "-config", conf, "repo", "create",
                            "-distribution", self.repo.data["dist"], "main"])  # TODO dist param
            # put the file somewhere for now
            with TemporaryDirectory() as tdir:
                tmppkgpath = os.path.join(tdir, fname)
                with open(tmppkgpath, "wb") as fdest:
                    shutil.copyfileobj(fobj, fdest)
                check_call(["aptly", "-config", conf, "repo", "add", "main", tmppkgpath])
            if not os.path.exists(os.path.join(self.dir, "public")):
                check_call(["aptly", "-config", conf, "publish", "repo", "main"],
                           env=self._env)
            else:
                check_call(["aptly", "-config", conf, "publish", "update",
                            "-force-overwrite", self.repo.data["dist"]],
                           env=self._env)

        # Make the public key available for clients
        self._export_pubkey()

        pkgobj.data["files"].append(fname)

        # TODO validate deb file name version against user passed version

    def browse(self, args):
        fpath = os.path.abspath(os.path.join(self.dir, "public", *args))
        if not os.path.exists(fpath):
            raise cherrypy.HTTPError(404)
        return cherrypy.lib.static.serve_file(fpath)

    def _generate_gpg_key(self):
        """
        Generate a GPG key for signing packages in this repo. Because only gpg2 supports unattended generation of
        passwordless keys we generate the key with gpg2 then export/import it into gpg1.
        """
        # Generate the key
        os.makedirs(self._gpg_dir)
        proc = Popen(["gpg", "--batch", "--gen-key"], stdin=PIPE, env=self._env)
        proc.stdin.write("""%no-protection
Key-Type: rsa
Key-Length: 1024
Subkey-Type: default
Subkey-Length: 1024
Name-Real: Apt Master
Name-Comment: Apt signing key
Name-Email: aptmaster@localhost
Expire-Date: 0
%commit""".encode("ascii"))
        proc.stdin.close()
        proc.wait()
        assert proc.returncode == 0

        # Export the private key
        keydata = check_output(["gpg", "--export-secret-key", "--armor", "aptmaster@localhost"], env=self._env)
        shutil.rmtree(self._gpg_dir)
        os.makedirs(self._gpg_dir)

        # Import the private key
        proc = Popen(["gpg1", "--import"], stdin=PIPE, env=self._env)
        proc.stdin.write(keydata)
        proc.stdin.close()
        proc.wait()
        assert proc.returncode == 0

    def _export_pubkey(self):
        keypath = os.path.join(self.dir, "public", "repo.key")
        if not os.path.exists(keypath):
            keydata = check_output(["gpg", "--export", "--armor", "aptmaster@localhost"], env=self._env)
            with open(keypath, "wb") as f:
                f.write(keydata)

    @property
    def _env(self):
        """
        Return env vars to be used for subprocesses of this module
        """
        print(os.environ["PATH"])
        return {"GNUPGHOME": self._gpg_dir,
                "PATH": os.environ["PATH"]}

    @property
    def _gpg_dir(self):
        return os.path.join(self.dir, "gpg")


providers = {"pypi": PyPiProvider,
             "apt": AptProvider}
