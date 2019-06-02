
docker-artifact
===============

Software repository server

Artifactd provides an HTTP API for repository management. Supported repository formats are:

- Python (Pypi)
- Apt
- Generic tarball


Quickstart
----------

* Pull or build the image
* `docker run -it --rm -e 'DATABASE_URL=mysql+pymysql://...' -e 'S3_URL=http://...' -p 8080:8080 artifact`

The in-container webserver will listen on port 8080 by default. Database url is passed directly to sqlalchemy, but only
mysql is tested. S3_URL is in the form of `https?://keyname:keysecret@endpoint_url/bucket_name`. Amazon S3 is supported
but minio is the preferred backend.


Examples
--------

Upload python package:

`curl -vv -F 'f=@pyircbot-4.0.0.post3-py3.5.egg' 'http://localhost:8080/addpkg?provider=pypi&reponame=reponame&name=pyircbot&version=4.0.0'`


Install python packages:

`pip3 install -i http://host/repo/pypi/reponame/ --trusted-host host <packages>`


Upload apt package:

`curl -vv -F 'f=@python3_3.6.7-1~18.04_amd64.deb' 'http://host/addpkg?provider=apt&reponame=reponame&name=python3&version=3.6.7-1~18.04&dist=bionic'`


Install apt packages:

```
wget -qO- http://host/repo/apt/reponame/pubkey | apt-key add - && \
echo "deb http://host/repo/apt/reponame bionic main" | tee -a /etc/apt/sources.list && \
apt-get update
```


CLI
---

Building on the rest endpoints above:

Apt:

* `rpcli -s http://localhost:8080 upload -y apt -f extpython-python3.6_3.6.7_amd64.deb_trusty -r reponame -p extpython-python3.6 -i 3.6.7 -a dist=trusty`

Python:

* `rpcli -s http://localhost:8080 upload -y pypi -f tensorflow-2.0.0a0-cp37-cp37m-manylinux1_x86_64.whl -r reponame -p tensorflow -i 2.0.0a0`


Notes
-----

* Repos are created automatically when a package is added to them.
* Repo URLs are structured as: `/repo/<provider>/<name>`. URLs at and below this level are handled directly by
  the provider.
* In the apt provider, only binary-amd64 packages are supported. No source, binary-i386 or other groups
* In the apt provider, every repo has only one component, named "main"
* The apt provider will generate a gpg key per repo upon repo creation
* The repo contents can be browsed on the web
* This uses my fork of python-dpkg, from [here](https://git.davepedu.com/dave/python-dpkg), which is not automatically
  installed via `setup.py` due to pip limitations.
* The apt provider includes a convenience shell script:

```
apt-get update && \
apt-get install -y wget gnupg && \
wget -qO- http://host/repo/apt/reponame/dists/trusty/install | bash -x /dev/stdin
```

Todo
----

* CLI tool (for adding packages only)
* 'Simple' cli tool (shell script fetchable from the server for adding packages)
* Centralize deleting packages
* Rpm Support
* Auth
* Support using existing GPG keys for apt
* Nicer UI
* Json API
* deb need to be able to slice package in repos by: component (arbitrary names), index (binary-amd64, binary-i386, source)
* can already slice packages by: repo, dist
* Move copysha256 somewhere generic
* Have the server dictate the S3 root path to the provider plugins
* Assert that submitted package names and file names are sane
* Assert that submitted files smell like the type of file that is intended
* Global & per-provider options:
    * option to block overwriting
* Standardize what is returned from provider's web_addpkg
* Standardize some fields of provider's schema (name, version)
* Delete repos if empty (with option to disable per provider)
* Centralize the jinja template environment
    * need a way for providers to register jinja filters though
