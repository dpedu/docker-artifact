docker-artifact
===============

Software repository server

Artifactd provides an HTTP API for repository management. Currently, Python and Apt repositories are supported.


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

`curl -vv -F 'f=@pyircbot-4.0.0.post3-py3.5.egg' 'http://localhost:8080/addpkg?provider=pypi&reponame=main&name=pyircbot&version=4.0.0'`


Install python packages:

`pip3 install -i http://host/repo/pypi/main/ --trusted-host host <packages>`


Upload apt package:

`curl -vv -F 'f=@python3_3.6.7-1~18.04_amd64.deb' 'http://host/addpkg?provider=apt&reponame=main&name=python3&version=3.6.7-1~18.04&dist=bionic'`


Install apt packages:

```
wget -qO- http://host/repo/apt/main/pubkey | apt-key add - && \
echo "deb http://host/repo/apt/main bionic main" | tee -a /etc/apt/sources.list && \
apt-get update
```


CLI
---

Building on the rest endpoints above:

Apt:

* `rpcli -s http://localhost:8080 upload -y apt -f extpython-python3.6_3.6.7_amd64.deb_trusty -r exttest -p extpython-python3.6 -i 3.6.7 -a dist=trusty`

Python:

* `rpcli -s http://localhost:8080 upload -y pypi -f tensorflow-2.0.0a0-cp37-cp37m-manylinux1_x86_64.whl -r ttest2 -p tensorflow -i 2.0.0a0`


Notes
-----

* Repos are created automatically when a package is added to them.
* Repo URLs are structured as: `/repo/<provider>/<name>`. URLs at and below this level are handled directly by
  the provider.
* In the apt provider, only binary-amd64 packages are supported. No source, binary-386 or other groups
* In the apt provider, every repo has only one component, named "main"
* The apt provider will generate a gpg key per repo upon repo creation
* The repo contents can be browsed on the web
* This uses my fork of python-dpkg, from [here](https://git.davepedu.com/dave/python-dpkg), which is not automatically
  installed via `setup.py` due to pip limitations.


Todo
----

* Auth
* Delete packages
* Support using existing GPG keys
* Nicer UI
