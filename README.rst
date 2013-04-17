MDS2
----

This is MDS2 , the second version of Mer delivery system.

The basic concept is simple: packages are stored in a git tree, and binary 
repos are built from them. Both are served over a REST API that mimics OBS API.

Requirements
============

* python 2.7
* git, gitdb, GitPython
* recent lxml (2.3 was tested)

Setup
=====

* Create a user called mer (system specific)
* Install requirements ::

    zypper install zlib-devel python-devel gcc gcc-g++ python-pip 
    pip install gitdb
    pip install GitPython

* As the mer user checkout mds2 from git ::

    cd /home/mer
    sudo -u mer git clone git@github.com:mer-tools/mds.git
    sudo -u mer git checkout mds2

* Create or edit the mappings.xml ::

    <mappings>
    <mapping project="Core" path="packages-git/mer/project-core" 
                            binaries="obs-repos/" 
                            packages-path="packages-git/" 
                            packages-upstream="rsync://releases.merproject.org/mer-releases/packages-git/" 
                            binaries-upstream="rsync://releases.merproject.org/mer-releases/obs-repos/"/>
    </mappings>

* Run the mds2 daemon ::

    sudo -u mer python2.7 tools/mds2.py 7000

* For systemd create a service file to run mds as a daemon. Note the value of the port, and uncomment the rsync proxy if needed ::

    [Unit]
    Description=Mer Delivery System
    After=multi-user.target
    
    [Service]
    User=mer
    Group=users
    WorkingDirectory=/home/mer/mds
    #Environment=RSYNC_PROXY=proxy:8080
    ExecStart=/usr/bin/python2.7 /home/mer/mds/tools/mds2.py 7000
    Restart=always
    
    [Install]
    WantedBy=multi-user.target

* Pull the packages and repos using the REST API. It will take a long time with no output, just be patient ::

    curl http://127.0.0.1:7000/update/packages/Core
    curl http://127.0.0.1:7000/update/repo/Core/0.20130314.0.2

